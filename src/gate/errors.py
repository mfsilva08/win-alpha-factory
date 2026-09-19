"""Exceções tipadas do gate."""

from __future__ import annotations


class GateError(Exception):
    """Base dos erros do gate."""


class PreregIncomplete(GateError):
    """O pré-registro ainda tem marcador ``<<< DECIDIR >>>`` ou campo ausente."""


class PreregMismatch(GateError):
    """O sha256 do pré-registro não bate com o registrado no gênesis."""


class PreregInvalid(GateError):
    """Campo do pré-registro com valor fora do domínio."""


class StatisticsError(GateError):
    """Entrada estatística fora do domínio (T pequeno, denominador não positivo)."""
