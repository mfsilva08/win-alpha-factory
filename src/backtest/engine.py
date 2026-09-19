"""A porta do motor de backtest.

O backtest roda num motor externo (uma API chamada com o id do teste). Este
módulo define **o que** a fábrica pede e **o que** aceita de volta; o adaptador
concreto vive em ``api_engine.py``. Trocar de motor é trocar de adaptador — o
runner, o livro-razão e o gate não mudam.

O motor é zona de verificação: pode ver métricas, nunca fala com LLM (R2) e
nunca grava no livro-razão (quem grava é o ``runner``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time, timedelta
from math import comb
from typing import Protocol

from src.backtest.costs import CostModel
from src.backtest.metrics import BacktestOutcome


@dataclass(frozen=True)
class TradeRules:
    """Regras de execução (SPEC-fase-2 §2.4). O motor aplica o preenchimento pessimista."""

    threshold: float  # limiar do sinal
    contracts: int
    max_holding: timedelta  # fecha por tempo
    stop_points: int | None
    target_points: int | None
    cutoff: time  # zeragem compulsória
    session: tuple[time, time]


@dataclass(frozen=True)
class CvConfig:
    """Validação cruzada combinatória com purga e embargo (SPEC-fase-2 §2.3)."""

    n_groups: int
    k_test: int
    label_horizon: timedelta
    embargo_mult: float

    def n_splits(self) -> int:
        """Partições treino/teste: C(n_groups, k_test). Com 8 e 2, 28."""
        return comb(self.n_groups, self.k_test)


@dataclass(frozen=True)
class BacktestRequest:
    test_id: str  # o trial_id do livro-razão; é o id com que a API é chamada
    hypothesis_id: str
    formula: str  # S-expression da AST original (``canonical.serialize``)
    ast_hash: str  # assinatura exata
    data_hash: str  # dataset exigido; a resposta precisa ecoar o mesmo
    rules: TradeRules
    costs: CostModel
    cv: CvConfig


class BacktestEngine(Protocol):
    def evaluate(self, req: BacktestRequest) -> BacktestOutcome:
        """Roda o backtest e devolve as métricas por partição.

        Levanta ``EngineError`` em falha de rede, timeout ou erro remoto. Não
        grava nada: o write-ahead é responsabilidade do ``runner``.
        """
        ...
