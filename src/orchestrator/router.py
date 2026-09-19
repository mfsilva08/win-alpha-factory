"""Roteamento: funções puras e determinísticas (R3, SPEC-fase-3 §3.3).

Nunca substitua por um "agente supervisor". Rodar duas vezes dá o mesmo caminho.

Diferença deliberada em relação ao esboço da spec: **toda** rota de ``retry``
confere o orçamento de tentativas. No esboço, ``INVALID_AST``, ``TOO_COMPLEX`` e
``REDUNDANT`` devolviam ``retry`` antes de olhar ``attempt``, e ``route_proof``
nunca olhava — fórmula válida seguida de prova falha repetia para sempre.
Uma fórmula válida na última tentativa ainda segue adiante.
"""

from __future__ import annotations

from typing import Literal

from src.dsl.verdict import Verdict
from src.orchestrator.state import ProofStatus, TrialState

FORMULA_RETRY: frozenset[Verdict] = frozenset(
    {Verdict.INVALID_AST, Verdict.TOO_COMPLEX, Verdict.REDUNDANT}
)
GATE_RETRY: frozenset[Verdict] = frozenset(
    {Verdict.INSUFFICIENT_SAMPLE, Verdict.COST_DOMINATED, Verdict.UNSTABLE_ACROSS_FOLDS}
)


class RoutingError(Exception):
    """Estado que nenhuma rota prevê: bug, não caso de negócio."""


def _exhausted(st: TrialState) -> bool:
    return st.attempt >= st.budget.max_attempts


def route_formula(st: TrialState) -> Literal["proof", "retry", "abandon"]:
    if st.verdict in FORMULA_RETRY:
        return "abandon" if _exhausted(st) else "retry"
    if st.verdict is not None:
        raise RoutingError(f"veredito {st.verdict} não pertence ao nó de fórmula")
    if st.formula_ast is None:
        raise RoutingError("fórmula ausente sem veredito")
    return "proof"


def route_proof(st: TrialState) -> Literal["backtest", "retry", "abandon"]:
    if st.proof_status is ProofStatus.FAILED:
        return "abandon" if _exhausted(st) else "retry"
    if st.proof_status in (ProofStatus.PASSED, ProofStatus.SKIPPED):
        return "backtest"
    raise RoutingError(f"prova em estado {st.proof_status} ao sair do nó de prova")


def route_gate(st: TrialState) -> Literal["codegen", "retry", "abandon"]:
    if st.verdict is Verdict.ACCEPTED:
        return "codegen"
    if st.verdict is Verdict.FAILED_GATE:
        return "abandon"  # encerra a hipótese. Não "melhore" isto.
    if st.verdict in GATE_RETRY:
        return "abandon" if _exhausted(st) else "retry"
    raise RoutingError(f"veredito {st.verdict} não pertence ao gate")
