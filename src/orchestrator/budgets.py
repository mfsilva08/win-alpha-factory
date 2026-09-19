"""Orçamentos de tentativa (SPEC-fase-3 §3.4).

Retries por ``INVALID_AST`` e ``PROOF_FAILED`` não consomem tentativa global —
nunca chegam ao backtest —, mas consomem ``max_attempts``.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.gate.config import GateConfig
from src.ledger.ledger import Ledger


class BudgetExhausted(Exception):
    """Teto atingido. A sessão para; o projeto pode ter terminado."""


class BudgetMismatch(Exception):
    """``max_trials_global`` diverge do ``MAX_TRIALS`` pré-registrado."""


@dataclass(frozen=True)
class Budget:
    max_attempts: int = 10  # refinamentos da mesma fórmula
    max_formulas_per_hypothesis: int = 25
    max_trials_global: int = 3_000  # bate com gate-prereg.md
    max_proof_iterations: int = 15

    def __post_init__(self) -> None:
        for name in ("max_attempts", "max_formulas_per_hypothesis", "max_trials_global",
                     "max_proof_iterations"):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} deve ser >= 1")


def check(ledger: Ledger, b: Budget) -> None:
    """Levanta ``BudgetExhausted`` se o teto global foi atingido."""
    used = ledger.count()
    if used >= b.max_trials_global:
        raise BudgetExhausted(f"teto global atingido: {used} de {b.max_trials_global}")


def check_formulas(emitted: int, b: Budget) -> None:
    if emitted >= b.max_formulas_per_hypothesis:
        raise BudgetExhausted(f"hipótese esgotou {b.max_formulas_per_hypothesis} fórmulas")


def check_consistent(b: Budget, cfg: GateConfig) -> None:
    """O teto do orçamento é o do pré-registro, nunca outro."""
    if b.max_trials_global != cfg.max_trials:
        raise BudgetMismatch(
            f"max_trials_global={b.max_trials_global} != MAX_TRIALS={cfg.max_trials}"
        )
