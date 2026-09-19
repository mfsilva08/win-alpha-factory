"""SPEC-fase-1 §1.2–1.3: tipagem, validação e leitura da notação textual."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src.dsl.ast import Constraints, Node, Unit, depth, free_params, infer_unit, node_count
from src.dsl.errors import DslParseError
from src.dsl.ops import Op
from src.dsl.parser import parse, validate
from src.dsl.verdict import Verdict

DOCS = Path(__file__).resolve().parents[1] / "docs"

H001_FORMULA = """
mul(
  in_window("10:30", "16:30"),
  sub(
    zscore(ret(ref("ES"), 2), 55),
    zscore(ret(close(), 2), 55)
  )
)
"""

# As restrições dos vetores da spec: as da h001, acrescidas dos operadores que
# os casos negativos usam, para que cada um falhe pelo motivo que pretende testar.
SPEC_CONSTRAINTS = Constraints(
    allowed_ops=frozenset(
        {Op.ZSCORE, Op.RET, Op.LAG, Op.ROLLING_STD, Op.SUB, Op.MUL, Op.IN_WINDOW, Op.REF,
         Op.ADD, Op.GT, Op.ROLLING_MEAN, Op.EMA}
    ),
    forbidden_ops=frozenset({Op.RANK_TS}),
    max_window=120,
    max_depth=4,
    max_nodes=12,
    max_free_params=3,
)

NEGATIVE_VECTORS = [
    ('sub(close(), ref("ES"))', Verdict.INVALID_AST),  # raiz em Price
    ("add(close(), volume())", Verdict.INVALID_AST),  # unidades diferentes
    ("gt(close(), volume())", Verdict.INVALID_AST),  # comparação de unidades diferentes
    ("zscore(ret(close(),2), 500)", Verdict.INVALID_AST),  # janela > max_window
    (  # profundidade 6 com max_depth 4
        (
            "mul(gt(volume(), rolling_mean(volume(),21)),"
            "    sub(zscore(ema(ret(lag(close(),1),2),8),21),"
            '        zscore(ema(ret(lag(ref("ES"),1),2),8),21)))'
        ),
        Verdict.TOO_COMPLEX,
    ),
]


@pytest.mark.parametrize(("text", "expected"), NEGATIVE_VECTORS)
def test_casos_negativos_da_spec(text: str, expected: Verdict) -> None:
    result = validate(parse(text), SPEC_CONSTRAINTS)
    assert result.verdict is expected


def test_quinto_vetor_tem_profundidade_6() -> None:
    assert depth(parse(NEGATIVE_VECTORS[4][0])) == 6


def test_formula_h001_aceita() -> None:
    n = parse(H001_FORMULA)
    assert depth(n) == 4
    assert free_params(n) == 2
    assert node_count(n) == 9
    assert infer_unit(n) is Unit.Z
    assert validate(n, SPEC_CONSTRAINTS).ok


def test_formula_h001_passa_nas_restricoes_do_yaml() -> None:
    h = yaml.safe_load((DOCS / "exemplos" / "h001.yaml").read_text(encoding="utf-8"))
    dc = h["dsl_constraints"]
    c = Constraints(
        allowed_ops=frozenset(Op[o] for o in dc["allowed_ops"]),
        forbidden_ops=frozenset(Op[o] for o in dc["forbidden_ops"]),
        max_window=dc["max_window"],
        max_depth=dc["max_depth"],
        max_nodes=dc["max_nodes"],
        max_free_params=dc["max_free_params"],
        required_refs=frozenset(dc["required_refs"]),
        session_mask=tuple(dc["session_mask"]),
    )
    assert validate(parse(H001_FORMULA), c).ok


def test_series_base_sempre_permitidas_salvo_se_proibidas() -> None:
    c = Constraints(frozenset({Op.ZSCORE}), frozenset(), 120, 4)
    assert validate(parse("zscore(close(), 21)"), c).ok
    proibida = Constraints(frozenset({Op.ZSCORE}), frozenset({Op.CLOSE}), 120, 4)
    assert validate(parse("zscore(close(), 21)"), proibida).verdict is Verdict.INVALID_AST


def test_operador_fora_do_permitido() -> None:
    c = Constraints(frozenset({Op.ZSCORE}), frozenset(), 120, 4)
    r = validate(parse("zscore(ema(close(), 5), 21)"), c)
    assert r.verdict is Verdict.INVALID_AST
    assert r.detail is not None and "ema" in r.detail


def test_const_nunca_aceito_na_entrada() -> None:
    n = Node(Op.GT, (Node(Op.CONST, (1, "Z")), Node(Op.ZSCORE, (Node(Op.CLOSE), 5))))
    assert validate(n, SPEC_CONSTRAINTS).verdict is Verdict.INVALID_AST


def test_mul_aceita_bool_nos_dois_lados() -> None:
    a = parse('mul(in_window("10:00","11:00"), zscore(close(), 5))')
    b = parse('mul(zscore(close(), 5), in_window("10:00","11:00"))')
    assert infer_unit(a) is Unit.Z and infer_unit(b) is Unit.Z


@pytest.mark.parametrize(
    "text",
    [
        "zscore(close())",  # aridade
        "zscore(close(), close())",  # janela não inteira
        'ref("NASDAQ")',  # símbolo desconhecido
        'in_window("16:30", "10:30")',  # janela invertida
        'in_window("25:00", "26:00")',  # horário inválido
        "clip(zscore(close(),5), 2, 1)",  # limites invertidos
        "clip(close(), -1, 1)",  # clip só aceita Z
        "div(close(), volume())",  # div de unidades mistas
        "zscore(gt(close(), high()), 5)",  # janela sobre Bool
    ],
)
def test_malformadas_sao_invalid_ast(text: str) -> None:
    c = Constraints(frozenset(op for op in Op if op is not Op.CONST), frozenset(), 120, 8, 20)
    assert validate(parse(text), c).verdict is Verdict.INVALID_AST


def test_janela_zero_rejeitada() -> None:
    assert validate(parse("zscore(close(), 0)"), SPEC_CONSTRAINTS).verdict is Verdict.INVALID_AST


def test_required_refs() -> None:
    c = Constraints(frozenset({Op.ZSCORE, Op.REF}), frozenset(), 120, 4, required_refs=frozenset({"ES"}))
    assert validate(parse("zscore(close(), 5)"), c).verdict is Verdict.INVALID_AST
    assert validate(parse('zscore(ref("ES"), 5)'), c).ok


def test_ordem_das_checagens_complexidade() -> None:
    c = Constraints(frozenset({Op.ZSCORE, Op.ADD}), frozenset(), 120, 4, max_nodes=3,
                    max_free_params=1)
    too_many_nodes = parse("add(zscore(close(),5), zscore(high(),5))")  # 5 nós
    assert validate(too_many_nodes, c).verdict is Verdict.TOO_COMPLEX
    c2 = Constraints(frozenset({Op.ZSCORE, Op.ADD}), frozenset(), 120, 4, max_free_params=1)
    two_windows = parse("add(zscore(close(),5), zscore(close(),8))")
    assert validate(two_windows, c2).verdict is Verdict.TOO_COMPLEX
    same_window = parse("add(zscore(close(),5), zscore(high(),5))")
    assert validate(same_window, c2).ok


def test_too_complex_nao_carrega_detalhe() -> None:
    r = validate(parse(NEGATIVE_VECTORS[4][0]), SPEC_CONSTRAINTS)
    assert r.detail is None


@pytest.mark.parametrize(
    "text", ["", "close(", "close() close()", "foo()", 'ref("ES"', "zscore(close(), 2.5)"]
)
def test_parse_erros(text: str) -> None:
    with pytest.raises(DslParseError):
        parse(text)
