"""M9 — as invariantes de risco e do motor, por teste de propriedade.

Não é prova formal: é a verificação que roda hoje, sobre milhares de entradas
geradas. As provas em Lean (BUILD-ORDER M9, passos 1 e 2) seguem pendentes e
devem provar exatamente estes enunciados.

Invariantes de risco (sobre ``codegen/guards.py``, espelhado no ``.mq5``):

1. Nenhuma ordem de abertura depois do cutoff ou fora da sessão
2. Nenhuma ordem com tamanho acima da margem disponível
3. Todo preço enviado está na grade, arredondado contra nós, a menos de um tick

Invariantes do motor:

4. Causalidade: o sinal em t não depende de nenhuma barra depois de t
5. Idempotência da forma canônica (já coberta em 10.000 ASTs em test_dsl_canonical)
"""

from __future__ import annotations

import math
import re
from pathlib import Path

import numpy as np
from hypothesis import given, settings
from hypothesis import strategies as st

from src.codegen.guards import (
    GuardLimits,
    decide,
    guard_order,
    max_lots,
    must_flatten,
    on_grid,
    to_grid,
)
from src.codegen.telemetry import Decision, GuardBlock
from src.dsl.eval_vectorized import eval_vec
from src.dsl.market import MarketFrame
from src.dsl.parser import validate
from tests.dsl_factory import RANDOM_CONSTRAINTS, AstGen, synthetic_frame

ROOT = Path(__file__).resolve().parents[1]

limits = st.builds(
    GuardLimits,
    cutoff_min=st.integers(9 * 60, 18 * 60),
    session=st.tuples(st.integers(8 * 60, 12 * 60), st.integers(12 * 60, 18 * 60 + 30)),
    tick_size=st.sampled_from([1, 5, 10]),
    margin_per_contract=st.floats(10.0, 5_000.0),
    daily_loss_points=st.floats(1.0, 5_000.0),
    max_rejections=st.integers(1, 10),
)
order = st.fixed_dictionaries({
    "tod": st.integers(0, 24 * 60 - 1),
    "lots": st.integers(-2, 50),
    "equity": st.floats(0.0, 1e6),
    "price": st.floats(1_000.0, 300_000.0),
    "pnl": st.floats(-10_000.0, 10_000.0),
    "rej": st.integers(0, 12),
})


@settings(max_examples=3000, deadline=None)
@given(lim=limits, o=order)
def test_invariantes_de_risco(lim: GuardLimits, o: dict[str, float]) -> None:
    tod, lots = int(o["tod"]), int(o["lots"])
    g = guard_order(lim, tod, lots, o["equity"], o["price"], o["pnl"], int(o["rej"]))
    if g is None:
        # 1. nunca depois do cutoff, nunca fora da sessão
        assert tod < lim.cutoff_min
        assert lim.session[0] < tod <= lim.session[1]
        # 2. nunca acima da margem
        assert 1 <= lots <= max_lots(o["equity"], lim.margin_per_contract)
        assert lots * lim.margin_per_contract <= o["equity"] + 1e-6
        # 3. preço na grade
        assert on_grid(o["price"], lim.tick_size)
        # e os freios de dia ruim
        assert o["pnl"] > -lim.daily_loss_points and o["rej"] < lim.max_rejections
    if tod >= lim.cutoff_min:
        assert g is not None


@settings(max_examples=3000, deadline=None)
@given(price=st.floats(1_000.0, 300_000.0), tick=st.sampled_from([1, 5, 10]),
       side=st.sampled_from([1, -1]))
def test_grade_contra_nos(price: float, tick: int, side: int) -> None:
    p = to_grid(price, tick, side)
    assert on_grid(p, tick)
    assert abs(p - price) < tick + 1e-9
    if side > 0:
        assert p >= price - 1e-6  # compra: nunca abaixo do preço visto
    else:
        assert p <= price + 1e-6  # venda: nunca acima


@settings(max_examples=2000, deadline=None)
@given(lim=limits, tod=st.integers(0, 24 * 60 - 1), held=st.integers(0, 600),
       max_hold=st.integers(1, 240))
def test_posicao_nunca_atravessa_o_cutoff(lim: GuardLimits, tod: int, held: int,
                                          max_hold: int) -> None:
    if tod >= lim.cutoff_min or held >= max_hold:
        assert must_flatten(tod, held, lim, max_hold)


@settings(max_examples=2000, deadline=None)
@given(sig=st.one_of(st.none(), st.floats(allow_nan=True, allow_infinity=False)),
       th=st.floats(0.0, 5.0), direction=st.sampled_from([1, -1]),
       pos=st.integers(-1, 1))
def test_decisao_so_abre_zerado_e_nunca_com_nan(sig: float | None, th: float, direction: int,
                                                pos: int) -> None:
    d = decide(sig, th, direction, pos)
    if pos != 0 or sig is None or math.isnan(sig):
        assert d is Decision.NONE
    if d is Decision.OPEN_LONG:
        assert sig is not None and direction * sig > th
    if d is Decision.OPEN_SHORT:
        assert sig is not None and direction * sig < -th


def test_template_verifica_na_mesma_ordem_que_guards_py() -> None:
    tpl = (ROOT / "src" / "codegen" / "templates" / "robo.mq5.j2").read_text(encoding="utf-8")
    body = tpl.split("string GuardOrder(", 1)[1].split("\n  }", 1)[0]
    in_template = re.findall(r'return "([A-Z_]+)"', body)
    py = (ROOT / "src" / "codegen" / "guards.py").read_text(encoding="utf-8")
    in_python = re.findall(r"return GuardBlock\.([A-Z_]+)", py.split("def guard_order(", 1)[1])
    assert in_template == in_python == ["BREAKER", "DAILY_LOSS", "CUTOFF", "MARGIN", "GRID"]
    assert GuardBlock.DATA_GAP.value == "DATA_GAP"


# ---------------------------------------------------------------- causalidade do motor


def head(frame: MarketFrame, n: int) -> MarketFrame:
    """As primeiras ``n`` barras: o que o robô conhecia naquele instante."""
    return MarketFrame(
        ts=frame.ts[:n], close=frame.close[:n], high=frame.high[:n], low=frame.low[:n],
        volume=frame.volume[:n], trades=frame.trades[:n], vwap=frame.vwap[:n],
        refs={k: v[:n] for k, v in frame.refs.items()},
        session_mask=frame.session_mask[:n], roll_day=frame.roll_day[:n],
    )


def test_causalidade_em_200_asts_aleatorias() -> None:
    frame = synthetic_frame(n_days=3, roll_day_index=1)
    gen = AstGen(seed=9)
    checked = 0
    cuts = (60, 541, 1000, 1500)
    while checked < 200:
        ast = gen.root()
        if not validate(ast, RANDOM_CONSTRAINTS).ok:
            continue
        full = eval_vec(ast, frame)
        for n in cuts:
            part = eval_vec(ast, head(frame, n))
            np.testing.assert_array_equal(np.isnan(part), np.isnan(full[:n]))
            np.testing.assert_allclose(part, full[:n], rtol=1e-12, atol=1e-12, equal_nan=True)
        checked += 1
