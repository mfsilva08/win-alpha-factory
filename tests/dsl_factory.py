"""Fábricas determinísticas para os testes da DSL: frame sintético e ASTs aleatórias."""

from __future__ import annotations

import random
from datetime import date, timedelta

import numpy as np

from src.dsl.ast import Constraints, Node, Unit
from src.dsl.market import MarketFrame
from src.dsl.ops import Op

BARS_PER_DAY = 540  # fechamentos de 09:01 a 18:00


def trading_days(start: date, n: int, holidays: frozenset[date] = frozenset()) -> list[date]:
    out: list[date] = []
    d = start
    while len(out) < n:
        if d.weekday() < 5 and d not in holidays:
            out.append(d)
        d += timedelta(days=1)
    return out


def synthetic_frame(
    n_days: int = 75, seed: int = 7, roll_day_index: int | None = 40
) -> MarketFrame:
    """Passeio aleatório na grade de 5 pontos, com fins de semana, um feriado,
    virada de dia e um dia de rolagem com salto de nível (não ajustado, de propósito)."""
    rng = np.random.default_rng(seed)
    days = trading_days(date(2026, 1, 5), n_days, frozenset({date(2026, 2, 16)}))
    n = n_days * BARS_PER_DAY
    ts = np.empty(n, dtype="datetime64[m]")
    for i, d in enumerate(days):
        base = np.datetime64(d.isoformat()) + np.timedelta64(9 * 60 + 1, "m")
        ts[i * BARS_PER_DAY : (i + 1) * BARS_PER_DAY] = base + np.arange(BARS_PER_DAY)

    shock = rng.normal(0.0, 1.0, n)
    win_steps = np.round(rng.normal(0.0, 6.0, n) + 4.0 * shock) * 5.0
    roll = np.zeros(n, dtype=np.bool_)
    if roll_day_index is not None:
        a = roll_day_index * BARS_PER_DAY
        roll[a : a + BARS_PER_DAY] = True
        win_steps[a] += 1500.0  # salto de rolagem
    close = 130_000.0 + np.cumsum(win_steps)
    high = close + 5.0 * rng.integers(0, 6, n)
    low = close - 5.0 * rng.integers(0, 6, n)
    vwap = (high + low + close) / 3.0
    volume = rng.integers(50, 5_000, n).astype(np.float64)
    trades = np.maximum(1.0, np.floor(volume / rng.integers(2, 8, n)))
    es = 5_000.0 + np.cumsum(np.round((0.8 * shock + rng.normal(0, 0.6, n)) * 4.0) * 0.25)
    wdo = 5_500.0 + np.cumsum(np.round(rng.normal(0, 3.0, n) * 2.0) * 0.5)
    tod = ts.astype(np.int64) % 1440
    session = (tod > 9 * 60 + 15) & (tod <= 17 * 60 + 30)
    return MarketFrame(
        ts=ts,
        close=close,
        high=high,
        low=low,
        volume=volume,
        trades=trades,
        vwap=vwap,
        refs={"ES": es, "WDO": wdo},
        session_mask=session,
        roll_day=roll,
    )


# ---------------------------------------------------------------- ASTs aleatórias

ALL_RESEARCH_OPS = frozenset(op for op in Op if op is not Op.CONST)

RANDOM_CONSTRAINTS = Constraints(
    allowed_ops=ALL_RESEARCH_OPS,
    forbidden_ops=frozenset(),
    max_window=144,
    max_depth=6,
    max_nodes=20,
    max_free_params=6,
)

_WINDOWS = (1, 2, 3, 5, 8, 13, 20, 21, 22, 34, 55, 89, 120, 144)
_PRESERVING = (Op.LAG, Op.DELTA, Op.ROLLING_MEAN, Op.ROLLING_STD,
               Op.ROLLING_MAX, Op.ROLLING_MIN, Op.EMA)
_NUMERIC = (Unit.PRICE, Unit.RETURN, Unit.VOLUME, Unit.Z)


class AstGen:
    """Gera ASTs bem tipadas, com viés para padrões que as reescritas pegam."""

    def __init__(self, seed: int) -> None:
        self.r = random.Random(seed)

    def w(self) -> int:
        return self.r.choice(_WINDOWS)

    def hhmm(self) -> tuple[str, str]:
        a = self.r.randrange(9 * 60, 17 * 60)
        b = self.r.randrange(a + 1, 18 * 60 + 1)
        return f"{a // 60:02d}:{a % 60:02d}", f"{b // 60:02d}:{b % 60:02d}"

    def leaf(self, u: Unit) -> Node:
        r = self.r
        if u is Unit.PRICE:
            if r.random() < 0.3:
                return Node(Op.REF, (r.choice(("ES", "WDO")),))
            return Node(r.choice((Op.CLOSE, Op.HIGH, Op.LOW, Op.VWAP)))
        if u is Unit.VOLUME:
            return Node(r.choice((Op.VOLUME, Op.TRADES, Op.MINUTES_SINCE_OPEN)))
        if u is Unit.BOOL:
            return Node(Op.IN_WINDOW, self.hhmm())
        if u is Unit.RETURN:
            return Node(Op.RET, (self.leaf(Unit.PRICE), self.w()))
        return Node(Op.ZSCORE, (self.leaf(self.r.choice(_NUMERIC[:3])), self.w()))

    def gen(self, u: Unit, d: int) -> Node:
        r = self.r
        if d <= 0 or r.random() < 0.2:
            return self.leaf(u)
        sub = d - 1
        choices: list[str] = ["window", "addsub", "mulbool"]
        if u is Unit.RETURN:
            choices += ["ret", "div"]
        if u is Unit.Z:
            choices += ["zscore", "rank", "clip", "mulz", "divz", "zz", "divmul"]
        if u is Unit.BOOL:
            choices = ["cmp", "logic", "lagbool", "mulbool", "cmpsame"]
        kind = r.choice(choices)
        if kind == "window":
            return Node(r.choice(_PRESERVING), (self.gen(u, sub), self.w()))
        if kind == "addsub":
            a = self.gen(u, sub)
            b = a if r.random() < 0.2 else self.gen(u, sub)
            return Node(r.choice((Op.ADD, Op.SUB)), (a, b))
        if kind == "mulbool":
            a, b = self.gen(u, sub), self.gen(Unit.BOOL, sub)
            return Node(Op.MUL, (a, b) if r.random() < 0.5 else (b, a))
        if kind == "ret":
            return Node(Op.RET, (self.gen(Unit.PRICE, sub), self.w()))
        if kind == "div":
            v = r.choice((Unit.PRICE, Unit.VOLUME))
            return Node(Op.DIV, (self.gen(v, sub), self.gen(v, sub)))
        if kind == "zscore":
            return Node(Op.ZSCORE, (self.gen(r.choice(_NUMERIC), sub), self.w()))
        if kind == "rank":
            return Node(Op.RANK_TS, (self.gen(r.choice(_NUMERIC), sub), self.w()))
        if kind == "clip":
            lo = r.randint(-3, 0)
            return Node(Op.CLIP, (self.gen(Unit.Z, sub), lo, r.randint(lo, 3)))
        if kind == "mulz":
            return Node(Op.MUL, (self.gen(Unit.Z, sub), self.gen(Unit.Z, sub)))
        if kind == "divz":
            return Node(Op.DIV, (self.gen(Unit.Z, sub), self.gen(Unit.Z, sub)))
        if kind == "zz":
            w = self.w()
            inner = Node(Op.ZSCORE, (self.gen(r.choice(_NUMERIC), max(sub - 1, 0)), w))
            return Node(Op.ZSCORE, (inner, w))
        if kind == "divmul":
            a, b = self.gen(Unit.Z, max(sub - 1, 0)), self.gen(Unit.Z, max(sub - 1, 0))
            return Node(Op.DIV, (Node(Op.MUL, (a, b)), r.choice((a, b))))
        if kind == "cmp":
            v = r.choice(_NUMERIC)
            return Node(r.choice((Op.GT, Op.LT)), (self.gen(v, sub), self.gen(v, sub)))
        if kind == "cmpsame":
            a = self.gen(r.choice(_NUMERIC), sub)
            return Node(r.choice((Op.GT, Op.LT)), (a, a))
        if kind == "logic":
            return Node(
                r.choice((Op.AND_, Op.OR_)),
                (self.gen(Unit.BOOL, sub), self.gen(Unit.BOOL, sub)),
            )
        if kind == "lagbool":
            return Node(Op.LAG, (self.gen(Unit.BOOL, sub), self.w()))
        raise AssertionError(kind)

    def root(self) -> Node:
        return self.gen(self.r.choice((Unit.Z, Unit.BOOL)), self.r.randint(1, 6))
