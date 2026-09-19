"""Recompensa categórica por veredito (SPEC-fase-3 §3.7).

Mede produtividade do processo, não lucro — é a única medida que atravessa o
firewall sem contaminá-lo. **Nunca use Sharpe nem PnL como recompensa.**
``FAILED_GATE`` valer 0,8 não é erro: chegar ao gate significa hipótese
falseável, fórmula bem tipada, original e economicamente viável.
"""

from __future__ import annotations

from src.dsl.verdict import Verdict

REWARD: dict[Verdict, float] = {
    Verdict.INVALID_AST: 0.0,
    Verdict.REDUNDANT: 0.0,
    Verdict.TOO_COMPLEX: 0.1,
    Verdict.PROOF_FAILED: 0.3,
    Verdict.INSUFFICIENT_SAMPLE: 0.4,
    Verdict.COST_DOMINATED: 0.5,
    Verdict.UNSTABLE_ACROSS_FOLDS: 0.6,
    Verdict.FAILED_GATE: 0.8,
    Verdict.ACCEPTED: 1.0,
}


def reward(v: Verdict) -> float:
    return REWARD[v]
