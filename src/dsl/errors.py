"""Exceções tipadas do motor de DSL.

Toda falha por dado inválido levanta uma destas, nunca devolve ``None`` em silêncio.
"""

from __future__ import annotations


class DslError(Exception):
    """Base de todos os erros do motor de DSL."""


class DslParseError(DslError):
    """Texto de fórmula que não pode ser convertido em AST."""


class DslTypeError(DslError):
    """AST mal formada: aridade, tipo de argumento ou unidade incompatível."""


class DslEvalError(DslError):
    """Falha ao avaliar uma AST sobre dados de mercado."""


class MarketDataError(DslError):
    """``MarketFrame`` ou ``Bar`` inconsistente com o que o avaliador exige."""


class CanonicalizationError(DslError):
    """A canonicalização não convergiu para um ponto fixo."""
