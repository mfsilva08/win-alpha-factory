"""O vocabulário fechado da DSL.

Não acrescente operadores. Cada um a mais amplia o espaço de busca e encarece o
limiar de todas as fórmulas futuras. ``CONST`` é a única exceção e não é
vocabulário de pesquisa: só a canonicalização o produz, e ``validate`` o recusa
em qualquer AST de entrada.
"""

from __future__ import annotations

from enum import StrEnum


class Op(StrEnum):
    # séries base
    CLOSE = "close"
    HIGH = "high"
    LOW = "low"
    VOLUME = "volume"
    TRADES = "trades"
    VWAP = "vwap"
    # referência a outro instrumento
    REF = "ref"
    # temporais
    LAG = "lag"
    DELTA = "delta"
    RET = "ret"
    ROLLING_MEAN = "rolling_mean"
    ROLLING_STD = "rolling_std"
    ROLLING_MAX = "rolling_max"
    ROLLING_MIN = "rolling_min"
    EMA = "ema"
    # normalização
    ZSCORE = "zscore"
    RANK_TS = "rank_ts"
    CLIP = "clip"
    # combinação
    ADD = "add"
    SUB = "sub"
    MUL = "mul"
    DIV = "div"
    # lógica
    GT = "gt"
    LT = "lt"
    AND_ = "and_"
    OR_ = "or_"
    # sessão
    IN_WINDOW = "in_window"
    MINUTES_SINCE_OPEN = "minutes_since_open"
    # interno: produzido apenas pela canonicalização
    CONST = "const"


# Séries base: sempre permitidas, salvo se estiverem em ``forbidden_ops``.
BASE_SERIES: frozenset[Op] = frozenset(
    {Op.CLOSE, Op.HIGH, Op.LOW, Op.VOLUME, Op.TRADES, Op.VWAP}
)

# Operadores cujo segundo argumento é uma janela inteira.
WINDOW_OPS: frozenset[Op] = frozenset(
    {
        Op.LAG,
        Op.DELTA,
        Op.RET,
        Op.ROLLING_MEAN,
        Op.ROLLING_STD,
        Op.ROLLING_MAX,
        Op.ROLLING_MIN,
        Op.EMA,
        Op.ZSCORE,
        Op.RANK_TS,
    }
)

# Operadores comutativos: a canonicalização ordena seus dois argumentos.
COMMUTATIVE_OPS: frozenset[Op] = frozenset({Op.ADD, Op.MUL, Op.AND_, Op.OR_})

REF_SYMBOLS: frozenset[str] = frozenset({"ES", "WDO"})


class ArgKind(StrEnum):
    NODE = "node"
    WINDOW = "window"
    INT = "int"
    STR = "str"


# Assinatura posicional de cada operador (checagem 2 do ``validate``).
ARG_KINDS: dict[Op, tuple[ArgKind, ...]] = {
    Op.CLOSE: (),
    Op.HIGH: (),
    Op.LOW: (),
    Op.VOLUME: (),
    Op.TRADES: (),
    Op.VWAP: (),
    Op.REF: (ArgKind.STR,),
    **{op: (ArgKind.NODE, ArgKind.WINDOW) for op in WINDOW_OPS},
    Op.CLIP: (ArgKind.NODE, ArgKind.INT, ArgKind.INT),
    Op.ADD: (ArgKind.NODE, ArgKind.NODE),
    Op.SUB: (ArgKind.NODE, ArgKind.NODE),
    Op.MUL: (ArgKind.NODE, ArgKind.NODE),
    Op.DIV: (ArgKind.NODE, ArgKind.NODE),
    Op.GT: (ArgKind.NODE, ArgKind.NODE),
    Op.LT: (ArgKind.NODE, ArgKind.NODE),
    Op.AND_: (ArgKind.NODE, ArgKind.NODE),
    Op.OR_: (ArgKind.NODE, ArgKind.NODE),
    Op.IN_WINDOW: (ArgKind.STR, ArgKind.STR),
    Op.MINUTES_SINCE_OPEN: (),
    Op.CONST: (ArgKind.INT, ArgKind.STR),
}
