"""O livro-razão: append-only, encadeado por hash, escrita serializada.

Regras (SPEC-fase-1 §1.7, R4, R7, ADR-010):

- Não existe ``delete``, ``update`` nem ``reset``. O schema ainda recusa UPDATE e
  DELETE por gatilho, como segunda linha de defesa.
- ``row_hash`` cobre **todas** as colunas, inclusive ``seq``, ``kind``,
  ``verdict``, ``crashed`` e ``payload``: qualquer alteração direta no banco
  quebra ``verify_chain()``.
- Uma única conexão, ``journal_mode=WAL``, ``synchronous=FULL`` e uma transação
  ``BEGIN IMMEDIATE`` por append. O ``prev_hash`` é lido dentro da transação.
- ``count()`` conta tentativas: ``backtest`` e ``holdout``, inclusive ``crashed``.
  ``kill``, ``daily``, ``hypothesis`` e ``verdict`` nunca contam.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import Any, Self

from src.dsl.verdict import Verdict

SCHEMA = Path(__file__).with_name("schema.sql")
ZERO_HASH = "0" * 64

JsonValue = Any


class LedgerError(Exception):
    """Base dos erros do livro-razão."""


class GenesisError(LedgerError):
    """Gênesis ausente, repetido ou inconsistente."""


class ProjectMismatch(LedgerError):
    """Registro com ``config_hash`` (ou ``data_hash``) diferente do gênesis."""


class InvalidRecord(LedgerError):
    """Registro que viola as regras do seu ``kind``."""


class Kind(StrEnum):
    GENESIS = "genesis"
    BACKTEST = "backtest"
    HOLDOUT = "holdout"
    KILL = "kill"
    DAILY = "daily"
    HYPOTHESIS = "hypothesis"  # hipótese aceita pela validação determinística
    VERDICT = "verdict"  # o veredito categórico de cada tentativa de fórmula


TRIAL_KINDS: frozenset[Kind] = frozenset({Kind.BACKTEST, Kind.HOLDOUT})
_PAYLOAD_KINDS: frozenset[Kind] = frozenset(
    {Kind.KILL, Kind.DAILY, Kind.HYPOTHESIS, Kind.VERDICT}
)


def sha256_hex(data: bytes) -> str:
    return sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    """sha256 dos bytes exatos do arquivo (sem normalizar quebras de linha)."""
    return sha256_hex(path.read_bytes())


def config_hash(prereg_sha256: str, catalog_sha256: str) -> str:
    """``config_hash`` do gênesis: sha256 da concatenação dos dois hashes hex."""
    return sha256_hex((prereg_sha256 + catalog_sha256).encode("ascii"))


def canonical_json(payload: Mapping[str, JsonValue]) -> str:
    try:
        return json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        )
    except (TypeError, ValueError) as e:
        raise InvalidRecord(f"payload não serializável em JSON canônico: {e}") from None


@dataclass(frozen=True)
class LedgerRecord:
    """O que um chamador entrega a ``append``. ``seq``, hashes e ``ts`` são do livro."""

    trial_id: str
    kind: Kind
    data_hash: str
    config_hash: str
    hypothesis_id: str | None = None
    ast_hash: str | None = None
    structural_sig: str | None = None
    verdict: Verdict | None = None
    crashed: bool = False
    payload: Mapping[str, JsonValue] | None = None


@dataclass(frozen=True)
class StoredRecord:
    seq: int
    trial_id: str
    kind: Kind
    hypothesis_id: str | None
    ast_hash: str | None
    structural_sig: str | None
    data_hash: str
    config_hash: str
    verdict: Verdict | None
    crashed: bool
    payload: Mapping[str, JsonValue] | None
    prev_hash: str
    row_hash: str
    ts: str


@dataclass(frozen=True)
class GenesisInfo:
    data_hash: str
    config_hash: str
    prereg_sha256: str
    catalog_sha256: str
    ts: str = field(default="")


_COLUMNS = (
    "seq", "trial_id", "kind", "hypothesis_id", "ast_hash", "structural_sig",
    "data_hash", "config_hash", "verdict", "crashed", "payload", "prev_hash", "ts",
)


def compute_row_hash(row: Mapping[str, object]) -> str:
    """sha256 do JSON canônico de todas as colunas, exceto o próprio ``row_hash``."""
    body = {c: row[c] for c in _COLUMNS}
    return sha256_hex(canonical_json(body).encode("utf-8"))


def _utc_now() -> datetime:
    return datetime.now(UTC)


class Ledger:
    """Livro-razão sobre um arquivo SQLite. Uma instância, uma conexão."""

    def __init__(self, path: Path | str, clock: Callable[[], datetime] = _utc_now) -> None:
        self._path = Path(path)
        self._clock = clock
        self._conn = sqlite3.connect(self._path, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.executescript(SCHEMA.read_text(encoding="utf-8"))

    # ------------------------------------------------------------ ciclo de vida

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # ------------------------------------------------------------ escrita

    def genesis(self, data_hash: str, prereg_sha256: str, catalog_sha256: str) -> str:
        """Grava o registro zero. Levanta ``GenesisError`` se já existir."""
        for name, h in (("data_hash", data_hash), ("prereg", prereg_sha256),
                        ("catálogo", catalog_sha256)):
            _require_hex(name, h)
        cfg = config_hash(prereg_sha256, catalog_sha256)
        payload = {"prereg_sha256": prereg_sha256, "catalog_sha256": catalog_sha256}
        row: dict[str, object] = {
            "trial_id": "genesis",
            "kind": Kind.GENESIS.value,
            "hypothesis_id": None,
            "ast_hash": None,
            "structural_sig": None,
            "data_hash": data_hash,
            "config_hash": cfg,
            "verdict": None,
            "crashed": 0,
            "payload": canonical_json(payload),
        }
        return self._insert(row, genesis=True)

    def append(self, rec: LedgerRecord) -> str:
        """Grava um registro e devolve seu ``row_hash``."""
        self._check(rec)
        row: dict[str, object] = {
            "trial_id": rec.trial_id,
            "kind": rec.kind.value,
            "hypothesis_id": rec.hypothesis_id,
            "ast_hash": rec.ast_hash,
            "structural_sig": rec.structural_sig,
            "data_hash": rec.data_hash,
            "config_hash": rec.config_hash,
            "verdict": rec.verdict.value if rec.verdict is not None else None,
            "crashed": int(rec.crashed),
            "payload": canonical_json(rec.payload) if rec.payload is not None else None,
        }
        return self._insert(row, genesis=False)

    def _check(self, rec: LedgerRecord) -> None:
        if rec.kind is Kind.GENESIS:
            raise GenesisError("use genesis() para o registro zero")
        if not rec.trial_id:
            raise InvalidRecord("trial_id vazio")
        g = self.genesis_info()
        _require_hex("data_hash", rec.data_hash)
        if rec.config_hash != g.config_hash:
            raise ProjectMismatch("config_hash diferente do gênesis: é outro projeto")
        if rec.kind is Kind.BACKTEST and rec.data_hash != g.data_hash:
            raise ProjectMismatch("data_hash do backtest diferente do gênesis")
        if rec.kind in TRIAL_KINDS:
            if rec.ast_hash is None or rec.structural_sig is None:
                raise InvalidRecord(f"{rec.kind.value} exige ast_hash e structural_sig")
            if rec.payload is not None:
                raise InvalidRecord(f"{rec.kind.value} não carrega payload")
        else:
            if rec.hypothesis_id is None:
                raise InvalidRecord(f"{rec.kind.value} exige hypothesis_id")
            if rec.crashed:
                raise InvalidRecord(f"{rec.kind.value} não pode ser crashed")
            if rec.kind is Kind.VERDICT and rec.verdict is None:
                raise InvalidRecord("verdict exige o veredito")
            if rec.kind is Kind.HYPOTHESIS and rec.payload is None:
                raise InvalidRecord("hypothesis exige o payload com a hipótese")
        if rec.payload is not None and rec.kind not in _PAYLOAD_KINDS:
            raise InvalidRecord(f"{rec.kind.value} não carrega payload")

    def _insert(self, row: dict[str, object], genesis: bool) -> str:
        c = self._conn
        c.execute("BEGIN IMMEDIATE")
        try:
            last = c.execute(
                "SELECT seq, row_hash FROM ledger ORDER BY seq DESC LIMIT 1"
            ).fetchone()
            if genesis and last is not None:
                raise GenesisError("o gênesis já existe")
            if not genesis and last is None:
                raise GenesisError("livro-razão sem gênesis")
            row["seq"] = 1 if last is None else int(last["seq"]) + 1
            row["prev_hash"] = ZERO_HASH if last is None else str(last["row_hash"])
            row["ts"] = self._clock().astimezone(UTC).isoformat(timespec="microseconds")
            row["row_hash"] = compute_row_hash(row)
            cols = (*_COLUMNS, "row_hash")
            c.execute(
                f"INSERT INTO ledger ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                tuple(row[k] for k in cols),
            )
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise
        return str(row["row_hash"])

    # ------------------------------------------------------------ leitura

    def genesis_info(self) -> GenesisInfo:
        r = self._conn.execute(
            "SELECT data_hash, config_hash, payload, ts FROM ledger WHERE kind = 'genesis'"
        ).fetchone()
        if r is None:
            raise GenesisError("livro-razão sem gênesis")
        p = json.loads(r["payload"])
        return GenesisInfo(r["data_hash"], r["config_hash"], p["prereg_sha256"],
                           p["catalog_sha256"], r["ts"])

    def count(self, kind: Kind | None = None) -> int:
        """Sem argumento: tentativas (``backtest`` + ``holdout``, inclusive crashed)."""
        kinds = sorted(k.value for k in TRIAL_KINDS) if kind is None else [kind.value]
        q = f"SELECT COUNT(*) FROM ledger WHERE kind IN ({', '.join('?' * len(kinds))})"
        return int(self._conn.execute(q, kinds).fetchone()[0])

    def head(self) -> str:
        r = self._conn.execute("SELECT row_hash FROM ledger ORDER BY seq DESC LIMIT 1").fetchone()
        return ZERO_HASH if r is None else str(r["row_hash"])

    def verify_chain(self) -> bool:
        prev = ZERO_HASH
        expected_seq = 1
        for r in self._conn.execute("SELECT * FROM ledger ORDER BY seq"):
            row = {k: r[k] for k in r.keys()}  # noqa: SIM118 sqlite3.Row itera valores
            if row["seq"] != expected_seq or row["prev_hash"] != prev:
                return False
            if (row["kind"] == Kind.GENESIS.value) != (expected_seq == 1):
                return False
            if compute_row_hash(row) != row["row_hash"]:
                return False
            prev = row["row_hash"]
            expected_seq += 1
        return True

    def has_trial(self, trial_id: str) -> bool:
        r = self._conn.execute(
            "SELECT 1 FROM ledger WHERE trial_id = ? AND kind IN ('backtest', 'holdout') LIMIT 1",
            (trial_id,),
        ).fetchone()
        return r is not None

    def seen_structural(self, sig: str) -> bool:
        r = self._conn.execute(
            "SELECT 1 FROM ledger WHERE structural_sig = ? LIMIT 1", (sig,)
        ).fetchone()
        return r is not None

    def verdicts_for(self, hypothesis_id: str) -> list[Verdict]:
        rows = self._conn.execute(
            "SELECT verdict FROM ledger WHERE hypothesis_id = ? AND verdict IS NOT NULL "
            "ORDER BY seq",
            (hypothesis_id,),
        )
        return [Verdict(r["verdict"]) for r in rows]

    def records(self, kind: Kind | None = None) -> list[StoredRecord]:
        """Leitura completa, para a zona de verificação e o coletor.

        Nunca exponha o resultado à zona de pesquisa (R1).
        """
        if kind is None:
            rows = self._conn.execute("SELECT * FROM ledger ORDER BY seq")
        else:
            rows = self._conn.execute(
                "SELECT * FROM ledger WHERE kind = ? ORDER BY seq", (kind.value,)
            )
        return [_stored(r) for r in rows]


def _stored(r: sqlite3.Row) -> StoredRecord:
    return StoredRecord(
        seq=int(r["seq"]),
        trial_id=r["trial_id"],
        kind=Kind(r["kind"]),
        hypothesis_id=r["hypothesis_id"],
        ast_hash=r["ast_hash"],
        structural_sig=r["structural_sig"],
        data_hash=r["data_hash"],
        config_hash=r["config_hash"],
        verdict=Verdict(r["verdict"]) if r["verdict"] is not None else None,
        crashed=bool(r["crashed"]),
        payload=json.loads(r["payload"]) if r["payload"] is not None else None,
        prev_hash=r["prev_hash"],
        row_hash=r["row_hash"],
        ts=r["ts"],
    )


def _require_hex(name: str, h: str) -> None:
    if len(h) != 64 or any(ch not in "0123456789abcdef" for ch in h):
        raise InvalidRecord(f"{name} deve ser sha256 em hex minúsculo")
