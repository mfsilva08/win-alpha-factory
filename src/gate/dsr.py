"""Sharpe deflacionado (Bailey & López de Prado, 2014) — SPEC-fase-2 §2.8.

**Unidades.** Todas as funções trabalham em Sharpe **por observação** (não
anualizado), na mesma frequência de ``T``. ``var_sr`` também. Para usar Sharpes
anualizados, divida-os — e o desvio de ``var_sr`` — por ``sqrt(períodos por ano)``
com ``deannualize`` antes. Misturar Sharpe anualizado com ``sqrt(T - 1)`` de
operações infla o DSR na direção errada.

``kurt`` é a curtose **não excedente** (normal = 3).
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from scipy.stats import norm

from src.gate.errors import StatisticsError

GAMMA = 0.5772156649015329  # Euler-Mascheroni


def sr_star(var_sr: float, n_trials: int) -> float:
    """Máximo esperado do Sharpe entre ``n_trials`` tentativas sem habilidade.

    Com uma única tentativa, o máximo esperado é a média nula: 0.
    """
    if n_trials < 1:
        raise StatisticsError("n_trials deve ser >= 1")
    if not math.isfinite(var_sr) or var_sr < 0:
        raise StatisticsError("var_sr deve ser finita e não negativa")
    if n_trials == 1:
        return 0.0
    z1 = float(norm.ppf(1 - 1 / n_trials))
    z2 = float(norm.ppf(1 - 1 / (n_trials * math.e)))
    return math.sqrt(var_sr) * ((1 - GAMMA) * z1 + GAMMA * z2)


def deflated_sharpe(sr: float, sr_star_: float, T: int, skew: float, kurt: float) -> float:
    """Probabilidade de o Sharpe verdadeiro superar ``sr_star_``, dado o observado ``sr``."""
    if T < 2:
        raise StatisticsError("T deve ser >= 2")
    for name, v in (("sr", sr), ("sr_star", sr_star_), ("skew", skew), ("kurt", kurt)):
        if not math.isfinite(v):
            raise StatisticsError(f"{name} não finito")
    den2 = 1 - skew * sr + (kurt - 1) / 4 * sr**2
    if den2 <= 0:
        raise StatisticsError("denominador do DSR não positivo: momentos incoerentes")
    num = (sr - sr_star_) * math.sqrt(T - 1)
    return float(norm.cdf(num / math.sqrt(den2)))


def deannualize(sr_annual: float, periods_per_year: float) -> float:
    if periods_per_year <= 0:
        raise StatisticsError("periods_per_year deve ser positivo")
    return sr_annual / math.sqrt(periods_per_year)


def var_sr(sharpes: Sequence[float]) -> float:
    """Variância amostral dos Sharpes das tentativas do projeto (mesmas unidades)."""
    if len(sharpes) < 2:
        raise StatisticsError("var_sr exige ao menos duas tentativas")
    xs = [float(s) for s in sharpes]
    if not all(math.isfinite(x) for x in xs):
        raise StatisticsError("Sharpe não finito")
    m = math.fsum(xs) / len(xs)
    return math.fsum((x - m) ** 2 for x in xs) / (len(xs) - 1)
