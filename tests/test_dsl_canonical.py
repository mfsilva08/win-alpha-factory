"""SPEC-fase-1 §1.4: forma canônica, serialização e assinaturas."""

from __future__ import annotations

import pytest

from src.dsl.ast import Node, Unit, const
from src.dsl.canonical import (
    WINDOW_BUCKETS,
    canonicalize,
    exact_signature,
    serialize,
    snap_to_bucket,
    structural_signature,
)
from src.dsl.ops import Op
from src.dsl.parser import parse, validate
from tests.dsl_factory import RANDOM_CONSTRAINTS, AstGen

H001 = 'mul(in_window("10:30","16:30"), sub(zscore(ret(ref("ES"),2),55), zscore(ret(close(),2),55)))'


def _h001_with_window(w: int) -> Node:
    return parse(H001.replace(",55)", f",{w})"))


# ---------------------------------------------------------------- buckets


def test_buckets_20_21_22_colidem_55_difere() -> None:
    sigs = {w: structural_signature(_h001_with_window(w)) for w in (20, 21, 22, 55)}
    assert sigs[20] == sigs[21] == sigs[22]
    assert sigs[55] != sigs[21]


def test_assinatura_exata_distingue_20_e_21() -> None:
    assert exact_signature(_h001_with_window(20)) != exact_signature(_h001_with_window(21))


@pytest.mark.parametrize(
    ("w", "bucket"),
    [(1, 1), (4, 3), (6, 5), (7, 8), (10, 8), (11, 13), (17, 13), (18, 21), (27, 21),
     (28, 34), (44, 34), (45, 55), (72, 55), (116, 89), (117, 144), (500, 144)],
)
def test_snap_to_bucket(w: int, bucket: int) -> None:
    # empates (4 entre 3 e 5, 17 entre 13 e 21, 72 entre 55 e 89) resolvem para o menor
    assert snap_to_bucket(w) == bucket


def test_buckets_sao_fixos() -> None:
    assert WINDOW_BUCKETS == (1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 144)


# ---------------------------------------------------------------- serialização


def test_serializacao_deterministica() -> None:
    n = parse('mul(in_window("10:30","16:30"), zscore(ret(ref("ES"),2),55))')
    assert serialize(n) == '(mul (in_window "10:30" "16:30") (zscore (ret (ref "ES") 2) 55))'


# ---------------------------------------------------------------- reescritas


def test_comutatividade() -> None:
    a = parse('add(zscore(close(),5), zscore(ref("ES"),5))')
    b = parse('add(zscore(ref("ES"),5), zscore(close(),5))')
    assert canonicalize(a) == canonicalize(b)
    assert exact_signature(a) == exact_signature(b)


def test_lt_vira_gt_invertido() -> None:
    a = parse("lt(zscore(close(),5), zscore(high(),5))")
    b = parse("gt(zscore(high(),5), zscore(close(),5))")
    assert canonicalize(a) == canonicalize(b)


def test_lag_zero() -> None:
    x = parse("zscore(close(),5)")
    assert canonicalize(Node(Op.LAG, (x, 0))) == x


def test_sub_x_x_vira_constante_com_unidade() -> None:
    assert canonicalize(parse("sub(zscore(close(),5), zscore(close(),5))")) == const(0, Unit.Z)
    assert canonicalize(parse("sub(close(), close())")) == const(0, Unit.PRICE)


def test_zscore_de_zscore() -> None:
    a = parse("zscore(zscore(ret(close(),2),21),21)")
    assert canonicalize(a) == parse("zscore(ret(close(),2),21)")
    b = parse("zscore(zscore(ret(close(),2),21),34)")
    assert canonicalize(b) == b


def test_div_mul() -> None:
    a, b = "zscore(close(),5)", 'zscore(ref("ES"),8)'
    assert canonicalize(parse(f"div(mul({a},{b}),{b})")) == canonicalize(parse(a))
    assert canonicalize(parse(f"div(mul({a},{b}),{a})")) == canonicalize(parse(b))


def test_dobra_de_constantes() -> None:
    z = "zscore(close(),5)"
    # sub(x,x) -> 0; add(y, 0) -> y
    n = parse(f'add(zscore(ref("ES"),5), sub({z},{z}))')
    assert canonicalize(n) == parse('zscore(ref("ES"),5)')
    # mul por zero
    assert canonicalize(parse(f"mul({z}, sub({z},{z}))")) == const(0, Unit.Z)
    # zscore de constante
    assert canonicalize(parse("zscore(sub(close(),close()),5)")) == const(0, Unit.Z)
    # comparação de x consigo mesmo: gt(x, x) não é reescrita, mas sub dentro sim
    assert canonicalize(parse(f"gt(sub({z},{z}), sub({z},{z}))")) == const(0, Unit.BOOL)


def test_agrupamento_expoe_redundancia() -> None:
    """``sub(zscore(x,20), zscore(x,21))`` só vira constante depois do agrupamento."""
    n = parse("sub(zscore(ret(close(),2),20), zscore(ret(close(),2),21))")
    assert canonicalize(n) != const(0, Unit.Z)
    assert structural_signature(n) == structural_signature(
        parse("sub(zscore(close(),5), zscore(close(),5))")
    )


# ---------------------------------------------------------------- idempotência


def test_idempotencia_em_10000_asts_aleatorias() -> None:
    gen = AstGen(seed=20260919)
    checked = 0
    changed = 0
    attempts = 0
    while checked < 10_000:
        attempts += 1
        assert attempts < 200_000, "gerador não produz ASTs válidas o bastante"
        n = gen.root()
        if not validate(n, RANDOM_CONSTRAINTS).ok:
            continue
        c1 = canonicalize(n)
        assert canonicalize(c1) == c1, serialize(n)
        assert exact_signature(c1) == exact_signature(n)
        s = structural_signature(n)
        assert structural_signature(c1) == s
        changed += c1 != n
        checked += 1
    # o gerador precisa de fato exercitar as reescritas
    assert changed > 2_000
