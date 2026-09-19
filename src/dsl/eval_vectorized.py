"""Avaliação vetorizada de uma AST sobre um ``MarketFrame``.

A semântica de cada operador está em SPEC-fase-1 §1.5 e é compartilhada, operador
por operador, com ``eval_incremental``. Qualquer mudança aqui exige a mesma mudança
lá, e o teste de paridade é quem garante isso.
"""

from __future__ import annotations

import math
from collections.abc import Callable

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from src.dsl.ast import Node, parse_hhmm
from src.dsl.errors import DslEvalError, MarketDataError
from src.dsl.market import FloatArray, MarketFrame
from src.dsl.ops import Op

GUARD = 1e-12  # denominador ou desvio abaixo disso produz 0.0

_CHUNK = 20_000  # linhas por bloco nas janelas deslizantes, para limitar memória


def _node(n: Node, i: int) -> Node:
    a = n.args[i]
    if not isinstance(a, Node):
        raise DslEvalError(f"{n.op.value}: argumento {i + 1} deve ser nó")
    return a


def _int(n: Node, i: int) -> int:
    a = n.args[i]
    if not isinstance(a, int) or isinstance(a, bool):
        raise DslEvalError(f"{n.op.value}: argumento {i + 1} deve ser inteiro")
    return a


def _str(n: Node, i: int) -> str:
    a = n.args[i]
    if not isinstance(a, str):
        raise DslEvalError(f"{n.op.value}: argumento {i + 1} deve ser texto")
    return a


# ---------------------------------------------------------------- janelas


def _shift(x: FloatArray, k: int) -> FloatArray:
    out = np.full_like(x, np.nan)
    if k < x.shape[0]:
        out[k:] = x[: x.shape[0] - k]
    return out


def _rolling(
    x: FloatArray, w: int, fn: Callable[[FloatArray, FloatArray], FloatArray]
) -> FloatArray:
    """Aplica ``fn(janelas, atual)`` bloco a bloco; ``NaN`` onde a janela tem ``NaN``.

    ``janelas`` tem forma (m, w) com a barra atual na última coluna.
    """
    n = x.shape[0]
    out = np.full(n, np.nan)
    if n < w:
        return out
    view = sliding_window_view(x, w)
    for start in range(0, view.shape[0], _CHUNK):
        block = view[start : start + _CHUNK]
        cur = block[:, -1]
        res = fn(block, cur)
        bad = np.isnan(block).any(axis=1)
        res = np.where(bad, np.nan, res)
        out[w - 1 + start : w - 1 + start + block.shape[0]] = res
    return out


def _std(block: FloatArray) -> FloatArray:
    """Desvio amostral (n-1); 0 com uma amostra ou janela constante."""
    w = block.shape[1]
    if w == 1:
        return np.zeros(block.shape[0])
    with np.errstate(invalid="ignore"):
        s: FloatArray = np.std(block, axis=1, ddof=1)
        flat = np.max(block, axis=1) == np.min(block, axis=1)
    return np.where(flat, 0.0, s)


def _mean(block: FloatArray, _cur: FloatArray) -> FloatArray:
    r: FloatArray = np.sum(block, axis=1) / block.shape[1]
    return r


def _zscore(block: FloatArray, cur: FloatArray) -> FloatArray:
    mean = np.sum(block, axis=1) / block.shape[1]
    std = _std(block)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(std < GUARD, 0.0, (cur - mean) / std)


def _rank(block: FloatArray, cur: FloatArray) -> FloatArray:
    w = block.shape[1]
    if w == 1:
        return np.zeros(block.shape[0])
    c = cur[:, None]
    less = np.sum(block < c, axis=1)
    equal = np.sum(block == c, axis=1) - 1
    r: FloatArray = 2.0 * (less + 0.5 * equal) / (w - 1) - 1.0
    return r


def _ema(x: FloatArray, w: int) -> FloatArray:
    """Semente no primeiro valor válido; ``NaN`` até acumular ``w`` valores."""
    alpha = 2.0 / (w + 1)
    out = np.full_like(x, np.nan)
    e = 0.0
    seen = 0
    for t in range(x.shape[0]):
        v = float(x[t])
        if math.isnan(v):
            if seen:
                raise DslEvalError("ema: NaN após o aquecimento")
            continue
        e = v if seen == 0 else e + alpha * (v - e)
        seen += 1
        if seen >= w:
            out[t] = e
    return out


# ---------------------------------------------------------------- aritmética


def _guarded_div(a: FloatArray, b: FloatArray) -> FloatArray:
    """``NaN`` se algum lado é ``NaN``; 0.0 se ``|b| < GUARD``; senão ``a/b``."""
    with np.errstate(invalid="ignore", divide="ignore"):
        q = np.where(np.abs(b) < GUARD, 0.0, a / b)
    nan = np.isnan(a) | np.isnan(b)
    return np.where(nan, np.nan, q)


def _ret(x: FloatArray, w: int) -> FloatArray:
    """``x[t]/x[t-w] - 1``; 0.0 se ``|x[t-w]| < GUARD``; ``NaN`` se algum lado é ``NaN``."""
    den = _shift(x, w)
    with np.errstate(invalid="ignore", divide="ignore"):
        r = np.where(np.abs(den) < GUARD, 0.0, x / den - 1.0)
    return np.where(np.isnan(x) | np.isnan(den), np.nan, r)


def _logic(a: FloatArray, b: FloatArray, value: FloatArray) -> FloatArray:
    return np.where(np.isnan(a) | np.isnan(b), np.nan, value)


# ---------------------------------------------------------------- sessão


def _in_window(frame: MarketFrame, h1: str, h2: str) -> FloatArray:
    """1.0 se a barra cabe inteira na janela: início < fechamento <= fim."""
    tod = frame.minute_of_day()
    lo, hi = parse_hhmm(h1), parse_hhmm(h2)
    return ((tod > lo) & (tod <= hi)).astype(np.float64)


def _minutes_since_open(frame: MarketFrame) -> FloatArray:
    """Minutos desde a abertura do pregão; a primeira barra do dia vale 1."""
    m = frame.epoch_minutes()
    day = frame.day_index()
    new_day = np.ones(m.shape[0], dtype=np.bool_)
    new_day[1:] = day[1:] != day[:-1]
    first = np.maximum.accumulate(np.where(new_day, np.arange(m.shape[0]), 0))
    return (m - m[first] + 1).astype(np.float64)


# ---------------------------------------------------------------- avaliação


def eval_vec(n: Node, data: MarketFrame) -> FloatArray:
    """Array do mesmo tamanho de ``data``, com ``NaN`` no aquecimento."""
    op = n.op
    if op is Op.CLOSE:
        return data.close.copy()
    if op is Op.HIGH:
        return data.high.copy()
    if op is Op.LOW:
        return data.low.copy()
    if op is Op.VOLUME:
        return data.volume.copy()
    if op is Op.TRADES:
        return data.trades.copy()
    if op is Op.VWAP:
        return data.vwap.copy()
    if op is Op.REF:
        sym = _str(n, 0)
        if sym not in data.refs:
            raise MarketDataError(f"frame sem a referência {sym}")
        return data.refs[sym].copy()
    if op is Op.CONST:
        return np.full(len(data), float(_int(n, 0)))
    if op is Op.IN_WINDOW:
        return _in_window(data, _str(n, 0), _str(n, 1))
    if op is Op.MINUTES_SINCE_OPEN:
        return _minutes_since_open(data)

    if op is Op.CLIP:
        x = eval_vec(_node(n, 0), data)
        return np.clip(x, float(_int(n, 1)), float(_int(n, 2)))

    if op in (
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
    ):
        x = eval_vec(_node(n, 0), data)
        w = _int(n, 1)
        if op is Op.LAG:
            return _shift(x, w)
        if op is Op.DELTA:
            return x - _shift(x, w)
        if op is Op.RET:
            return _ret(x, w)
        if op is Op.ROLLING_MEAN:
            return _rolling(x, w, _mean)
        if op is Op.ROLLING_STD:
            return _rolling(x, w, lambda b, _c: _std(b))
        if op is Op.ROLLING_MAX:
            return _rolling(x, w, lambda b, _c: np.max(b, axis=1))
        if op is Op.ROLLING_MIN:
            return _rolling(x, w, lambda b, _c: np.min(b, axis=1))
        if op is Op.EMA:
            return _ema(x, w)
        if op is Op.ZSCORE:
            return _rolling(x, w, _zscore)
        return _rolling(x, w, _rank)

    a = eval_vec(_node(n, 0), data)
    b = eval_vec(_node(n, 1), data)
    if op is Op.ADD:
        return a + b
    if op is Op.SUB:
        return a - b
    if op is Op.MUL:
        return a * b
    if op is Op.DIV:
        return _guarded_div(a, b)
    if op is Op.GT:
        return _logic(a, b, (a > b).astype(np.float64))
    if op is Op.LT:
        return _logic(a, b, (a < b).astype(np.float64))
    if op is Op.AND_:
        return _logic(a, b, ((a != 0) & (b != 0)).astype(np.float64))
    if op is Op.OR_:
        return _logic(a, b, ((a != 0) | (b != 0)).astype(np.float64))
    raise DslEvalError(f"operador sem avaliação: {op.value}")
