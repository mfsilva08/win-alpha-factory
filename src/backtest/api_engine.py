"""Adaptador para a API externa de backtest — **ainda não implementado**.

A API é chamada com o id do teste e devolve o resultado. Falta o contrato dela.
Quando ele chegar, este é o único arquivo que precisa mudar. Perguntas que o
contrato tem que responder (ver também SPEC-fase-2 §2.0):

1. Como a fórmula chega à API: enviamos ``BacktestRequest`` (S-expression, regras,
   custos, CPCV) e ela devolve um id, ou o teste já existe lá e só passamos o id?
2. A resposta é síncrona ou é preciso consultar o status até concluir?
3. A resposta traz as métricas **por partição** do CPCV, com skew, curtose e
   pregões ativos? O gate precisa do vetor, não da média.
4. A resposta diz qual dataset foi usado (hash)? Sem isso não há como garantir
   que o backtest rodou nos dados do gênesis.
5. A API aceita os custos e as regras de execução por requisição, ou usa os dela?
6. A API aceita dados sintéticos? O teste de calibração do gate roda 1.000
   estratégias sobre ruído.
7. A API mantém a lista dos ids executados? Serve para conferir que nenhuma
   execução deixou de entrar no livro-razão.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.backtest.engine import BacktestRequest
from src.backtest.errors import EngineNotConfigured
from src.backtest.metrics import BacktestOutcome


@dataclass(frozen=True)
class ApiEngineConfig:
    base_url: str
    timeout_s: float = 300.0


class ApiBacktestEngine:
    """Implementa ``BacktestEngine`` sobre a API externa."""

    def __init__(self, config: ApiEngineConfig) -> None:
        # Recusa na construção, não na chamada: um motor sem contrato não pode
        # chegar ao runner e queimar tentativas como crashed.
        raise EngineNotConfigured(
            "contrato da API de backtest ainda não definido; ver docstring de api_engine.py"
        )

    def evaluate(self, req: BacktestRequest) -> BacktestOutcome:
        raise EngineNotConfigured("contrato da API de backtest ainda não definido")
