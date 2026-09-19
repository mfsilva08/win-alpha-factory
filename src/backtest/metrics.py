"""O que o motor devolve: um vetor de métricas por partição do CPCV (SPEC-fase-2 §2.5).

**Devolva o vetor, nunca a média.** O agregador do gate é quem resume.

Este é o contrato do nosso lado. O adaptador da API converte a resposta dela
para ``BacktestOutcome``; ``validate_outcome`` recusa qualquer coisa fora dele,
e a recusa vira tentativa ``crashed`` no livro-razão.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, fields
from datetime import timedelta

from src.backtest.errors import InvalidOutcome


@dataclass(frozen=True)
class FoldMetrics:
    path_id: int
    n_trades: int
    gross_pnl_points: float
    cost_points: float
    net_pnl_points: float
    sharpe: float  # anualizado pelos pregões ativos do caminho, nunca 252 fixo
    max_dd_points: float
    hit_rate: float
    avg_holding: timedelta
    max_day_share: float  # maior dia / PnL total
    skew: float
    kurtosis: float
    active_days: int
    rejected_orders: int  # ordens recusadas pelo simulador (cutoff, sessão, rolagem)


@dataclass(frozen=True)
class BacktestOutcome:
    test_id: str
    data_hash: str  # o dataset que o motor de fato usou
    folds: tuple[FoldMetrics, ...]


_FLOAT_FIELDS = tuple(
    f.name for f in fields(FoldMetrics) if f.type in ("float", float)
)


def validate_outcome(o: BacktestOutcome, *, test_id: str, data_hash: str,
                     expected_folds: int) -> None:
    """Levanta ``InvalidOutcome`` se a resposta violar o contrato."""
    if o.test_id != test_id:
        raise InvalidOutcome(f"resposta de outro teste: {o.test_id!r} != {test_id!r}")
    if o.data_hash != data_hash:
        raise InvalidOutcome("o motor usou um dataset diferente do gênesis")
    if len(o.folds) != expected_folds:
        raise InvalidOutcome(f"esperadas {expected_folds} partições, vieram {len(o.folds)}")
    ids = sorted(f.path_id for f in o.folds)
    if ids != list(range(expected_folds)):
        raise InvalidOutcome("path_id precisa cobrir 0..n-1 sem repetição")
    for f in o.folds:
        for name in _FLOAT_FIELDS:
            if not math.isfinite(getattr(f, name)):
                raise InvalidOutcome(f"partição {f.path_id}: {name} não finito")
        if f.n_trades < 0 or f.active_days < 0 or f.rejected_orders < 0:
            raise InvalidOutcome(f"partição {f.path_id}: contagem negativa")
        if not 0.0 <= f.hit_rate <= 1.0:
            raise InvalidOutcome(f"partição {f.path_id}: hit_rate fora de [0, 1]")
        if f.avg_holding < timedelta(0):
            raise InvalidOutcome(f"partição {f.path_id}: avg_holding negativo")
        if abs(f.gross_pnl_points - f.cost_points - f.net_pnl_points) > 1e-6 * max(
            1.0, abs(f.gross_pnl_points)
        ):
            raise InvalidOutcome(f"partição {f.path_id}: bruto − custo ≠ líquido")
