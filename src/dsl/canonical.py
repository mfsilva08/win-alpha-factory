"""Forma normal, serialização e as duas assinaturas de uma AST.

A forma canônica existe para **identificar** fórmulas (assinaturas, REDUNDANT),
nunca para avaliá-las: algumas reescritas (``div(mul(a,b),b) -> a``,
``zscore(zscore(x,w),w) -> zscore(x,w)``) não preservam o valor numérico sob o
guard de divisão ou no aquecimento. O avaliador sempre roda a AST original.

``canonicalize`` pressupõe uma AST que passou por ``validate``.
"""

from __future__ import annotations

from hashlib import sha256

from src.dsl.ast import Node, Unit, const, infer_unit
from src.dsl.errors import CanonicalizationError
from src.dsl.ops import COMMUTATIVE_OPS, WINDOW_OPS, Op

WINDOW_BUCKETS: tuple[int, ...] = (1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 144)

_MAX_REWRITES = 10_000


# ---------------------------------------------------------------- serialização


def serialize(n: Node | int | str) -> str:
    """S-expression determinística: ``(mul (in_window "10:30" "16:30") (sub ...))``."""
    if isinstance(n, Node):
        if not n.args:
            return f"({n.op.value})"
        return "(" + n.op.value + " " + " ".join(serialize(a) for a in n.args) + ")"
    if isinstance(n, str):
        return f'"{n}"'
    return str(n)


def _hash(n: Node | int | str) -> str:
    return sha256(serialize(n).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- reescritas


def _const_value(n: Node | int | str) -> int | None:
    if isinstance(n, Node) and n.op is Op.CONST:
        v = n.args[0]
        assert isinstance(v, int)
        return v
    return None


def _is_true(n: Node | int | str) -> bool:
    return _const_value(n) == 1 and isinstance(n, Node) and n.args[1] == Unit.BOOL.value


def _fold_const(n: Node) -> Node | None:
    """Dobra de constantes e identidades com constante. ``None`` se nada se aplica."""
    op = n.op
    if op in WINDOW_OPS:
        x = n.args[0]
        c = _const_value(x)
        if c is None:
            return None
        assert isinstance(x, Node)
        if op in (Op.LAG, Op.ROLLING_MEAN, Op.ROLLING_MAX, Op.ROLLING_MIN, Op.EMA):
            return x
        return const(0, infer_unit(n))  # delta, ret, rolling_std, zscore, rank_ts
    if op is Op.CLIP:
        c = _const_value(n.args[0])
        if c is None:
            return None
        lo, hi = n.args[1], n.args[2]
        assert isinstance(lo, int) and isinstance(hi, int)
        return const(min(max(c, lo), hi), Unit.Z)
    if len(n.args) != 2 or op in (Op.IN_WINDOW, Op.CONST):
        return None
    a, b = n.args
    ca, cb = _const_value(a), _const_value(b)
    if ca is not None and cb is not None:
        unit = infer_unit(n)
        if op is Op.ADD:
            return const(ca + cb, unit)
        if op is Op.SUB:
            return const(ca - cb, unit)
        if op is Op.MUL:
            return const(ca * cb, unit)
        if op is Op.GT:
            return const(int(ca > cb), unit)
        if op is Op.AND_:
            return const(int(bool(ca) and bool(cb)), unit)
        if op is Op.OR_:
            return const(int(bool(ca) or bool(cb)), unit)
        return None  # div e lt (lt já vira gt antes)
    if op is Op.ADD:
        if ca == 0:
            return b if isinstance(b, Node) else None
        if cb == 0:
            return a if isinstance(a, Node) else None
    if op is Op.SUB and cb == 0 and isinstance(a, Node):
        return a
    if op is Op.MUL:
        if ca == 0 or cb == 0:
            return const(0, infer_unit(n))
        # mul(x, const(1, Bool)) -> x
        if _is_true(a) and isinstance(b, Node):
            return b
        if _is_true(b) and isinstance(a, Node):
            return a
    return None


def _rewrite(n: Node) -> Node:
    """Uma reescrita no topo de ``n``, cujos filhos já estão canônicos."""
    op, args = n.op, n.args
    if op in COMMUTATIVE_OPS:
        a, b = args
        if _hash(a) > _hash(b):
            return Node(op, (b, a))
    if op is Op.LT:
        a, b = args
        return Node(Op.GT, (b, a))
    if op is Op.LAG and args[1] == 0:
        x = args[0]
        assert isinstance(x, Node)
        return x
    if op is Op.SUB and args[0] == args[1]:
        x = args[0]
        assert isinstance(x, Node)
        return const(0, infer_unit(x))
    if op is Op.ZSCORE:
        inner = args[0]
        if isinstance(inner, Node) and inner.op is Op.ZSCORE and inner.args[1] == args[1]:
            return inner
    if op is Op.DIV:
        num, den = args
        if isinstance(num, Node) and num.op is Op.MUL:
            a, b = num.args
            if b == den and isinstance(a, Node):
                return a
            if a == den and isinstance(b, Node):
                return b
    folded = _fold_const(n)
    if folded is not None:
        return folded
    return n


def _canon(n: Node, budget: list[int]) -> Node:
    args = tuple(_canon(a, budget) if isinstance(a, Node) else a for a in n.args)
    cur = Node(n.op, args)
    while True:
        nxt = _rewrite(cur)
        if nxt == cur:
            return cur
        budget[0] -= 1
        if budget[0] <= 0:
            raise CanonicalizationError("reescritas não convergiram para ponto fixo")
        # toda reescrita devolve um nó cujos filhos já são canônicos,
        # então basta continuar reescrevendo o topo
        cur = nxt


def canonicalize(n: Node) -> Node:
    """Aplica as reescritas até ponto fixo. Idempotente por construção."""
    return _canon(n, [_MAX_REWRITES])


# ---------------------------------------------------------------- janelas


def snap_to_bucket(w: int) -> int:
    """Retorna o bucket mais próximo. Empate resolve para o menor."""
    return min(WINDOW_BUCKETS, key=lambda b: (abs(b - w), b))


def snap_windows(n: Node) -> Node:
    args: list[Node | int | str] = []
    for i, a in enumerate(n.args):
        if isinstance(a, Node):
            args.append(snap_windows(a))
        elif i == 1 and n.op in WINDOW_OPS and isinstance(a, int):
            args.append(snap_to_bucket(a))
        else:
            args.append(a)
    return Node(n.op, tuple(args))


# ---------------------------------------------------------------- assinaturas


def exact_signature(n: Node) -> str:
    return _hash(canonicalize(n))


def structural_signature(n: Node) -> str:
    """Janelas em buckets de Fibonacci antes do hash (R9).

    Recanonicaliza depois de agrupar: ``sub(zscore(x,20), zscore(x,21))`` vira
    ``sub(y, y)`` após o agrupamento e precisa colapsar para constante.
    """
    return _hash(canonicalize(snap_windows(canonicalize(n))))
