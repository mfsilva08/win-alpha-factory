"""Os dados que os avaliadores consomem: ``MarketFrame`` (vetorizado) e ``Bar`` (incremental).

Convenções, fixadas aqui e na SPEC-fase-1 §1.5:

- ``ts`` é o horário de **fechamento** da barra M1, hora local da B3, sem timezone.
  Os arquivos do MT5 marcam a abertura; o carregador (``backtest/data.py``)
  desloca +1 minuto antes de construir o frame.
- Os preços são a série contínua ajustada. Rolagem é só marcada em ``roll_day``.
- Nenhum ``NaN`` nem ``inf`` é aceito: o aquecimento é o único lugar onde o
  avaliador produz ``NaN``.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType

import numpy as np
import numpy.typing as npt

from src.dsl.errors import MarketDataError
from src.dsl.ops import REF_SYMBOLS

FloatArray = npt.NDArray[np.float64]
BoolArray = npt.NDArray[np.bool_]
TimeArray = npt.NDArray[np.datetime64]
IntArray = npt.NDArray[np.int64]

MINUTES_PER_DAY = 1440


@dataclass(frozen=True)
class Bar:
    ts: datetime  # fechamento da barra, hora local da B3
    close: float
    high: float
    low: float
    volume: float
    trades: float
    vwap: float
    refs: Mapping[str, float] = field(default_factory=dict)
    roll_day: bool = False


def _frozen_float(name: str, a: npt.ArrayLike, n: int | None) -> FloatArray:
    arr = np.array(a, dtype=np.float64)
    if arr.ndim != 1:
        raise MarketDataError(f"{name}: esperado array 1-D")
    if n is not None and arr.shape[0] != n:
        raise MarketDataError(f"{name}: tamanho {arr.shape[0]} diferente de {n}")
    if not np.all(np.isfinite(arr)):
        raise MarketDataError(f"{name}: contém NaN ou inf")
    arr.setflags(write=False)
    return arr


@dataclass(frozen=True)
class MarketFrame:
    """Arrays alinhados de WIN e das referências, imutáveis."""

    ts: TimeArray
    close: FloatArray
    high: FloatArray
    low: FloatArray
    volume: FloatArray
    trades: FloatArray
    vwap: FloatArray
    refs: Mapping[str, FloatArray]
    session_mask: BoolArray
    roll_day: BoolArray

    def __post_init__(self) -> None:
        ts = np.array(self.ts, dtype="datetime64[m]")
        if ts.ndim != 1 or ts.shape[0] == 0:
            raise MarketDataError("ts: esperado array 1-D não vazio")
        if np.any(np.isnat(ts)):
            raise MarketDataError("ts: contém NaT")
        if ts.shape[0] > 1 and not np.all(ts[1:] > ts[:-1]):
            raise MarketDataError("ts: precisa ser estritamente crescente")
        ts.setflags(write=False)
        n = int(ts.shape[0])
        object.__setattr__(self, "ts", ts)
        for name in ("close", "high", "low", "volume", "trades", "vwap"):
            object.__setattr__(self, name, _frozen_float(name, getattr(self, name), n))
        refs: dict[str, FloatArray] = {}
        for sym, arr in self.refs.items():
            if sym not in REF_SYMBOLS:
                raise MarketDataError(f"referência desconhecida: {sym}")
            refs[sym] = _frozen_float(f"ref {sym}", arr, n)
        object.__setattr__(self, "refs", MappingProxyType(refs))
        for name in ("session_mask", "roll_day"):
            arr = np.array(getattr(self, name), dtype=np.bool_)
            if arr.shape != (n,):
                raise MarketDataError(f"{name}: tamanho diferente de {n}")
            arr.setflags(write=False)
            object.__setattr__(self, name, arr)

    def __len__(self) -> int:
        return int(self.ts.shape[0])

    # ------------------------------------------------------------ derivados

    def epoch_minutes(self) -> IntArray:
        return self.ts.astype("datetime64[m]").astype(np.int64)

    def minute_of_day(self) -> IntArray:
        return self.epoch_minutes() % MINUTES_PER_DAY

    def day_index(self) -> IntArray:
        return self.epoch_minutes() // MINUTES_PER_DAY

    # ------------------------------------------------------------ barras

    def bar(self, i: int) -> Bar:
        ts = self.ts[i].astype("datetime64[m]").item()
        assert isinstance(ts, datetime)
        return Bar(
            ts=ts,
            close=float(self.close[i]),
            high=float(self.high[i]),
            low=float(self.low[i]),
            volume=float(self.volume[i]),
            trades=float(self.trades[i]),
            vwap=float(self.vwap[i]),
            refs={sym: float(arr[i]) for sym, arr in self.refs.items()},
            roll_day=bool(self.roll_day[i]),
        )

    def bars(self) -> Iterator[Bar]:
        for i in range(len(self)):
            yield self.bar(i)
