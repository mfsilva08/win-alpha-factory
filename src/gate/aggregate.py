"""Das partições do CPCV para a entrada do gate — **a decisão pendente do M4**.

Três perguntas ainda abertas, e cada uma muda o número que sai daqui:

- **D2** (SPEC-fase-2 §2.3): as `C(8,2) = 28` combinações são *partições* de
  treino/teste; os *caminhos* completos de backtest são `k/N · C(N,k) = 7`.
  `min_trades_per_path`, a fração de sinal invertido e o PBO mudam conforme a
  resposta
- **D3** (§2.8): de onde vem o Sharpe **por observação** de cada tentativa, que o
  `var_sr` usa. A API precisa devolvê-lo, ou o fator de anualização
- **D4** (§2.9): quem são as N configurações que o PBO compara

`aggregate_partitions` é uma implementação de referência, pronta para quando a
resposta vier: ela **exige** que você declare a política e passe o PBO calculado.
`default_aggregate` continua recusando — é o que a sessão chama, e é de propósito
que ela pare.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from src.backtest.metrics import FoldMetrics
from src.gate.collapse import GateInputs
from src.gate.dsr import deannualize
from src.gate.errors import GateError


class AggregationPending(GateError):
    """A política de agregação do CPCV ainda não foi decidida (D2, D3, D4)."""


@dataclass(frozen=True)
class AggregationPolicy:
    """O que precisa estar decidido para o gate rodar."""

    unit: Literal["partitions", "paths"]  # D2: 28 partições ou 7 caminhos
    periods_per_year: float  # D3: para converter o Sharpe anualizado
    expected_units: int  # 28 ou 7, conferido contra o que veio


def aggregate_partitions(metrics: Sequence[FoldMetrics], *, policy: AggregationPolicy,
                         pbo: float,
                         robustness_all_passed: bool) -> GateInputs:
    """Vetor de ``FoldMetrics`` → ``GateInputs``. Nunca usa a média sozinha.

    - contagens (operações, dias ativos) são somadas quando a unidade são
      caminhos completos e tomadas pela **mediana** quando são partições, que se
      sobrepõem e contariam a mesma operação várias vezes
    - a fração de sinal invertido é a de unidades com PnL líquido de sinal
      oposto ao agregado
    - o Sharpe entra por observação (D3), pela mediana entre unidades
    """
    folds = list(metrics)
    if not folds:
        raise AggregationPending("nenhuma partição na resposta do motor")
    if len(folds) != policy.expected_units:
        raise AggregationPending(
            f"esperadas {policy.expected_units} unidades ({policy.unit}), vieram {len(folds)}"
        )
    nets = [f.net_pnl_points for f in folds]
    median_net = statistics.median(nets)
    sign = 1.0 if median_net >= 0 else -1.0
    overlapping = policy.unit == "partitions"
    total_trades = (int(statistics.median([f.n_trades for f in folds])) if overlapping
                    else sum(f.n_trades for f in folds))
    active_days = (int(statistics.median([f.active_days for f in folds])) if overlapping
                   else sum(f.active_days for f in folds))
    scale = 1.0 if overlapping else 1.0 / len(folds)
    sr_ann = statistics.median([f.sharpe for f in folds])
    return GateInputs(
        total_trades=total_trades,
        min_path_trades=min(f.n_trades for f in folds),
        active_days=active_days,
        max_day_share=max(f.max_day_share for f in folds),
        gross_points=sum(f.gross_pnl_points for f in folds) * scale,
        cost_points=sum(f.cost_points for f in folds) * scale,
        sign_flip_frac=sum(1 for v in nets if v * sign < 0) / len(folds),
        pbo=pbo,
        sr=deannualize(sr_ann, policy.periods_per_year),
        n_obs=max(total_trades, 2),
        skew=statistics.median([f.skew for f in folds]),
        kurtosis=statistics.median([f.kurtosis for f in folds]),
        robustness_all_passed=robustness_all_passed,
    )


def default_aggregate(metrics: object) -> GateInputs:
    """O que a sessão usa enquanto D2, D3 e D4 não forem decididas: recusa."""
    raise AggregationPending(
        "decida a agregação do CPCV (SPEC-fase-2 §2.3), a origem do Sharpe por "
        "observação (§2.8) e as configurações do PBO (§2.9); então use "
        "aggregate_partitions com uma AggregationPolicy"
    )
