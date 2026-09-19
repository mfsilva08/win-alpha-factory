"""SPEC-fase-2 §2.1, §2.5, §2.6: modelo de custo, contrato do motor e o selo."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Iterator
from datetime import time, timedelta
from pathlib import Path

import pytest

from src.backtest.api_engine import ApiBacktestEngine, ApiEngineConfig
from src.backtest.costs import load_costs
from src.backtest.engine import BacktestRequest, CvConfig, TradeRules
from src.backtest.errors import (
    BacktestCrashed,
    CostConfigError,
    DuplicateTrial,
    EngineError,
    EngineNotConfigured,
    InvalidOutcome,
)
from src.backtest.metrics import BacktestOutcome, FoldMetrics
from src.backtest.runner import BacktestConfig, SealedResult, run
from src.dsl.canonical import exact_signature, serialize, structural_signature
from src.dsl.errors import DslTypeError
from src.dsl.parser import parse
from src.ledger.ledger import Kind, Ledger, LedgerRecord, sha256_hex

ROOT = Path(__file__).resolve().parents[1]
DATA = sha256_hex(b"dataset")
H001 = parse(
    'mul(in_window("10:30","16:30"), sub(zscore(ret(ref("ES"),2),55),'
    " zscore(ret(close(),2),55)))"
)

# ---------------------------------------------------------------- custos


def test_custos_do_config_reproduzem_o_template() -> None:
    c = load_costs(ROOT / "config" / "costs.yaml")
    assert c.round_trip_points() == 10
    assert c.fees_brl(2) == pytest.approx(1.00)


def test_custos_escalonados_arredondam_slippage_para_cima() -> None:
    c = load_costs(ROOT / "config" / "costs.yaml")
    c15 = c.scaled(1.5)
    assert c15.slippage_ticks == 2 and c15.exchange_fee == pytest.approx(0.375)
    assert c.scaled(2.0).fees_brl(1) == pytest.approx(1.00)


@pytest.mark.parametrize(
    ("content", "msg"),
    [
        ("point_value: 0.2\n", "ausentes"),
        ("- 1\n- 2\n", "mapeamento"),
    ],
)
def test_custos_invalidos(tmp_path: Path, content: str, msg: str) -> None:
    p = tmp_path / "c.yaml"
    p.write_text(content, encoding="utf-8")
    with pytest.raises(CostConfigError, match=msg):
        load_costs(p)


def test_custos_rejeitam_valor_negativo_e_campo_extra(tmp_path: Path) -> None:
    base = (ROOT / "config" / "costs.yaml").read_text(encoding="utf-8")
    p = tmp_path / "c.yaml"
    p.write_text(base.replace("exchange_fee:         0.25", "exchange_fee: -1"), encoding="utf-8")
    with pytest.raises(CostConfigError, match="negativo"):
        load_costs(p)
    p.write_text(base + "\ncorretagem_extra: 1\n", encoding="utf-8")
    with pytest.raises(CostConfigError, match="desconhecidos"):
        load_costs(p)


# ---------------------------------------------------------------- motor falso


def fold(i: int, **over: object) -> FoldMetrics:
    f = FoldMetrics(
        path_id=i, n_trades=40, gross_pnl_points=500.0, cost_points=400.0,
        net_pnl_points=100.0, sharpe=0.8, max_dd_points=300.0, hit_rate=0.51,
        avg_holding=timedelta(minutes=4), max_day_share=0.1, skew=-0.2, kurtosis=4.0,
        active_days=30, rejected_orders=2,
    )
    return dataclasses.replace(f, **over)  # type: ignore[arg-type]


class FakeEngine:
    """Simula a API: devolve o que ``respond`` mandar, e registra as chamadas."""

    def __init__(self, respond: Callable[[BacktestRequest], BacktestOutcome]) -> None:
        self.respond = respond
        self.calls: list[BacktestRequest] = []

    def evaluate(self, req: BacktestRequest) -> BacktestOutcome:
        self.calls.append(req)
        return self.respond(req)


def ok(req: BacktestRequest) -> BacktestOutcome:
    return BacktestOutcome(req.test_id, req.data_hash, tuple(fold(i) for i in range(28)))


def raises(exc: BaseException) -> Callable[[BacktestRequest], BacktestOutcome]:
    def f(_req: BacktestRequest) -> BacktestOutcome:
        raise exc
    return f


CFG = BacktestConfig(
    rules=TradeRules(
        threshold=1.5, contracts=2, max_holding=timedelta(minutes=30), stop_points=None,
        target_points=None, cutoff=time(17, 50), session=(time(10, 30), time(16, 30)),
    ),
    costs=load_costs(ROOT / "config" / "costs.yaml"),
    cv=CvConfig(n_groups=8, k_test=2, label_horizon=timedelta(minutes=3), embargo_mult=3.0),
)


@pytest.fixture
def led(tmp_path: Path) -> Iterator[Ledger]:
    with Ledger(tmp_path / "l.sqlite") as lg:
        lg.genesis(DATA, sha256_hex(b"p"), sha256_hex(b"c"))
        yield lg


# ---------------------------------------------------------------- o selo


def test_sucesso_grava_e_devolve(led: Ledger) -> None:
    eng = FakeEngine(ok)
    res = run(H001, "h001", "t0001", CFG, led, eng)
    assert len(res.metrics) == 28
    assert led.count() == 1
    rec = led.records(Kind.BACKTEST)[0]
    assert not rec.crashed
    assert rec.ast_hash == exact_signature(H001)
    assert rec.structural_sig == structural_signature(H001)
    assert rec.hypothesis_id == "h001" and rec.trial_id == "t0001"


def test_requisicao_carrega_o_que_o_motor_precisa(led: Ledger) -> None:
    eng = FakeEngine(ok)
    run(H001, "h001", "t0001", CFG, led, eng)
    (req,) = eng.calls
    assert req.test_id == "t0001"
    assert req.data_hash == DATA
    assert req.formula == serialize(H001)
    assert req.costs == CFG.costs and req.rules == CFG.rules and req.cv.n_splits() == 28


@pytest.mark.parametrize(
    "exc", [EngineError("timeout"), ConnectionError("rede"), RuntimeError("bug no adaptador")]
)
def test_falha_do_motor_grava_crashed_e_levanta(led: Ledger, exc: Exception) -> None:
    with pytest.raises(BacktestCrashed):
        run(H001, "h001", "t0001", CFG, led, FakeEngine(raises(exc)))
    assert led.count() == 1
    assert led.records(Kind.BACKTEST)[0].crashed


def test_interrupcao_tambem_grava(led: Ledger) -> None:
    with pytest.raises(KeyboardInterrupt):
        run(H001, "h001", "t0001", CFG, led, FakeEngine(raises(KeyboardInterrupt())))
    assert led.count() == 1 and led.records(Kind.BACKTEST)[0].crashed


def _bad(**change: object) -> Callable[[BacktestRequest], BacktestOutcome]:
    def f(req: BacktestRequest) -> BacktestOutcome:
        return dataclasses.replace(ok(req), **change)  # type: ignore[arg-type]
    return f


@pytest.mark.parametrize(
    "respond",
    [
        _bad(folds=tuple(fold(i) for i in range(7))),  # partições faltando
        _bad(folds=tuple(fold(0) for _ in range(28))),  # path_id repetido
        _bad(folds=(fold(0, sharpe=float("nan")), *(fold(i) for i in range(1, 28)))),
        _bad(folds=(fold(0, net_pnl_points=999.0), *(fold(i) for i in range(1, 28)))),
        _bad(folds=(fold(0, hit_rate=1.5), *(fold(i) for i in range(1, 28)))),
        _bad(data_hash=sha256_hex(b"outro dataset")),
        _bad(test_id="t9999"),
    ],
)
def test_resposta_invalida_vira_crashed(
    led: Ledger, respond: Callable[[BacktestRequest], BacktestOutcome]
) -> None:
    with pytest.raises(BacktestCrashed) as e:
        run(H001, "h001", "t0001", CFG, led, FakeEngine(respond))
    assert isinstance(e.value.__cause__, InvalidOutcome)
    assert led.count() == 1 and led.records(Kind.BACKTEST)[0].crashed


def test_sem_registro_nao_ha_resultado(led: Ledger) -> None:
    """Se o livro-razão recusar a escrita, o resultado não pode sair."""

    class BrokenLedger(Ledger):
        def append(self, rec: LedgerRecord) -> str:
            raise OSError("disco cheio")

    broken = BrokenLedger.__new__(BrokenLedger)
    broken.__dict__.update(led.__dict__)
    with pytest.raises(OSError, match="disco cheio"):
        run(H001, "h001", "t0001", CFG, broken, FakeEngine(ok))


def test_ast_malformada_nao_consome_tentativa(led: Ledger) -> None:
    eng = FakeEngine(ok)
    with pytest.raises(DslTypeError):
        run(parse('sub(close(), ref("ES"))'), "h001", "t0001", CFG, led, eng)
    with pytest.raises(DslTypeError):
        run(parse("add(close(), volume())"), "h001", "t0002", CFG, led, eng)
    assert led.count() == 0 and eng.calls == []


def test_trial_id_repetido_recusado_antes_do_motor(led: Ledger) -> None:
    eng = FakeEngine(ok)
    run(H001, "h001", "t0001", CFG, led, eng)
    with pytest.raises(DuplicateTrial):
        run(H001, "h001", "t0001", CFG, led, eng)
    assert led.count() == 1 and len(eng.calls) == 1


def test_count_cresce_uma_unidade_por_avaliacao(led: Ledger) -> None:
    for i in range(10):
        respond = ok if i % 3 else raises(EngineError("x"))
        try:
            run(H001, "h001", f"t{i:04d}", CFG, led, FakeEngine(respond))
        except BacktestCrashed:
            pass
        assert led.count() == i + 1
    assert led.verify_chain()


def test_sealed_result_so_tem_metricas() -> None:
    assert [f.name for f in dataclasses.fields(SealedResult)] == ["metrics"]


def test_api_sem_contrato_recusa_na_construcao() -> None:
    with pytest.raises(EngineNotConfigured):
        ApiBacktestEngine(ApiEngineConfig(base_url="http://localhost"))
