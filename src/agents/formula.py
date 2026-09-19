"""Agente de fórmula (SPEC-fase-3 §3.8, §3.10).

Entrada: ``ResearchView``, e nada além dela (via ``graph.call_agent``).
Saída: AST validada + assinatura exata + assinatura estrutural.

O modelo emite por tool use com schema montado **por tentativa** a partir das
``Constraints``: o ``op`` é um ``enum`` só com os operadores permitidos naquela
hipótese. Mesmo assim o código revalida tudo — o schema é a jaula, ``validate``
é o carcereiro.

Efeito do veredito anterior na tentativa seguinte (§3.10):

| Veredito                | Efeito                                              |
|-------------------------|-----------------------------------------------------|
| INVALID_AST             | detalhe completo volta; retry                       |
| TOO_COMPLEX             | ``max_depth`` cai em 1                              |
| REDUNDANT               | assinatura bloqueada; operador raiz banido          |
| UNSTABLE_ACROSS_FOLDS   | ``max_window`` cortado pela metade                  |
| COST_DOMINATED          | horizonte sobe um bucket, limitado à família        |
| PROOF_FAILED            | detalhe completo volta; retry                       |
| FAILED_GATE             | hipótese encerrada — não há próxima tentativa       |
"""

from __future__ import annotations

import dataclasses
import json
from datetime import timedelta
from typing import Any

from src.agents.schemas import Hypothesis
from src.catalog.families import Family
from src.dsl.ast import Constraints, Node
from src.dsl.canonical import WINDOW_BUCKETS, structural_signature
from src.dsl.errors import DslParseError
from src.dsl.ops import BASE_SERIES, Op
from src.dsl.parser import validate
from src.dsl.verdict import Verdict
from src.ledger.ledger import Kind, Ledger, LedgerRecord
from src.orchestrator.events import AgentClient
from src.orchestrator.graph import NodeFn, call_agent
from src.orchestrator.state import ProofStatus, TrialState

NODE = "formula"
TOOL_NAME = "emit_formula"


# ---------------------------------------------------------------- a jaula


def permitted_ops(c: Constraints) -> list[Op]:
    ops = (c.allowed_ops | BASE_SERIES) - c.forbidden_ops - {Op.CONST}
    return sorted(ops, key=lambda o: o.value)


def tool_schema(c: Constraints) -> dict[str, Any]:
    """Schema de tool use desta tentativa: ``op`` restrito ao permitido."""
    return {
        "name": TOOL_NAME,
        "description": "Emite a fórmula como árvore: cada nó tem op e args.",
        "input_schema": {
            "type": "object",
            "properties": {"formula": {"$ref": "#/$defs/node"}},
            "required": ["formula"],
            "additionalProperties": False,
            "$defs": {
                "node": {
                    "type": "object",
                    "properties": {
                        "op": {"type": "string", "enum": [o.value for o in permitted_ops(c)]},
                        "args": {
                            "type": "array",
                            "items": {"anyOf": [
                                {"$ref": "#/$defs/node"},
                                {"type": "integer"},
                                {"type": "string"},
                            ]},
                        },
                    },
                    "required": ["op", "args"],
                    "additionalProperties": False,
                }
            },
        },
    }


def ast_from_json(obj: Any, depth: int = 0) -> Node:
    """Converte o JSON do tool use em ``Node``. Levanta ``DslParseError``."""
    if depth > 64:
        raise DslParseError("fórmula aninhada demais")
    if not isinstance(obj, dict) or set(obj) - {"op", "args"} or "op" not in obj:
        raise DslParseError("nó deve ser objeto com 'op' e 'args'")
    try:
        op = Op(obj["op"])
    except ValueError:
        raise DslParseError(f"operador desconhecido: {obj['op']!r}") from None
    raw_args = obj.get("args", [])
    if not isinstance(raw_args, list):
        raise DslParseError("'args' deve ser lista")
    args: list[Node | int | str] = []
    for a in raw_args:
        if isinstance(a, dict):
            args.append(ast_from_json(a, depth + 1))
        elif isinstance(a, int) and not isinstance(a, bool) or isinstance(a, str):
            args.append(a)
        else:
            raise DslParseError(f"argumento inválido: {a!r}")
    return Node(op, tuple(args))


def parse_response(response: str) -> Node:
    try:
        raw = json.loads(response)
    except json.JSONDecodeError as e:
        raise DslParseError(f"JSON inválido: {e}") from None
    if not isinstance(raw, dict) or "formula" not in raw:
        raise DslParseError("resposta sem o campo 'formula'")
    return ast_from_json(raw["formula"])


# ---------------------------------------------------------------- realimentação


def _next_bucket_minutes(minutes: int) -> int:
    for b in WINDOW_BUCKETS:
        if b > minutes:
            return b
    return minutes


def feedback(st: TrialState, family: Family) -> dict[str, Any]:
    """Aperto das restrições para a próxima tentativa, a partir do veredito anterior."""
    c, h = st.constraints, st.hypothesis
    v = st.verdict
    if v is Verdict.TOO_COMPLEX:
        return {"constraints": dataclasses.replace(c, max_depth=max(c.max_depth - 1, 1))}
    if v is Verdict.UNSTABLE_ACROSS_FOLDS:
        return {"constraints": dataclasses.replace(c, max_window=max(c.max_window // 2, 2))}
    if v is Verdict.COST_DOMINATED:
        minutes = int(h.horizon.total_seconds() // 60)
        new = min(_next_bucket_minutes(minutes), family.horizon_range[1])
        return {"hypothesis": dataclasses.replace(h, horizon=timedelta(minutes=new))}
    return {}


def ban_redundant(c: Constraints, ast: Node) -> Constraints:
    """REDUNDANT: o operador raiz da fórmula repetida sai do permitido."""
    if ast.op in BASE_SERIES:
        return c
    return dataclasses.replace(c, forbidden_ops=c.forbidden_ops | {ast.op})


# ---------------------------------------------------------------- o nó


def record_verdict(ledger: Ledger, st: TrialState, verdict: Verdict, family: str) -> None:
    """Todo veredito vira ``kind='verdict'`` — é o que o bandit lê. Nunca conta tentativa."""
    g = ledger.genesis_info()
    ledger.append(LedgerRecord(
        trial_id=f"{st.trial_id}/{st.attempt}/{verdict.value}",
        kind=Kind.VERDICT,
        data_hash=g.data_hash,
        config_hash=g.config_hash,
        hypothesis_id=st.hypothesis.id,
        verdict=verdict,
        payload={"family": family, "attempt": st.attempt},
    ))


def make_node(client: AgentClient, ledger: Ledger, family: Family) -> NodeFn:
    def node(st: TrialState) -> dict[str, Any]:
        updates = feedback(st, family)
        cur = dataclasses.replace(st, attempt=st.attempt + 1, **updates)
        response, events = call_agent(cur, client, NODE)
        out: dict[str, Any] = {**updates, "attempt": cur.attempt, "events": events,
                               "proof_status": ProofStatus.NOT_STARTED, "metrics": None,
                               "verdict": None, "detail": None}
        try:
            ast = parse_response(response)
        except DslParseError as e:
            out.update(formula_ast=None, verdict=Verdict.INVALID_AST, detail=str(e))
        else:
            out["formula_ast"] = ast
            r = validate(ast, cur.constraints)
            if not r.ok:
                out.update(verdict=r.verdict, detail=r.detail)
            else:
                sig = structural_signature(ast)
                if sig in cur.blocked_sigs or ledger.seen_structural(sig):
                    out.update(verdict=Verdict.REDUNDANT,
                               blocked_sigs=cur.blocked_sigs | {sig},
                               constraints=ban_redundant(cur.constraints, ast))
        if out["verdict"] is not None:
            record_verdict(ledger, cur, out["verdict"], family.name)
        return out
    return node


def with_hypothesis(st: TrialState, h: Hypothesis) -> TrialState:
    return dataclasses.replace(st, hypothesis=h, constraints=h.dsl_constraints)
