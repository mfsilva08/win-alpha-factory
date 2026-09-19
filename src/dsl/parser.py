"""Leitura da notação textual da DSL e validação de uma AST contra ``Constraints``.

Notação: ``mul(in_window("10:30","16:30"), sub(zscore(ret(ref("ES"),2),55), ...))``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from src.dsl.ast import (
    ROOT_UNITS,
    Constraints,
    Node,
    check_arg_kinds,
    depth,
    free_params,
    infer_unit,
    iter_nodes,
    node_count,
    refs,
    windows,
)
from src.dsl.errors import DslParseError, DslTypeError
from src.dsl.ops import BASE_SERIES, Op
from src.dsl.verdict import Verdict

# ---------------------------------------------------------------- leitura

_TOKEN = re.compile(
    r"""\s*(?:
        (?P<ident>[a-z_][a-z_0-9]*) |
        (?P<int>-?\d+) |
        "(?P<str>[^"\\]*)" |
        (?P<punct>[(),])
    )""",
    re.VERBOSE,
)


def _tokenize(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    pos = 0
    while pos < len(text):
        if text[pos:].strip() == "":
            break
        m = _TOKEN.match(text, pos)
        if m is None or m.end() == pos:
            raise DslParseError(f"caractere inesperado na posição {pos}: {text[pos:pos + 10]!r}")
        kind = m.lastgroup
        assert kind is not None
        out.append((kind, m.group(kind)))
        pos = m.end()
    return out


class _Reader:
    def __init__(self, tokens: list[tuple[str, str]]) -> None:
        self.tokens = tokens
        self.i = 0

    def peek(self) -> tuple[str, str] | None:
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def take(self) -> tuple[str, str]:
        t = self.peek()
        if t is None:
            raise DslParseError("fim inesperado da fórmula")
        self.i += 1
        return t

    def expect(self, value: str) -> None:
        kind, v = self.take()
        if kind != "punct" or v != value:
            raise DslParseError(f"esperado '{value}', encontrado {v!r}")

    def node(self) -> Node:
        kind, name = self.take()
        if kind != "ident":
            raise DslParseError(f"esperado nome de operador, encontrado {name!r}")
        try:
            op = Op(name)
        except ValueError:
            raise DslParseError(f"operador desconhecido: {name}") from None
        self.expect("(")
        args: list[Node | int | str] = []
        nxt = self.peek()
        if nxt == ("punct", ")"):
            self.take()
            return Node(op, ())
        while True:
            args.append(self.arg())
            kind, v = self.take()
            if (kind, v) == ("punct", ")"):
                return Node(op, tuple(args))
            if (kind, v) != ("punct", ","):
                raise DslParseError(f"esperado ',' ou ')', encontrado {v!r}")

    def arg(self) -> Node | int | str:
        t = self.peek()
        if t is None:
            raise DslParseError("fim inesperado da fórmula")
        kind, v = t
        if kind == "int":
            self.take()
            return int(v)
        if kind == "str":
            self.take()
            return v
        return self.node()


def parse(text: str) -> Node:
    """Converte a notação textual em ``Node``. Não valida tipos nem restrições."""
    reader = _Reader(_tokenize(text))
    n = reader.node()
    if reader.peek() is not None:
        raise DslParseError(f"texto sobrando após a fórmula: {reader.peek()!r}")
    return n


# ---------------------------------------------------------------- validação


@dataclass(frozen=True)
class ValidationResult:
    """``verdict is None`` significa AST válida.

    ``detail`` só é preenchido para ``INVALID_AST`` (R1: verdade sintática).
    """

    verdict: Verdict | None
    detail: str | None = None

    @property
    def ok(self) -> bool:
        return self.verdict is None


_OK = ValidationResult(None)


def _invalid(detail: str) -> ValidationResult:
    return ValidationResult(Verdict.INVALID_AST, detail)


def _too_complex() -> ValidationResult:
    return ValidationResult(Verdict.TOO_COMPLEX)


def validate(node: Node, c: Constraints) -> ValidationResult:
    """As nove checagens da SPEC-fase-1 §1.3, nesta ordem, parando no primeiro erro."""
    # 1. vocabulário
    for m in iter_nodes(node):
        if m.op is Op.CONST:
            return _invalid("const é interno à canonicalização e não pode ser emitido")
        if m.op in c.forbidden_ops:
            return _invalid(f"operador proibido nesta hipótese: {m.op.value}")
        if m.op not in c.allowed_ops and m.op not in BASE_SERIES:
            return _invalid(f"operador fora do permitido nesta hipótese: {m.op.value}")
    # 2. aridade
    try:
        check_arg_kinds(node)
    except DslTypeError as e:
        return _invalid(str(e))
    # 3. unidades
    try:
        root_unit = infer_unit(node)
    except DslTypeError as e:
        return _invalid(str(e))
    # 4. raiz
    if root_unit not in ROOT_UNITS:
        return _invalid(f"a raiz deve ter unidade Z ou Bool, tem {root_unit.value}")
    # 5. janelas
    for w in windows(node):
        if not 1 <= w <= c.max_window:
            return _invalid(f"janela {w} fora de [1, {c.max_window}]")
    # 6. referências exigidas
    missing = c.required_refs - refs(node)
    if missing:
        return _invalid(f"referência exigida ausente: {sorted(missing)}")
    # 7-9. complexidade
    if depth(node) > c.max_depth:
        return _too_complex()
    if node_count(node) > c.max_nodes:
        return _too_complex()
    if free_params(node) > c.max_free_params:
        return _too_complex()
    return _OK
