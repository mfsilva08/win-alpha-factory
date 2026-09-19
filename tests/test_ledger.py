"""SPEC-fase-1 §1.7: livro-razão append-only, encadeado por hash."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import src.ledger.ledger as ledger_module
from src.dsl.verdict import Verdict
from src.ledger.ledger import (
    ZERO_HASH,
    GenesisError,
    InvalidRecord,
    Kind,
    Ledger,
    LedgerRecord,
    ProjectMismatch,
    compute_row_hash,
    config_hash,
    sha256_hex,
)

DATA = sha256_hex(b"dataset")
PREREG = sha256_hex(b"gate-prereg.md")
CATALOG = sha256_hex(b"catalogo")
CONFIG = config_hash(PREREG, CATALOG)


class FakeClock:
    """Relógio determinístico: cada chamada avança um segundo."""

    def __init__(self) -> None:
        self.t = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        self.t += timedelta(seconds=1)
        return self.t


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "ledger.sqlite"


@pytest.fixture
def led(db: Path) -> Iterator[Ledger]:
    with Ledger(db, clock=FakeClock()) as lg:
        lg.genesis(DATA, PREREG, CATALOG)
        yield lg


def trial(i: int, *, crashed: bool = False, hyp: str = "h001",
          verdict: Verdict | None = None, kind: Kind = Kind.BACKTEST) -> LedgerRecord:
    return LedgerRecord(
        trial_id=f"t{i:05d}", kind=kind, data_hash=DATA, config_hash=CONFIG,
        hypothesis_id=hyp, ast_hash=sha256_hex(f"ast{i}".encode()),
        structural_sig=sha256_hex(f"sig{i}".encode()), verdict=verdict, crashed=crashed,
    )


def raw(db: Path) -> sqlite3.Connection:
    """Conexão direta, como a de alguém tentando adulterar o banco."""
    return sqlite3.connect(db, isolation_level=None)


# ---------------------------------------------------------------- testes obrigatórios


def test_cadeia_integra_apos_1000_appends(led: Ledger) -> None:
    for i in range(1000):
        led.append(trial(i, crashed=i % 7 == 0))
    assert led.count() == 1000
    assert led.verify_chain()


def test_adulteracao_por_sql_direto_e_detectada(led: Ledger, db: Path) -> None:
    for i in range(20):
        led.append(trial(i, verdict=Verdict.FAILED_GATE))
    assert led.verify_chain()
    c = raw(db)
    # os gatilhos barram o UPDATE ingênuo...
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        c.execute("UPDATE ledger SET verdict = 'ACCEPTED' WHERE seq = 10")
    # ...e quem derrubar os gatilhos ainda é pego pelo hash
    c.execute("DROP TRIGGER ledger_append_only_update")
    c.execute("UPDATE ledger SET verdict = 'ACCEPTED' WHERE seq = 10")
    c.close()
    assert not led.verify_chain()


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE ledger SET crashed = 0 WHERE seq = 5",
        "UPDATE ledger SET kind = 'kill' WHERE seq = 5",
        "UPDATE ledger SET structural_sig = 'x' WHERE seq = 5",
        "UPDATE ledger SET ts = '2020-01-01' WHERE seq = 5",
        "UPDATE ledger SET config_hash = 'x' WHERE seq = 1",
    ],
)
def test_toda_coluna_esta_no_hash(led: Ledger, db: Path, sql: str) -> None:
    for i in range(8):
        led.append(trial(i, crashed=True))
    c = raw(db)
    c.execute("DROP TRIGGER ledger_append_only_update")
    c.execute(sql)
    c.close()
    assert not led.verify_chain()


def test_apagar_linha_e_detectado(led: Ledger, db: Path) -> None:
    for i in range(10):
        led.append(trial(i))
    c = raw(db)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        c.execute("DELETE FROM ledger WHERE seq = 4")
    c.execute("DROP TRIGGER ledger_append_only_delete")
    c.execute("DELETE FROM ledger WHERE seq = 4")
    c.close()
    assert not led.verify_chain()


def test_count_inclui_crashed(led: Ledger) -> None:
    for i in range(5):
        led.append(trial(i, crashed=i in (1, 3)))
    assert led.count() == 5


def test_genesis_duas_vezes_levanta(led: Ledger) -> None:
    with pytest.raises(GenesisError):
        led.genesis(DATA, PREREG, CATALOG)


def test_nao_existe_delete_update_reset() -> None:
    for name in dir(Ledger):
        assert not re.search(r"delete|update|reset|remove|truncate|drop", name, re.IGNORECASE), name
    src = Path(ledger_module.__file__).read_text(encoding="utf-8")
    assert not re.search(r"\bDELETE\s+FROM\b|\bUPDATE\s+ledger\b|\bDROP\b", src, re.IGNORECASE)


# ---------------------------------------------------------------- gênesis e projeto


def test_append_sem_genesis(db: Path) -> None:
    with Ledger(db) as lg, pytest.raises(GenesisError):
        lg.append(trial(0))


def test_genesis_guarda_os_componentes(led: Ledger) -> None:
    g = led.genesis_info()
    assert (g.data_hash, g.prereg_sha256, g.catalog_sha256) == (DATA, PREREG, CATALOG)
    assert g.config_hash == CONFIG
    first = led.records()[0]
    assert first.seq == 1 and first.kind is Kind.GENESIS and first.prev_hash == ZERO_HASH


def test_genesis_via_append_recusado(led: Ledger) -> None:
    with pytest.raises(GenesisError):
        led.append(LedgerRecord("x", Kind.GENESIS, DATA, CONFIG))


def test_config_diferente_e_outro_projeto(led: Ledger) -> None:
    outro = config_hash(sha256_hex(b"prereg alterado"), CATALOG)
    rec = LedgerRecord("t", Kind.BACKTEST, DATA, outro, "h001", DATA, DATA)
    with pytest.raises(ProjectMismatch):
        led.append(rec)


def test_backtest_com_outro_dataset_recusado(led: Ledger) -> None:
    rec = LedgerRecord("t", Kind.BACKTEST, sha256_hex(b"outro"), CONFIG, "h001", DATA, DATA)
    with pytest.raises(ProjectMismatch):
        led.append(rec)


def test_holdout_usa_dataset_proprio_e_conta(led: Ledger) -> None:
    rec = LedgerRecord("t", Kind.HOLDOUT, sha256_hex(b"holdout"), CONFIG, "h001", DATA, DATA)
    led.append(rec)
    assert led.count() == 1 and led.count(Kind.HOLDOUT) == 1


def test_hash_invalido_recusado(db: Path) -> None:
    with Ledger(db) as lg, pytest.raises(InvalidRecord):
        lg.genesis("abc", PREREG, CATALOG)


# ---------------------------------------------------------------- kill e daily


def test_kill_e_daily_nao_contam_como_tentativa(led: Ledger) -> None:
    led.append(trial(0))
    tele = sha256_hex(b"win_20260915.csv")
    led.append(LedgerRecord("d1", Kind.DAILY, tele, CONFIG, "h001",
                            payload={"day": "2026-09-15", "trades": 3, "slippage_ticks_avg": 1.2}))
    led.append(LedgerRecord("k1", Kind.KILL, tele, CONFIG, "h001",
                            payload={"day": "2026-09-15", "metric": "lag_ms", "value": 18000.0}))
    assert led.count() == 1
    assert led.count(Kind.DAILY) == 1 and led.count(Kind.KILL) == 1
    assert led.records(Kind.KILL)[0].payload == {
        "day": "2026-09-15", "metric": "lag_ms", "value": 18000.0}
    assert led.verify_chain()


@pytest.mark.parametrize(
    "rec",
    [
        # backtest sem ast_hash
        LedgerRecord("t", Kind.BACKTEST, DATA, CONFIG, "h001"),
        # backtest com payload: métricas não moram no livro-razão por ora
        LedgerRecord("t", Kind.BACKTEST, DATA, CONFIG, "h001", DATA, DATA,
                     payload={"sharpe": 2.1}),
        # kill sem hipótese
        LedgerRecord("k", Kind.KILL, DATA, CONFIG, payload={"value": 1.0}),
        # daily marcado como crashed
        LedgerRecord("d", Kind.DAILY, DATA, CONFIG, "h001", crashed=True),
        # payload com NaN não tem JSON canônico
        LedgerRecord("d", Kind.DAILY, DATA, CONFIG, "h001", payload={"v": float("nan")}),
        LedgerRecord("", Kind.BACKTEST, DATA, CONFIG, "h001", DATA, DATA),
    ],
)
def test_registros_invalidos(led: Ledger, rec: LedgerRecord) -> None:
    before = led.head()
    with pytest.raises(InvalidRecord):
        led.append(rec)
    assert led.head() == before  # nada foi gravado


# ---------------------------------------------------------------- leitura e persistência


def test_seen_structural_e_verdicts_for(led: Ledger) -> None:
    led.append(trial(1, hyp="h001", verdict=Verdict.COST_DOMINATED))
    led.append(trial(2, hyp="h001"))
    led.append(trial(3, hyp="h002", verdict=Verdict.ACCEPTED))
    led.append(trial(4, hyp="h001", verdict=Verdict.FAILED_GATE))
    assert led.seen_structural(sha256_hex(b"sig2"))
    assert not led.seen_structural(sha256_hex(b"sig9"))
    assert led.verdicts_for("h001") == [Verdict.COST_DOMINATED, Verdict.FAILED_GATE]


def test_reabrir_continua_a_cadeia(db: Path) -> None:
    with Ledger(db, clock=FakeClock()) as lg:
        lg.genesis(DATA, PREREG, CATALOG)
        lg.append(trial(1))
        head = lg.head()
    with Ledger(db, clock=FakeClock()) as lg:
        assert lg.head() == head
        lg.append(trial(2))
        assert lg.records()[-1].prev_hash == head
        assert lg.verify_chain() and lg.count() == 2


def test_row_hash_deterministico(led: Ledger) -> None:
    led.append(trial(1))
    r = led.records()[-1]
    row = {"seq": r.seq, "trial_id": r.trial_id, "kind": r.kind.value,
           "hypothesis_id": r.hypothesis_id, "ast_hash": r.ast_hash,
           "structural_sig": r.structural_sig, "data_hash": r.data_hash,
           "config_hash": r.config_hash, "verdict": None, "crashed": 0, "payload": None,
           "prev_hash": r.prev_hash, "ts": r.ts}
    assert compute_row_hash(row) == r.row_hash
