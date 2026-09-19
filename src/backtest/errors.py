"""Exceções tipadas do backtest."""

from __future__ import annotations


class BacktestError(Exception):
    """Base dos erros do backtest."""


class CostConfigError(BacktestError):
    """``config/costs.yaml`` ausente, incompleto ou com valor inválido."""


class EngineError(BacktestError):
    """O motor de backtest (a API externa) falhou: rede, timeout, erro remoto."""


class EngineNotConfigured(EngineError):
    """O adaptador da API ainda não foi implementado ou configurado."""


class InvalidOutcome(BacktestError):
    """O motor respondeu, mas a resposta viola o contrato (caminhos, NaN, dataset)."""


class DuplicateTrial(BacktestError):
    """O ``trial_id`` já existe no livro-razão; recusado antes de chamar o motor."""


class BacktestCrashed(BacktestError):
    """A avaliação falhou **depois** de gravada como tentativa ``crashed`` no livro-razão.

    Levantada de propósito: uma API fora do ar não pode queimar tentativas em
    silêncio num laço. A sessão para e alguém olha.
    """

    def __init__(self, trial_id: str, cause: BaseException) -> None:
        super().__init__(f"tentativa {trial_id} gravada como crashed: {type(cause).__name__}")
        self.trial_id = trial_id
