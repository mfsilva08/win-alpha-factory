"""O estado de uma tentativa (SPEC-fase-3 §3.1). Único objeto mutável do projeto.

``metrics`` é **privado**: jamais é projetado à zona de pesquisa. Quem monta o
que vai para a API é ``projection.project`` — nunca serialize ``TrialState``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from src.agents.schemas import Hypothesis
from src.backtest.metrics import FoldMetrics
from src.dsl.ast import Constraints, Node
from src.dsl.verdict import Verdict
from src.orchestrator.budgets import Budget
from src.orchestrator.events import Event

__all__ = ["Metrics", "ProofStatus", "TrialState", "Verdict", "Zone"]


class ProofStatus(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    PASSED = "PASSED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"  # verificação formal fora do escopo (ADR-006)


class Zone(StrEnum):
    RESEARCH = "research"
    VERIFICATION = "verification"


@dataclass(frozen=True)
class Metrics:
    """O que o selo devolveu: o vetor por partição do CPCV."""

    folds: tuple[FoldMetrics, ...]


@dataclass
class TrialState:
    trial_id: str
    hypothesis: Hypothesis
    budget: Budget
    constraints: Constraints  # começa em hypothesis.dsl_constraints e só aperta
    parent_id: str | None = None
    formula_ast: Node | None = None
    proof_status: ProofStatus = ProofStatus.NOT_STARTED
    verdict: Verdict | None = None
    metrics: Metrics | None = None  # PRIVADO — jamais projetado à pesquisa
    attempt: int = 0
    detail: str | None = None  # só para INVALID_AST e PROOF_FAILED
    blocked_sigs: frozenset[str] = frozenset()
    events: list[Event] = field(default_factory=list)
