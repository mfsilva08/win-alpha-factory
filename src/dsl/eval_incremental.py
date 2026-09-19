"""Avaliação incremental, barra a barra, com custo O(1) por operador de janela.

É o espelho de ``eval_vectorized`` e o modelo do estado que o transpilador emite
em MQL5. Traduções obrigatórias (SPEC-fase-1 §1.6):

| Operador         | Estado                                   |
|------------------|------------------------------------------|
| lag, delta, ret  | ring buffer de k+1                       |
| rolling_mean     | soma corrente + ring buffer              |
| rolling_std      | Welford (mean + M2) com remoção          |
| rolling_max/min  | deque monotônica                         |
| ema              | recorrência de um valor                  |
| zscore           | Welford acima                            |

Soma corrente e Welford são **ressincronizados** a partir do ring buffer a cada
``w`` barras (custo amortizado O(1)), para que o erro de arredondamento não se
acumule por dezenas de milhares de barras. Janela constante (máx == mín) tem
desvio exatamente 0, nas duas implementações.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from collections import deque
from datetime import date, datetime

from src.dsl.ast import Node, max_window_of, parse_hhmm
from src.dsl.errors import DslEvalError, MarketDataError
from src.dsl.eval_vectorized import GUARD
from src.dsl.market import Bar
from src.dsl.ops import Op

NAN = math.nan


def _isnan(v: float) -> bool:
    return math.isnan(v)


# ---------------------------------------------------------------- estruturas de janela


class _Window:
    """Ring buffer dos últimos ``w`` valores válidos, com recusa de NaN tardio."""

    def __init__(self, w: int, name: str) -> None:
        self.w = w
        self.name = name
        self.buf: deque[float] = deque(maxlen=w)
        self.seen = 0

    def accept(self, v: float) -> bool:
        """``False`` se ``v`` é NaN do aquecimento; levanta se for NaN tardio."""
        if _isnan(v):
            if self.seen:
                raise DslEvalError(f"{self.name}: NaN após o aquecimento")
            return False
        self.seen += 1
        return True

    @property
    def full(self) -> bool:
        return len(self.buf) == self.w


class _MonoDeque:
    """Máximo (ou mínimo) de janela deslizante, O(1) amortizado."""

    def __init__(self, w: int, is_max: bool) -> None:
        self.w = w
        self.is_max = is_max
        self.q: deque[tuple[int, float]] = deque()

    def push(self, i: int, v: float) -> None:
        q = self.q
        if self.is_max:
            while q and q[-1][1] <= v:
                q.pop()
        else:
            while q and q[-1][1] >= v:
                q.pop()
        q.append((i, v))
        while q[0][0] <= i - self.w:
            q.popleft()

    def value(self) -> float:
        return self.q[0][1]


class _RollingSum:
    def __init__(self, w: int) -> None:
        self.win = _Window(w, "rolling_mean")
        self.total = 0.0
        self.since_sync = 0

    def push(self, v: float) -> None:
        win = self.win
        if win.full:
            self.total -= win.buf[0]
        win.buf.append(v)
        self.total += v
        self.since_sync += 1
        if self.since_sync >= win.w:
            self.total = math.fsum(win.buf)
            self.since_sync = 0

    def mean(self) -> float:
        return self.total / self.win.w


class _Welford:
    """Média e M2 de janela deslizante, com adição e remoção (nunca E[x²]−E[x]²)."""

    def __init__(self, w: int, name: str) -> None:
        self.win = _Window(w, name)
        self.n = 0
        self.mean = 0.0
        self.m2 = 0.0
        self.since_sync = 0
        self.hi = _MonoDeque(w, True)
        self.lo = _MonoDeque(w, False)
        self.i = 0

    def _add(self, x: float) -> None:
        self.n += 1
        delta = x - self.mean
        self.mean += delta / self.n
        self.m2 += delta * (x - self.mean)

    def _remove(self, x: float) -> None:
        self.n -= 1
        if self.n == 0:
            self.mean = 0.0
            self.m2 = 0.0
            return
        delta = x - self.mean
        self.mean -= delta / self.n
        self.m2 -= delta * (x - self.mean)

    def push(self, v: float) -> None:
        win = self.win
        if win.full:
            self._remove(win.buf[0])
        win.buf.append(v)
        self._add(v)
        self.hi.push(self.i, v)
        self.lo.push(self.i, v)
        self.i += 1
        self.since_sync += 1
        if self.since_sync >= win.w:
            self.n, self.mean, self.m2 = 0, 0.0, 0.0
            for x in win.buf:
                self._add(x)
            self.since_sync = 0

    def std(self) -> float:
        if self.n < 2 or self.hi.value() == self.lo.value():
            return 0.0
        return math.sqrt(max(self.m2 / (self.n - 1), 0.0))


# ---------------------------------------------------------------- nós avaliadores


class _Eval(ABC):
    @abstractmethod
    def step(self, bar: Bar) -> float: ...


class _Field(_Eval):
    def __init__(self, op: Op) -> None:
        self.op = op

    def step(self, bar: Bar) -> float:
        op = self.op
        if op is Op.CLOSE:
            return bar.close
        if op is Op.HIGH:
            return bar.high
        if op is Op.LOW:
            return bar.low
        if op is Op.VOLUME:
            return bar.volume
        if op is Op.TRADES:
            return bar.trades
        return bar.vwap


class _Ref(_Eval):
    def __init__(self, sym: str) -> None:
        self.sym = sym

    def step(self, bar: Bar) -> float:
        if self.sym not in bar.refs:
            raise MarketDataError(f"barra sem a referência {self.sym}")
        return bar.refs[self.sym]


class _Const(_Eval):
    def __init__(self, v: int) -> None:
        self.v = float(v)

    def step(self, bar: Bar) -> float:
        return self.v


class _InWindow(_Eval):
    def __init__(self, h1: str, h2: str) -> None:
        self.lo = parse_hhmm(h1)
        self.hi = parse_hhmm(h2)

    def step(self, bar: Bar) -> float:
        tod = bar.ts.hour * 60 + bar.ts.minute
        return 1.0 if self.lo < tod <= self.hi else 0.0


class _MinutesSinceOpen(_Eval):
    def __init__(self) -> None:
        self.day: date | None = None
        self.first = 0

    def step(self, bar: Bar) -> float:
        t = bar.ts
        minutes = t.toordinal() * 1440 + t.hour * 60 + t.minute
        if t.date() != self.day:
            self.day = t.date()
            self.first = minutes
        return float(minutes - self.first + 1)


class _Lag(_Eval):
    """lag, delta e ret compartilham o ring buffer de k+1."""

    def __init__(self, op: Op, child: _Eval, k: int) -> None:
        self.op = op
        self.child = child
        self.size = k + 1
        self.buf: deque[float] = deque(maxlen=self.size)

    def step(self, bar: Bar) -> float:
        x = self.child.step(bar)
        self.buf.append(x)
        if len(self.buf) < self.size:
            return NAN
        old = self.buf[0]
        if self.op is Op.LAG:
            return old
        if _isnan(x) or _isnan(old):
            return NAN
        if self.op is Op.DELTA:
            return x - old
        return 0.0 if abs(old) < GUARD else x / old - 1.0


class _RollingMean(_Eval):
    def __init__(self, child: _Eval, w: int) -> None:
        self.child = child
        self.s = _RollingSum(w)

    def step(self, bar: Bar) -> float:
        x = self.child.step(bar)
        if not self.s.win.accept(x):
            return NAN
        self.s.push(x)
        return self.s.mean() if self.s.win.full else NAN


class _RollingStd(_Eval):
    def __init__(self, child: _Eval, w: int) -> None:
        self.child = child
        self.wf = _Welford(w, "rolling_std")

    def step(self, bar: Bar) -> float:
        x = self.child.step(bar)
        if not self.wf.win.accept(x):
            return NAN
        self.wf.push(x)
        return self.wf.std() if self.wf.win.full else NAN


class _ZScore(_Eval):
    def __init__(self, child: _Eval, w: int) -> None:
        self.child = child
        self.wf = _Welford(w, "zscore")

    def step(self, bar: Bar) -> float:
        x = self.child.step(bar)
        if not self.wf.win.accept(x):
            return NAN
        self.wf.push(x)
        if not self.wf.win.full:
            return NAN
        sd = self.wf.std()
        return 0.0 if sd < GUARD else (x - self.wf.mean) / sd


class _RollingExtreme(_Eval):
    def __init__(self, child: _Eval, w: int, is_max: bool) -> None:
        self.child = child
        self.win = _Window(w, "rolling_max" if is_max else "rolling_min")
        self.dq = _MonoDeque(w, is_max)
        self.i = 0

    def step(self, bar: Bar) -> float:
        x = self.child.step(bar)
        if not self.win.accept(x):
            return NAN
        self.win.buf.append(x)
        self.dq.push(self.i, x)
        self.i += 1
        return self.dq.value() if self.win.full else NAN


class _Ema(_Eval):
    def __init__(self, child: _Eval, w: int) -> None:
        self.child = child
        self.alpha = 2.0 / (w + 1)
        self.w = w
        self.e = 0.0
        self.seen = 0

    def step(self, bar: Bar) -> float:
        v = self.child.step(bar)
        if _isnan(v):
            if self.seen:
                raise DslEvalError("ema: NaN após o aquecimento")
            return NAN
        self.e = v if self.seen == 0 else self.e + self.alpha * (v - self.e)
        self.seen += 1
        return self.e if self.seen >= self.w else NAN


class _RankTs(_Eval):
    """Posto da barra atual na janela, em [-1, 1]. O(w) por barra."""

    def __init__(self, child: _Eval, w: int) -> None:
        self.child = child
        self.win = _Window(w, "rank_ts")

    def step(self, bar: Bar) -> float:
        x = self.child.step(bar)
        if not self.win.accept(x):
            return NAN
        self.win.buf.append(x)
        if not self.win.full:
            return NAN
        w = self.win.w
        if w == 1:
            return 0.0
        less = sum(1 for v in self.win.buf if v < x)
        equal = sum(1 for v in self.win.buf if v == x) - 1
        return 2.0 * (less + 0.5 * equal) / (w - 1) - 1.0


class _Clip(_Eval):
    def __init__(self, child: _Eval, lo: int, hi: int) -> None:
        self.child = child
        self.lo = float(lo)
        self.hi = float(hi)

    def step(self, bar: Bar) -> float:
        x = self.child.step(bar)
        if _isnan(x):
            return NAN
        return min(max(x, self.lo), self.hi)


class _Binary(_Eval):
    def __init__(self, op: Op, a: _Eval, b: _Eval) -> None:
        self.op = op
        self.a = a
        self.b = b

    def step(self, bar: Bar) -> float:
        a = self.a.step(bar)
        b = self.b.step(bar)
        op = self.op
        if op is Op.ADD:
            return a + b
        if op is Op.SUB:
            return a - b
        if op is Op.MUL:
            return a * b
        if _isnan(a) or _isnan(b):
            return NAN
        if op is Op.DIV:
            return 0.0 if abs(b) < GUARD else a / b
        if op is Op.GT:
            return 1.0 if a > b else 0.0
        if op is Op.LT:
            return 1.0 if a < b else 0.0
        if op is Op.AND_:
            return 1.0 if (a != 0 and b != 0) else 0.0
        return 1.0 if (a != 0 or b != 0) else 0.0


# ---------------------------------------------------------------- compilação


def _arg_node(n: Node, i: int) -> Node:
    a = n.args[i]
    if not isinstance(a, Node):
        raise DslEvalError(f"{n.op.value}: argumento {i + 1} deve ser nó")
    return a


def _arg_int(n: Node, i: int) -> int:
    a = n.args[i]
    if not isinstance(a, int) or isinstance(a, bool):
        raise DslEvalError(f"{n.op.value}: argumento {i + 1} deve ser inteiro")
    return a


def _arg_str(n: Node, i: int) -> str:
    a = n.args[i]
    if not isinstance(a, str):
        raise DslEvalError(f"{n.op.value}: argumento {i + 1} deve ser texto")
    return a


def _compile(n: Node) -> _Eval:
    op = n.op
    if op in (Op.CLOSE, Op.HIGH, Op.LOW, Op.VOLUME, Op.TRADES, Op.VWAP):
        return _Field(op)
    if op is Op.REF:
        return _Ref(_arg_str(n, 0))
    if op is Op.CONST:
        return _Const(_arg_int(n, 0))
    if op is Op.IN_WINDOW:
        return _InWindow(_arg_str(n, 0), _arg_str(n, 1))
    if op is Op.MINUTES_SINCE_OPEN:
        return _MinutesSinceOpen()
    if op is Op.CLIP:
        return _Clip(_compile(_arg_node(n, 0)), _arg_int(n, 1), _arg_int(n, 2))
    if op in (Op.LAG, Op.DELTA, Op.RET):
        return _Lag(op, _compile(_arg_node(n, 0)), _arg_int(n, 1))
    if op is Op.ROLLING_MEAN:
        return _RollingMean(_compile(_arg_node(n, 0)), _arg_int(n, 1))
    if op is Op.ROLLING_STD:
        return _RollingStd(_compile(_arg_node(n, 0)), _arg_int(n, 1))
    if op is Op.ROLLING_MAX:
        return _RollingExtreme(_compile(_arg_node(n, 0)), _arg_int(n, 1), True)
    if op is Op.ROLLING_MIN:
        return _RollingExtreme(_compile(_arg_node(n, 0)), _arg_int(n, 1), False)
    if op is Op.EMA:
        return _Ema(_compile(_arg_node(n, 0)), _arg_int(n, 1))
    if op is Op.ZSCORE:
        return _ZScore(_compile(_arg_node(n, 0)), _arg_int(n, 1))
    if op is Op.RANK_TS:
        return _RankTs(_compile(_arg_node(n, 0)), _arg_int(n, 1))
    return _Binary(op, _compile(_arg_node(n, 0)), _compile(_arg_node(n, 1)))


class IncrementalState:
    """Estado de uma AST avaliada barra a barra."""

    def __init__(self, node: Node) -> None:
        self._node = node
        self._root = _compile(node)
        self._signal = NAN
        self._bars = 0
        self._last_ts: datetime | None = None

    def update(self, bar: Bar) -> None:
        if self._last_ts is not None and not bar.ts > self._last_ts:
            raise MarketDataError("barras fora de ordem ou repetidas")
        self._last_ts = bar.ts
        self._signal = self._root.step(bar)
        self._bars += 1

    def signal(self) -> float:
        return self._signal

    def warmup_bars(self) -> int:
        """Barras a descartar antes de operar: ``max_window * 3``."""
        return max_window_of(self._node) * 3

    @property
    def bars_seen(self) -> int:
        return self._bars
