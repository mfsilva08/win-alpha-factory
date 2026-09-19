"""O vocabulário de vereditos — única informação que atravessa o firewall (R1).

Vive no pacote ``dsl`` porque o M1 já precisa dele; ``orchestrator/state.py``
reexporta este mesmo enum no M5.
"""

from __future__ import annotations

from enum import StrEnum


class Verdict(StrEnum):
    ACCEPTED = "ACCEPTED"
    INVALID_AST = "INVALID_AST"
    TOO_COMPLEX = "TOO_COMPLEX"
    REDUNDANT = "REDUNDANT"
    PROOF_FAILED = "PROOF_FAILED"
    INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"
    UNSTABLE_ACROSS_FOLDS = "UNSTABLE_ACROSS_FOLDS"
    COST_DOMINATED = "COST_DOMINATED"
    FAILED_GATE = "FAILED_GATE"
