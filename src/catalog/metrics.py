"""O que o robô sabe medir (SPEC-fase-4 §4.2).

Uma condição de morte que o robô não sabe medir nunca vai disparar. ``transpile``
recusa qualquer ``kill_condition.metric`` fora de ``INSTRUMENTABLE_METRICS``, e o
agente de hipótese recebe esta lista no prompt.
"""

from __future__ import annotations

INSTRUMENTABLE_METRICS: dict[str, str] = {
    "lag_ms": "atraso entre o movimento da referência e o do WIN, em ms",
    "spread_ticks": "spread médio observado na barra, em ticks",
    "realized_vol": "volatilidade realizada na janela de normalização",
    "gap_overnight": "diferença entre abertura e fechamento anterior, em pontos",
    "minutes_to_open": "distância em minutos da abertura do pregão",
    "ref_corr": "correlação móvel com o instrumento de referência",
    "fill_ratio": "fração das ordens enviadas que foram preenchidas",
}

UNAVAILABLE_METRICS: dict[str, str] = {
    "book_imbalance": "exige histórico de book; o MT5 não fornece",
    "agg_ratio": "exige fluxo de agressão; o MT5 não fornece",
}
