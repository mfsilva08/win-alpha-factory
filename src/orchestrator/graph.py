"""O grafo de estados de uma tentativa (SPEC-fase-3 §3.5).

Os nós são injetados (``Nodes``): o grafo só conhece a topologia e as rotas puras
de ``router``. Os nós reais de hipótese, fórmula e codegen chegam no M6 e no M7.

Toda chamada a modelo passa por ``call_agent``, que monta o payload **só** por
``projection.research_payload`` e grava o ``Event`` para replay.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from langgraph.graph import END, StateGraph

from src.orchestrator.events import AgentClient, Event, record
from src.orchestrator.projection import research_payload
from src.orchestrator.router import route_formula, route_gate, route_proof
from src.orchestrator.state import TrialState

NodeFn = Callable[[TrialState], dict[str, Any]]


@dataclass(frozen=True)
class Nodes:
    hypothesis: NodeFn
    formula: NodeFn
    proof: NodeFn
    backtest: NodeFn
    gate: NodeFn
    codegen: NodeFn


def build_graph(nodes: Nodes) -> Any:
    g = StateGraph(TrialState)
    for name in ("hypothesis", "formula", "proof", "backtest", "gate", "codegen"):
        g.add_node(name, getattr(nodes, name))
    g.set_entry_point("hypothesis")
    g.add_edge("hypothesis", "formula")
    g.add_edge("backtest", "gate")
    g.add_edge("codegen", END)
    g.add_conditional_edges(
        "formula", route_formula, {"proof": "proof", "retry": "formula", "abandon": END}
    )
    g.add_conditional_edges(
        "proof", route_proof, {"backtest": "backtest", "retry": "formula", "abandon": END}
    )
    g.add_conditional_edges(
        "gate", route_gate, {"codegen": "codegen", "retry": "formula", "abandon": END}
    )
    return g.compile()


def recursion_limit(st: TrialState) -> int:
    """Passos máximos: cada tentativa percorre no máximo fórmula → prova → backtest → gate."""
    return 4 * st.budget.max_attempts + 8


def run_trial(app: Any, st: TrialState) -> TrialState:
    out = app.invoke(st, {"recursion_limit": recursion_limit(st)})
    return out if isinstance(out, TrialState) else TrialState(**out)


def seed_for(trial_id: str, node: str, attempt: int) -> int:
    """Semente determinística por tentativa: o replay a reconstrói sem guardar estado."""
    return int(sha256(f"{trial_id}:{node}:{attempt}".encode()).hexdigest()[:8], 16)


def call_agent(st: TrialState, client: AgentClient, node: str) -> tuple[str, list[Event]]:
    """Uma chamada ao modelo: payload projetado, resposta e evento gravado."""
    payload = research_payload(st)
    seed = seed_for(st.trial_id, node, st.attempt)
    response = client.complete(node, payload, seed)
    return response, [*st.events, record(node, st.attempt, seed, payload, response)]
