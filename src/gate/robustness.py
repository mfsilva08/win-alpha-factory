"""Bateria de robustez — conjuntiva, conta como **uma** tentativa (SPEC-fase-2 §2.10).

**Nunca escolha o melhor.** As variantes daqui não passam por ``runner.run``: não
são tentativas novas, são o teste de uma tentativa já registrada. Por isso também
não entram no livro-razão.

Este módulo tem as regras de julgamento e o gerador de variantes de janela. A
execução das variantes (ruído, subperíodos, custos) depende do contrato da API.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass

from src.dsl.ast import Node
from src.dsl.canonical import WINDOW_BUCKETS, snap_to_bucket
from src.dsl.ops import WINDOW_OPS
from src.gate.errors import StatisticsError

NOISE_RESAMPLES = 100  # ±1 tick, prereg
N_SUBPERIODS = 3
COST_FACTORS = (1.5, 2.0)


@dataclass(frozen=True)
class RobustnessResult:
    noise: bool
    subperiods: bool
    costs: bool
    parameters: bool

    @property
    def all_passed(self) -> bool:
        return self.noise and self.subperiods and self.costs and self.parameters


def noise_ok(net_points: Sequence[float]) -> bool:
    """Mediana positiva do PnL líquido sobre as reamostragens com ruído de ±1 tick."""
    if len(net_points) != NOISE_RESAMPLES:
        raise StatisticsError(f"esperadas {NOISE_RESAMPLES} reamostragens")
    return statistics.median(net_points) > 0


def sign_consistent(reference_net: float, variants_net: Sequence[float]) -> bool:
    """Referência positiva e todas as variantes com o mesmo sinal (estritamente)."""
    return reference_net > 0 and all(v > 0 for v in variants_net)


def subperiods_ok(reference_net: float, nets: Sequence[float]) -> bool:
    if len(nets) != N_SUBPERIODS:
        raise StatisticsError(f"esperados {N_SUBPERIODS} subperíodos")
    return sign_consistent(reference_net, nets)


def costs_ok(nets_by_factor: Sequence[float]) -> bool:
    """PnL líquido positivo a 1,5× e a 2,0× o custo."""
    if len(nets_by_factor) != len(COST_FACTORS):
        raise StatisticsError(f"esperados {len(COST_FACTORS)} fatores de custo")
    return all(v > 0 for v in nets_by_factor)


def parameters_ok(reference_net: float, nets: Sequence[float]) -> bool:
    return sign_consistent(reference_net, nets)


def judge(
    reference_net: float,
    noise_nets: Sequence[float],
    subperiod_nets: Sequence[float],
    cost_nets: Sequence[float],
    neighbor_nets: Sequence[float],
) -> RobustnessResult:
    return RobustnessResult(
        noise=noise_ok(noise_nets),
        subperiods=subperiods_ok(reference_net, subperiod_nets),
        costs=costs_ok(cost_nets),
        parameters=parameters_ok(reference_net, neighbor_nets),
    )


# ---------------------------------------------------------------- variantes de janela


def _neighbor(w: int, step: int) -> int:
    i = WINDOW_BUCKETS.index(snap_to_bucket(w))
    j = min(max(i + step, 0), len(WINDOW_BUCKETS) - 1)
    return WINDOW_BUCKETS[j]


def _shift(n: Node, step: int) -> Node:
    args: list[Node | int | str] = []
    for i, a in enumerate(n.args):
        if isinstance(a, Node):
            args.append(_shift(a, step))
        elif i == 1 and n.op in WINDOW_OPS and isinstance(a, int):
            args.append(_neighbor(a, step))
        else:
            args.append(a)
    return Node(n.op, tuple(args))


def neighbor_variants(ast: Node) -> tuple[Node, ...]:
    """Todas as janelas no bucket vizinho de baixo, e todas no de cima.

    Variantes idênticas à original (janela 1 descendo, 144 subindo) são descartadas.
    """
    out: list[Node] = []
    for step in (-1, 1):
        v = _shift(ast, step)
        if v != ast and v not in out:
            out.append(v)
    return tuple(out)
