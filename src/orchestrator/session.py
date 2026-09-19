"""Montagem de uma sessão da fábrica: os nós reais ligados ao grafo.

Uma sessão roda em lote e termina (R8). Para cada tentativa:

1. a hipótese vem escrita à mão (YAML) ou do agente de hipótese, **antes** do
   grafo — o nó ``hypothesis`` do grafo só confere que ela existe
2. o nó de fórmula chama o modelo pela projeção
3. prova: ``SKIPPED`` enquanto a verificação formal estiver fora do escopo (ADR-006)
4. backtest: ``budgets.check`` e ``runner.run`` — write-ahead no livro-razão
5. gate: agrega, colapsa e grava o veredito como ``kind='verdict'``
6. codegen: injetado (M7)

Duas peças do gate ainda são decisões pendentes do M4 e entram injetadas:
``aggregate`` (partições do CPCV → ``GateInputs``) e ``var_sr``. Sem elas a
sessão recusa começar — ``GatePending``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from src.agents.formula import make_node as make_formula_node
from src.agents.formula import record_verdict
from src.agents.schemas import Hypothesis
from src.backtest.engine import BacktestEngine
from src.backtest.runner import BacktestConfig, run
from src.catalog.families import Family
from src.gate.collapse import GateInputs, collapse
from src.gate.config import GateConfig
from src.ledger.ledger import Ledger
from src.orchestrator import budgets
from src.orchestrator.budgets import Budget
from src.orchestrator.events import AgentClient
from src.orchestrator.graph import NodeFn, Nodes, build_graph, run_trial
from src.orchestrator.state import Metrics, ProofStatus, TrialState


class GatePending(Exception):
    """Agregação do CPCV ou ``var_sr`` ainda não decididos (SPEC-fase-2 §2.3, §2.8)."""


@dataclass(frozen=True)
class SessionDeps:
    ledger: Ledger
    engine: BacktestEngine
    client: AgentClient
    backtest_cfg: BacktestConfig
    gate_cfg: GateConfig
    budget: Budget
    aggregate: Callable[[Metrics], GateInputs] | None
    var_sr: Callable[[], float] | None
    codegen: NodeFn


def backtest_node(d: SessionDeps) -> NodeFn:
    def node(st: TrialState) -> dict[str, Any]:
        budgets.check(d.ledger, d.budget)
        assert st.formula_ast is not None
        res = run(st.formula_ast, st.hypothesis.id, f"{st.trial_id}/{st.attempt}",
                  d.backtest_cfg, d.ledger, d.engine)
        return {"metrics": Metrics(res.metrics)}
    return node


def gate_node(d: SessionDeps, family: Family) -> NodeFn:
    def node(st: TrialState) -> dict[str, Any]:
        if d.aggregate is None or d.var_sr is None:
            raise GatePending("agregação do CPCV e var_sr pendentes")
        assert st.metrics is not None
        verdict = collapse(d.aggregate(st.metrics), d.ledger.count(), d.var_sr(), d.gate_cfg)
        record_verdict(d.ledger, st, verdict, family.name)
        return {"verdict": verdict}
    return node


def build_nodes(d: SessionDeps, family: Family) -> Nodes:
    if d.aggregate is None or d.var_sr is None:
        raise GatePending("agregação do CPCV e var_sr pendentes (SPEC-fase-2 §2.3, §2.8)")
    return Nodes(
        hypothesis=lambda st: {},
        formula=make_formula_node(d.client, d.ledger, family),
        proof=lambda st: {"proof_status": ProofStatus.SKIPPED},
        backtest=backtest_node(d),
        gate=gate_node(d, family),
        codegen=d.codegen,
    )


def run_hypothesis(d: SessionDeps, family: Family, h: Hypothesis, trial_id: str) -> TrialState:
    """Uma hipótese do começo ao fim do grafo."""
    budgets.check(d.ledger, d.budget)
    app = build_graph(build_nodes(d, family))
    st = TrialState(trial_id=trial_id, hypothesis=h, budget=d.budget,
                    constraints=h.dsl_constraints)
    return run_trial(app, st)
