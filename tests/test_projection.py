"""O teste mais importante do projeto (SPEC-fase-3 §3.2, R1).

Roda sobre o **payload final enviado à API**, não sobre o objeto de estado.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import timedelta
from pathlib import Path

import numpy as np
import pytest

from src.agents.schemas import load_hypothesis
from src.backtest.metrics import FoldMetrics
from src.dsl.parser import parse
from src.dsl.verdict import Verdict
from src.gate.config import GateConfig
from src.orchestrator.budgets import Budget
from src.orchestrator.projection import (
    ResearchView,
    VerificationView,
    assert_no_metrics,
    project,
    research_payload,
    serialize_for_api,
)
from src.orchestrator.state import Metrics, TrialState, Zone

ROOT = Path(__file__).resolve().parents[1]
H001 = load_hypothesis(ROOT / "docs" / "exemplos" / "h001.yaml")
FORMULA = parse(
    'mul(in_window("10:30","16:30"), sub(zscore(ret(ref("ES"),2),55),'
    " zscore(ret(close(),2),55)))"
)
GATE = GateConfig(400, 30, 120, 0.25, 0.30, 0.20, 0.95, 3000, 8, 2, 3.0)


def fold(i: int, sharpe: float = 2.3141, net: float = 88128.0) -> FoldMetrics:
    return FoldMetrics(
        path_id=i, n_trades=731, gross_pnl_points=net + 4170.0, cost_points=4170.0,
        net_pnl_points=net, sharpe=sharpe, max_dd_points=6435.5, hit_rate=0.5437,
        avg_holding=timedelta(minutes=7), max_day_share=0.0871, skew=-0.4432,
        kurtosis=5.7719, active_days=233, rejected_orders=17,
    )


def state(**over: object) -> TrialState:
    st = TrialState(
        trial_id="t0001", hypothesis=H001, budget=Budget(),
        constraints=H001.dsl_constraints, formula_ast=FORMULA,
        verdict=Verdict.FAILED_GATE, attempt=3,
        metrics=Metrics(tuple(fold(i) for i in range(28))),
    )
    for k, v in over.items():
        setattr(st, k, v)
    return st


# ---------------------------------------------------------------- o teste


def test_pesquisa_nunca_ve_metricas() -> None:
    st = state()
    payload = serialize_for_api(project(st, Zone.RESEARCH))
    assert "sharpe" not in payload
    assert "2.3141" not in payload
    assert "88128" not in payload


# ---------------------------------------------------------------- estrutura


def test_research_view_nao_tem_campo_de_metricas() -> None:
    names = {f.name for f in dataclasses.fields(ResearchView)}
    forbidden = {f.name for f in dataclasses.fields(FoldMetrics)} | {"metrics", "n_trials", "cfg"}
    assert names.isdisjoint(forbidden)
    assert names == {"hypothesis", "formula_ast", "verdict", "detail", "attempt",
                     "blocked_sigs", "constraints"}


def test_nenhum_numero_de_metrica_no_payload() -> None:
    payload = research_payload(state())
    for f in fold(0).__dict__.values():
        if isinstance(f, float) and not f.is_integer():
            assert repr(f) not in payload
            assert f"{f:.2f}" not in payload


@pytest.mark.parametrize("seed", range(20))
def test_metricas_aleatorias_nunca_aparecem(seed: int) -> None:
    rng = np.random.default_rng(seed)
    folds = tuple(
        dataclasses.replace(fold(i), sharpe=float(rng.normal(0, 2)),
                            net_pnl_points=float(rng.normal(0, 1e5)),
                            gross_pnl_points=0.0, cost_points=0.0)
        for i in range(28)
    )
    st = state(metrics=Metrics(folds))
    payload = research_payload(st)
    for f in folds:
        assert repr(f.sharpe) not in payload
        assert repr(f.net_pnl_points) not in payload


def test_verificacao_ve_as_metricas() -> None:
    v = project(state(), Zone.VERIFICATION, n_trials=1234, cfg=GATE)
    assert isinstance(v, VerificationView)
    assert v.metrics is not None and v.metrics.folds[0].sharpe == 2.3141
    assert v.n_trials == 1234


def test_verificacao_nunca_e_serializada_para_api() -> None:
    v = project(state(), Zone.VERIFICATION, n_trials=1, cfg=GATE)
    with pytest.raises(TypeError):
        serialize_for_api(v)  # type: ignore[arg-type]


# ---------------------------------------------------------------- detalhe


@pytest.mark.parametrize(
    "verdict",
    [Verdict.FAILED_GATE, Verdict.COST_DOMINATED, Verdict.UNSTABLE_ACROSS_FOLDS,
     Verdict.INSUFFICIENT_SAMPLE, Verdict.TOO_COMPLEX, Verdict.REDUNDANT, Verdict.ACCEPTED],
)
def test_detalhe_suprimido_fora_das_duas_excecoes(verdict: Verdict) -> None:
    st = state(verdict=verdict, detail="PBO 0.23, DSR 0.91 no caminho 4")
    view = project(st, Zone.RESEARCH)
    assert isinstance(view, ResearchView)
    assert view.detail is None
    assert "0.23" not in serialize_for_api(view)


@pytest.mark.parametrize("verdict", [Verdict.INVALID_AST, Verdict.PROOF_FAILED])
def test_detalhe_passa_nas_duas_excecoes(verdict: Verdict) -> None:
    st = state(verdict=verdict, detail="add: combinação de unidades inválida (Price, Volume)")
    view = project(st, Zone.RESEARCH)
    assert isinstance(view, ResearchView)
    assert view.detail == st.detail


# ---------------------------------------------------------------- a última barreira


def test_vazamento_em_texto_livre_e_barrado() -> None:
    """Uma hipótese que carregue um número de métrica no texto não sai."""
    leaky = dataclasses.replace(H001, claim="A fórmula anterior deu 2.3141 de Sharpe.")
    with pytest.raises(AssertionError):
        research_payload(state(hypothesis=leaky))


@pytest.mark.parametrize("word", ["sharpe", "Sharpe", "PnL", "drawdown"])
def test_palavra_de_metrica_e_barrada(word: str) -> None:
    leaky = dataclasses.replace(H001, observable=f"maximizar o {word}")
    with pytest.raises(AssertionError):
        research_payload(state(hypothesis=leaky))


def test_assert_no_metrics_nao_barra_numeros_legitimos() -> None:
    # janelas, horários e limites da hipótese coincidem com contagens inteiras
    st = state(metrics=Metrics(tuple(
        dataclasses.replace(fold(i), n_trades=55, active_days=30, rejected_orders=2)
        for i in range(28)
    )))
    research_payload(st)


def test_payload_e_json_deterministico() -> None:
    a = research_payload(state())
    b = research_payload(state())
    assert a == b
    body = json.loads(a)
    assert body["verdict"] == "FAILED_GATE"
    assert body["formula"].startswith("(mul (in_window")
    assert body["hypothesis"]["kill_condition"]["metric"] == "lag_ms"


def test_assert_no_metrics_direto() -> None:
    with pytest.raises(AssertionError):
        assert_no_metrics('{"x": "net_pnl_points"}', None)
    assert_no_metrics('{"x": 1}', None)
