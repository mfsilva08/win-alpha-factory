"""As regras de decisão e os guards do robô, em Python puro.

É a especificação executável do que ``templates/robo.mq5.j2`` faz em MQL5, função
por função. Serve para testar as invariantes de risco (M9) e para simular a
telemetria do robô. Mudou aqui, muda no template — e vice-versa.

Invariantes de risco (provadas por teste de propriedade em ``tests/test_guards.py``):

1. **Nenhuma ordem de abertura depois do cutoff**
2. **Nenhuma ordem com tamanho acima da margem disponível**
3. **Todo preço enviado está na grade de ``tick_size``, arredondado contra nós**
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from src.codegen.telemetry import Decision, GuardBlock


@dataclass(frozen=True)
class GuardLimits:
    cutoff_min: int  # minuto do dia da zeragem compulsória
    session: tuple[int, int]  # (início, fim) em minutos do dia, fechamento da barra
    tick_size: int
    margin_per_contract: float
    daily_loss_points: float  # perda diária máxima, em pontos, positiva
    max_rejections: int  # circuit breaker por rejeições em série


def decide(signal: float | None, threshold: float, direction: int, position: int) -> Decision:
    """Abertura só com posição zerada. ``direction`` = +1 (LONG_ON_HIGH) ou −1."""
    if signal is None or math.isnan(signal) or position != 0:
        return Decision.NONE
    s = direction * signal
    if s > threshold:
        return Decision.OPEN_LONG
    if s < -threshold:
        return Decision.OPEN_SHORT
    return Decision.NONE


def max_lots(equity: float, margin_per_contract: float) -> int:
    if margin_per_contract <= 0:
        raise ValueError("margem por contrato deve ser positiva")
    return max(math.floor(equity / margin_per_contract), 0)


def to_grid(price: float, tick: int, side: int) -> float:
    """Preço na grade, arredondado **contra nós**: compra para cima, venda para baixo."""
    q = price / tick
    steps = math.ceil(q - 1e-9) if side > 0 else math.floor(q + 1e-9)
    return float(steps * tick)


def on_grid(price: float, tick: int) -> bool:
    return abs(price / tick - round(price / tick)) < 1e-9


def guard_order(
    lim: GuardLimits, tod: int, lots: int, equity: float, price: float,
    daily_pnl_points: float, consecutive_rejections: int,
) -> GuardBlock | None:
    """``None`` libera a ordem de abertura. A ordem dos testes é a do template."""
    if consecutive_rejections >= lim.max_rejections:
        return GuardBlock.BREAKER
    if daily_pnl_points <= -lim.daily_loss_points:
        return GuardBlock.DAILY_LOSS
    if tod >= lim.cutoff_min or not lim.session[0] < tod <= lim.session[1]:
        return GuardBlock.CUTOFF
    if lots < 1 or lots > max_lots(equity, lim.margin_per_contract):
        return GuardBlock.MARGIN
    if not on_grid(price, lim.tick_size):
        return GuardBlock.GRID
    return None


def must_flatten(tod: int, held_min: int, lim: GuardLimits, max_holding_min: int) -> bool:
    """Posição aberta é zerada a mercado no cutoff ou ao estourar o tempo máximo."""
    return tod >= lim.cutoff_min or held_min >= max_holding_min
