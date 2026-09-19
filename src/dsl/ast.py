"""Nós, unidades, restrições e a tabela de tipagem da DSL.

A tabela de tipagem está em SPEC-fase-1 §1.2. ``infer_unit`` é a implementação
exata dela; o ``validate`` do parser e a canonicalização dependem desta função.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum

from src.dsl.errors import DslTypeError
from src.dsl.ops import ARG_KINDS, REF_SYMBOLS, WINDOW_OPS, ArgKind, Op


class Unit(StrEnum):
    PRICE = "Price"  # pontos do WIN
    RETURN = "Return"  # adimensional
    VOLUME = "Volume"  # contratos
    Z = "Z"  # adimensional normalizado
    BOOL = "Bool"  # {0,1}
    WINDOW = "Window"  # inteiro positivo


NUMERIC_UNITS: frozenset[Unit] = frozenset({Unit.PRICE, Unit.RETURN, Unit.VOLUME, Unit.Z})
VALUE_UNITS: frozenset[Unit] = NUMERIC_UNITS | {Unit.BOOL}
ROOT_UNITS: frozenset[Unit] = frozenset({Unit.Z, Unit.BOOL})


@dataclass(frozen=True)
class Node:
    op: Op
    args: tuple[Node | int | str, ...] = ()


@dataclass(frozen=True)
class Constraints:
    allowed_ops: frozenset[Op]
    forbidden_ops: frozenset[Op]
    max_window: int
    max_depth: int
    max_nodes: int = 12
    max_free_params: int = 3
    required_refs: frozenset[str] = frozenset()
    session_mask: tuple[str, str] | None = None


# ---------------------------------------------------------------- construtores


def const(value: int, unit: Unit) -> Node:
    """Nó constante. Só a canonicalização deve criar um."""
    return Node(Op.CONST, (value, unit.value))


# ---------------------------------------------------------------- percursos


def child_nodes(n: Node) -> tuple[Node, ...]:
    return tuple(a for a in n.args if isinstance(a, Node))


def iter_nodes(n: Node) -> Iterator[Node]:
    """Pré-ordem sobre todos os nós (argumentos int/str não são nós)."""
    yield n
    for c in child_nodes(n):
        yield from iter_nodes(c)


def depth(n: Node) -> int:
    """Profundidade em arestas: um nó sem filhos tem profundidade 0.

    ``mul(in_window(..), sub(zscore(ret(ref("ES"),2),55), ...))`` tem profundidade 4.
    """
    kids = child_nodes(n)
    return 0 if not kids else 1 + max(depth(c) for c in kids)


def node_count(n: Node) -> int:
    return sum(1 for _ in iter_nodes(n))


def window_of(n: Node) -> int | None:
    """A janela de um operador de janela, ou ``None``."""
    if n.op in WINDOW_OPS and len(n.args) == 2:
        w = n.args[1]
        if isinstance(w, int) and not isinstance(w, bool):
            return w
    return None


def windows(n: Node) -> list[int]:
    """Todas as ocorrências de janela, em pré-ordem."""
    out: list[int] = []
    for m in iter_nodes(n):
        w = window_of(m)
        if w is not None:
            out.append(w)
    return out


def free_params(n: Node) -> int:
    """Janelas distintas. ``zscore(ret(x,2),55) - zscore(ret(y,2),55)`` tem 2."""
    return len(set(windows(n)))


def max_window_of(n: Node) -> int:
    ws = windows(n)
    return max(ws) if ws else 0


def refs(n: Node) -> frozenset[str]:
    return frozenset(
        str(m.args[0]) for m in iter_nodes(n) if m.op is Op.REF and len(m.args) == 1
    )


# ---------------------------------------------------------------- checagem de aridade


def check_arg_kinds(n: Node) -> None:
    """Checagem 2: aridade e natureza (nó, janela, inteiro, texto) de cada argumento."""
    for m in iter_nodes(n):
        kinds = ARG_KINDS[m.op]
        if len(m.args) != len(kinds):
            raise DslTypeError(
                f"{m.op.value}: espera {len(kinds)} argumento(s), recebeu {len(m.args)}"
            )
        for i, (arg, kind) in enumerate(zip(m.args, kinds, strict=True)):
            if kind is ArgKind.NODE:
                ok = isinstance(arg, Node)
            elif kind in (ArgKind.WINDOW, ArgKind.INT):
                ok = isinstance(arg, int) and not isinstance(arg, bool)
            else:
                ok = isinstance(arg, str)
            if not ok:
                raise DslTypeError(
                    f"{m.op.value}: argumento {i + 1} deve ser {kind.value}, "
                    f"recebeu {type(arg).__name__}"
                )


# ---------------------------------------------------------------- tipagem

_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def parse_hhmm(s: str) -> int:
    """'HH:MM' -> minutos desde a meia-noite."""
    m = _HHMM.match(s)
    if m is None:
        raise DslTypeError(f'horário inválido "{s}", esperado "HH:MM"')
    return int(m.group(1)) * 60 + int(m.group(2))


_PRICE_SERIES = frozenset({Op.CLOSE, Op.HIGH, Op.LOW, Op.VWAP})
_VOLUME_SERIES = frozenset({Op.VOLUME, Op.TRADES, Op.MINUTES_SINCE_OPEN})
_PRESERVING = frozenset(
    {Op.DELTA, Op.ROLLING_MEAN, Op.ROLLING_STD, Op.ROLLING_MAX, Op.ROLLING_MIN, Op.EMA}
)


def _node_arg(n: Node, i: int) -> Node:
    a = n.args[i]
    if not isinstance(a, Node):
        raise DslTypeError(f"{n.op.value}: argumento {i + 1} deve ser um nó")
    return a


def _int_arg(n: Node, i: int) -> int:
    a = n.args[i]
    if not isinstance(a, int) or isinstance(a, bool):
        raise DslTypeError(f"{n.op.value}: argumento {i + 1} deve ser inteiro")
    return a


def _str_arg(n: Node, i: int) -> str:
    a = n.args[i]
    if not isinstance(a, str):
        raise DslTypeError(f"{n.op.value}: argumento {i + 1} deve ser texto")
    return a


def _mismatch(n: Node, *units: Unit) -> DslTypeError:
    got = ", ".join(u.value for u in units)
    return DslTypeError(f"{n.op.value}: combinação de unidades inválida ({got})")


def infer_unit(n: Node) -> Unit:
    """Unidade de saída de ``n`` segundo a tabela da SPEC-fase-1 §1.2.

    Levanta ``DslTypeError`` na primeira incompatibilidade encontrada.
    """
    op = n.op
    if op in _PRICE_SERIES:
        return Unit.PRICE
    if op in _VOLUME_SERIES:
        return Unit.VOLUME
    if op is Op.REF:
        sym = _str_arg(n, 0)
        if sym not in REF_SYMBOLS:
            raise DslTypeError(f'ref: símbolo "{sym}" fora de {sorted(REF_SYMBOLS)}')
        return Unit.PRICE
    if op is Op.CONST:
        _int_arg(n, 0)
        u = _str_arg(n, 1)
        try:
            unit = Unit(u)
        except ValueError:
            raise DslTypeError(f'const: unidade desconhecida "{u}"') from None
        if unit not in VALUE_UNITS:
            raise DslTypeError(f"const: unidade {u} não é de valor")
        return unit
    if op is Op.IN_WINDOW:
        h1 = parse_hhmm(_str_arg(n, 0))
        h2 = parse_hhmm(_str_arg(n, 1))
        if h1 >= h2:
            raise DslTypeError("in_window: início deve ser anterior ao fim")
        return Unit.BOOL

    if op in WINDOW_OPS:
        x = infer_unit(_node_arg(n, 0))
        _int_arg(n, 1)
        if op is Op.LAG:
            if x not in VALUE_UNITS:
                raise _mismatch(n, x)
            return x
        if x not in NUMERIC_UNITS:
            raise _mismatch(n, x)
        if op in _PRESERVING:
            return x
        if op is Op.RET:
            if x is not Unit.PRICE:
                raise _mismatch(n, x)
            return Unit.RETURN
        return Unit.Z  # zscore, rank_ts

    if op is Op.CLIP:
        x = infer_unit(_node_arg(n, 0))
        lo, hi = _int_arg(n, 1), _int_arg(n, 2)
        if x is not Unit.Z:
            raise _mismatch(n, x)
        if lo > hi:
            raise DslTypeError("clip: limite inferior maior que o superior")
        return Unit.Z

    a = infer_unit(_node_arg(n, 0))
    b = infer_unit(_node_arg(n, 1))
    if op in (Op.ADD, Op.SUB):
        if a != b or a not in NUMERIC_UNITS:
            raise _mismatch(n, a, b)
        return a
    if op is Op.MUL:
        # (T, Bool) -> T em qualquer ordem, ou (Z, Z) -> Z
        if a is Unit.BOOL:
            return b
        if b is Unit.BOOL:
            return a
        if a is Unit.Z and b is Unit.Z:
            return Unit.Z
        raise _mismatch(n, a, b)
    if op is Op.DIV:
        if a == b and a in (Unit.PRICE, Unit.VOLUME):
            return Unit.RETURN
        if a is Unit.Z and b is Unit.Z:
            return Unit.Z
        raise _mismatch(n, a, b)
    if op in (Op.GT, Op.LT):
        if a != b or a not in NUMERIC_UNITS:
            raise _mismatch(n, a, b)
        return Unit.BOOL
    if op in (Op.AND_, Op.OR_):
        if a is not Unit.BOOL or b is not Unit.BOOL:
            raise _mismatch(n, a, b)
        return Unit.BOOL
    raise DslTypeError(f"operador sem regra de tipagem: {op.value}")
