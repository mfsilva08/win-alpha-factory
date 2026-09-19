"""SPEC-fase-2 §2.3, §2.8, §2.9: a agregação do CPCV, que ainda depende de decisão."""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from src.backtest.metrics import FoldMetrics
from src.dsl.verdict import Verdict
from src.gate.aggregate import (
    AggregationPending,
    AggregationPolicy,
    aggregate_partitions,
    default_aggregate,
)
from src.gate.collapse import collapse
from src.gate.config import GateConfig
from tests.test_backtest import fold

GATE = GateConfig(400, 30, 120, 0.25, 0.30, 0.20, 0.95, 3000, 8, 2, 3.0)
PARTITIONS = AggregationPolicy(unit="partitions", periods_per_year=252.0, expected_units=28)
PATHS = AggregationPolicy(unit="paths", periods_per_year=252.0, expected_units=7)


def folds(n: int, **over: Any) -> tuple[FoldMetrics, ...]:
    return tuple(dataclasses.replace(fold(i), **over) for i in range(n))


def test_sessao_continua_recusando_enquanto_nao_houver_decisao() -> None:
    with pytest.raises(AggregationPending, match="§2.3"):
        default_aggregate(folds(28))


def test_politica_precisa_bater_com_o_que_veio() -> None:
    with pytest.raises(AggregationPending, match="28"):
        aggregate_partitions(folds(7), policy=PARTITIONS, pbo=0.1,
                             robustness_all_passed=True)
    with pytest.raises(AggregationPending):
        aggregate_partitions((), policy=PATHS, pbo=0.1, robustness_all_passed=True)


def test_particoes_nao_somam_operacoes_sobrepostas() -> None:
    m = aggregate_partitions(folds(28), policy=PARTITIONS, pbo=0.1,
                             robustness_all_passed=True)
    assert m.total_trades == 40 and m.min_path_trades == 40  # mediana, não soma
    assert m.active_days == 30
    p = aggregate_partitions(folds(7), policy=PATHS, pbo=0.1,
                             robustness_all_passed=True)
    assert p.total_trades == 7 * 40 and p.active_days == 7 * 30  # caminhos somam


def test_sinal_invertido_e_sharpe_por_observacao() -> None:
    mixed = (*folds(24), *folds(4, net_pnl_points=-100.0))
    m = aggregate_partitions(mixed, policy=PARTITIONS, pbo=0.1,
                             robustness_all_passed=True)
    assert m.sign_flip_frac == pytest.approx(4 / 28)
    assert m.sr == pytest.approx(0.8 / 252**0.5)  # anualizado 0,8 -> por observação
    assert m.n_obs >= 2


def test_entrada_agregada_atravessa_o_colapso() -> None:
    m = aggregate_partitions(folds(28, n_trades=900, active_days=300, sharpe=6.0),
                             policy=PARTITIONS, pbo=0.05,
                             robustness_all_passed=True)
    assert collapse(m, 10, 0.0005, GATE) is Verdict.ACCEPTED
    frouxo = dataclasses.replace(m, robustness_all_passed=False)
    assert collapse(frouxo, 10, 0.0005, GATE) is Verdict.FAILED_GATE
