"""SPEC-fase-1 §1.5–1.6: semântica dos operadores e paridade vetorizado/incremental."""

from __future__ import annotations

import math
from datetime import datetime

import numpy as np
import pytest

from src.dsl.ast import Node
from src.dsl.errors import MarketDataError
from src.dsl.eval_incremental import IncrementalState
from src.dsl.eval_vectorized import eval_vec
from src.dsl.market import Bar, FloatArray, MarketFrame
from src.dsl.parser import parse
from tests.dsl_factory import BARS_PER_DAY, synthetic_frame

# ---------------------------------------------------------------- frame pequeno, vetores à mão


def small_frame(close: list[float], start: str = "2026-03-02T10:28") -> MarketFrame:
    n = len(close)
    ts = np.datetime64(start, "m") + np.arange(n)
    c = np.array(close)
    return MarketFrame(
        ts=ts, close=c, high=c + 5, low=c - 5, volume=np.arange(1.0, n + 1),
        trades=np.ones(n), vwap=c, refs={"ES": c / 20.0},
        session_mask=np.ones(n, dtype=bool), roll_day=np.zeros(n, dtype=bool),
    )


def run_incremental(n: Node, frame: MarketFrame) -> FloatArray:
    st = IncrementalState(n)
    out = np.empty(len(frame))
    for i, b in enumerate(frame.bars()):
        st.update(b)
        out[i] = st.signal()
    return out


def both(text: str, frame: MarketFrame) -> FloatArray:
    n = parse(text)
    v = eval_vec(n, frame)
    inc = run_incremental(n, frame)
    np.testing.assert_array_equal(np.isnan(v), np.isnan(inc))
    np.testing.assert_allclose(v, inc, rtol=0, atol=1e-12, equal_nan=True)
    return v


NAN = math.nan
PRICES = [100.0, 105.0, 95.0, 110.0, 110.0, 110.0, 120.0]


def test_lag_delta_ret() -> None:
    f = small_frame(PRICES)
    np.testing.assert_array_equal(both("lag(close(),2)", f), [NAN, NAN, 100, 105, 95, 110, 110])
    np.testing.assert_array_equal(both("delta(close(),1)", f), [NAN, 5, -10, 15, 0, 0, 10])
    np.testing.assert_allclose(
        both("ret(close(),1)", f), [NAN, 0.05, 95 / 105 - 1, 110 / 95 - 1, 0, 0, 120 / 110 - 1]
    )


def test_rolling() -> None:
    f = small_frame(PRICES)
    np.testing.assert_allclose(
        both("rolling_mean(close(),3)", f), [NAN, NAN, 100, 310 / 3, 315 / 3, 110, 340 / 3]
    )
    np.testing.assert_array_equal(both("rolling_max(close(),3)", f), [NAN, NAN, 105, 110, 110, 110, 120])
    np.testing.assert_array_equal(both("rolling_min(close(),3)", f), [NAN, NAN, 95, 95, 95, 110, 110])
    std = both("rolling_std(close(),3)", f)
    assert std[2] == pytest.approx(5.0)
    assert std[5] == 0.0  # janela constante: exatamente zero


def test_zscore_e_rank() -> None:
    f = small_frame(PRICES)
    z = both("zscore(close(),3)", f)
    assert z[3] == pytest.approx((110 - 310 / 3) / np.std([105, 95, 110], ddof=1))
    assert z[5] == 0.0  # desvio zero -> 0, nunca inf
    r = both("rank_ts(close(),3)", f)
    # [105,95,110] -> 110 é o maior: 2*(2)/2-1 = 1 ; [110,110,110] -> empate total: 0
    assert r[3] == 1.0 and r[5] == 0.0 and r[2] == -1.0


def test_ema() -> None:
    f = small_frame(PRICES)
    e = both("ema(close(),3)", f)
    a = 0.5
    x = 100.0
    for p in PRICES[1:3]:
        x = x + a * (p - x)
    assert np.isnan(e[1]) and e[2] == pytest.approx(x)


def test_div_guard_e_logica() -> None:
    f = small_frame(PRICES)
    d = both("div(delta(close(),1), delta(close(),1))", f)
    np.testing.assert_array_equal(d, [NAN, 1, 1, 1, 0, 0, 1])  # 0/0 -> 0
    g = both("gt(close(), lag(close(),1))", f)
    np.testing.assert_array_equal(g, [NAN, 1, 0, 1, 0, 0, 1])
    a = both("and_(gt(close(), lag(close(),1)), lt(close(), high()))", f)
    np.testing.assert_array_equal(a, [NAN, 1, 0, 1, 0, 0, 1])


def test_clip() -> None:
    f = small_frame(PRICES)
    c = both("clip(zscore(close(),2),0,0)", f)
    assert np.all(c[1:] == 0.0)


def test_in_window_intervalo_semiaberto() -> None:
    # fechamentos 10:28 .. 10:34; janela 10:30-10:32 aceita 10:31 e 10:32
    f = small_frame(PRICES)
    np.testing.assert_array_equal(both('in_window("10:30","10:32")', f), [0, 0, 0, 1, 1, 0, 0])


def test_minutes_since_open_reinicia_no_dia() -> None:
    f = synthetic_frame(n_days=3, roll_day_index=None)
    m = both("minutes_since_open()", f)
    assert m[0] == 1 and m[BARS_PER_DAY - 1] == BARS_PER_DAY
    assert m[BARS_PER_DAY] == 1 and m[2 * BARS_PER_DAY + 9] == 10


def test_ref_ausente() -> None:
    f = small_frame(PRICES)
    with pytest.raises(MarketDataError):
        eval_vec(parse('ref("WDO")'), f)
    with pytest.raises(MarketDataError):
        run_incremental(parse('ref("WDO")'), f)


def test_frame_recusa_nan_e_ordem() -> None:
    with pytest.raises(MarketDataError):
        small_frame([100.0, NAN, 101.0])
    st = IncrementalState(parse("close()"))
    b = Bar(datetime(2026, 3, 2, 10, 0), 1, 1, 1, 1, 1, 1)  # noqa: DTZ001 hora local da B3
    st.update(b)
    with pytest.raises(MarketDataError):
        st.update(b)


def test_warmup() -> None:
    assert IncrementalState(parse("zscore(ret(close(),2),55)")).warmup_bars() == 165


# ---------------------------------------------------------------- paridade

PARITY_FORMULAS = [
    # h001
    'mul(in_window("10:30","16:30"), sub(zscore(ret(ref("ES"),2),55), zscore(ret(close(),2),55)))',
    "zscore(rolling_std(close(),21),55)",
    "gt(rolling_mean(close(),21), ema(close(),34))",
    "rank_ts(delta(vwap(),5),34)",
    "clip(zscore(div(rolling_max(high(),13), rolling_min(low(),13)),89),-2,2)",
    (
        'and_(gt(volume(), rolling_mean(volume(),21)), or_(in_window("09:00","11:00"),'
        " lt(minutes_since_open(), lag(trades(),3))))"
    ),
    'mul(zscore(ret(ref("WDO"),5),55), zscore(sub(high(),low()),21))',
    'div(zscore(ret(close(),1),144), zscore(ret(ref("ES"),1),144))',
    "zscore(minutes_since_open(),8)",
    "zscore(clip(zscore(ret(close(),2),21),-1,1),21)",  # janelas saturadas: desvio zero
    "zscore(close(),144)",  # zscore sobre nível de preço
    "zscore(ema(rolling_mean(ret(close(),3),13),21),89)",
    'lag(gt(ret(close(),1), ret(ref("ES"),1)),1)',
    # intermediários sem raiz válida, só para estressar o estado incremental
    "rolling_mean(close(),21)",
    "rolling_std(close(),55)",
    "ema(close(),89)",
    "rolling_std(ret(close(),1),144)",
]


@pytest.fixture(scope="module")
def big_frame() -> MarketFrame:
    f = synthetic_frame(n_days=75)
    assert len(f) >= 40_000
    days = f.day_index()
    assert np.count_nonzero(days[1:] != days[:-1]) >= 70  # viradas de dia
    assert f.roll_day.any()  # dia de rolagem
    return f


@pytest.mark.parametrize("text", PARITY_FORMULAS)
def test_paridade_vetorizado_incremental(text: str, big_frame: MarketFrame) -> None:
    n = parse(text)
    vec = eval_vec(n, big_frame)
    st = IncrementalState(n)
    inc = np.empty(len(big_frame))
    for i, b in enumerate(big_frame.bars()):
        st.update(b)
        inc[i] = st.signal()
    np.testing.assert_array_equal(np.isnan(vec), np.isnan(inc))
    assert not np.isnan(vec[st.warmup_bars():]).any()
    finite = ~np.isnan(vec)
    diff = float(np.max(parity_error(vec[finite], inc[finite])))
    assert diff < 1e-9, f"divergência {diff:.3e}"


def parity_error(a: FloatArray, b: FloatArray) -> FloatArray:
    """|a−b| / max(1, |a|): absoluto para |sinal| ≤ 1, relativo acima (SPEC-fase-1 §1.6).

    ``div`` com denominador perto de zero produz sinais de módulo alto; ali 1e-9
    absoluto exigiria mais precisão do que o float64 tem.
    """
    r: FloatArray = np.abs(a - b) / np.maximum(1.0, np.abs(a))
    return r


def test_criterio_de_paridade() -> None:
    assert parity_error(np.array([0.5]), np.array([0.5 + 2e-9]))[0] > 1e-9
    assert parity_error(np.array([-9419.0]), np.array([-9419.0 + 5e-9]))[0] < 1e-9
