"""Cliente do modelo para os agentes de pesquisa (SPEC-fase-3 §3.11).

- Uma chamada por nó, por iteração. Nunca duas. Retries de rede do SDK
  (``max_retries``) contam como a mesma chamada
- Todo payload passa por ``assert_no_metrics`` antes do envio — ``AssertionError``,
  não aviso
- Saída estruturada por tool use forçado, com schema da tentativa
- Credenciais: resolvidas pelo SDK (``ANTHROPIC_API_KEY`` ou ``ant auth login``).
  Este módulo nunca lê nem grava chave
- Fallback de recusa do lado do servidor habilitado por padrão
  (``fallbacks="default"``): numa recusa por política, a API refaz a mesma
  requisição em outro modelo dentro da mesma chamada

A semente é gravada no ``Event`` para auditoria, mas a API não a aceita: o replay
depende das respostas gravadas, não de amostragem reprodutível.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from src.agents.formula import tool_schema
from src.agents.hypothesis import HYPOTHESIS_TOOL
from src.agents.schemas import hypothesis_from_dict
from src.orchestrator.projection import assert_no_metrics

DEFAULT_MODEL = "claude-opus-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"

SYSTEM: dict[str, str] = {
    "hypothesis": (
        "Você propõe hipóteses de mercado falseáveis para o mini índice (WIN) na B3, "
        "dentro da família de mecanismo indicada no JSON do usuário. Cada hipótese "
        "precisa nomear quem paga e por qual motivo econômico, usar só os dados "
        "disponíveis, declarar uma condição de morte com uma das métricas "
        "instrumentáveis listadas e ficar dentro da faixa de horizonte da família. "
        "Não repita teses já exploradas. Responda usando a ferramenta emit_hypothesis."
    ),
    "formula": (
        "Você traduz uma hipótese de mercado em uma fórmula de sinal na DSL descrita "
        "pelas restrições do JSON do usuário. A raiz precisa ter unidade Z ou Bool. "
        "Se houver um veredito anterior, ele diz por que a tentativa anterior não "
        "seguiu; o campo detail, quando presente, traz a mensagem do validador. "
        "Não repita fórmulas bloqueadas. Responda usando a ferramenta emit_formula."
    ),
}


class AgentRefused(Exception):
    """O modelo (e o fallback) recusou a requisição."""


class AgentOutputMissing(Exception):
    """A resposta não trouxe a chamada de ferramenta esperada."""


@dataclass(frozen=True)
class ClientConfig:
    model: str = DEFAULT_MODEL
    max_tokens: int = 16_000
    effort: str = "high"
    timeout_s: float = 300.0
    max_retries: int = 2
    server_fallback: bool = True


def tool_for(node: str, payload: str) -> dict[str, Any]:
    if node == "hypothesis":
        return HYPOTHESIS_TOOL
    if node == "formula":
        body = json.loads(payload)
        constraints = hypothesis_from_dict(
            {**body["hypothesis"], "dsl_constraints": body["constraints"]}
        ).dsl_constraints
        return tool_schema(constraints)
    raise ValueError(f"nó sem ferramenta: {node}")


def build_request(node: str, payload: str, cfg: ClientConfig) -> dict[str, Any]:
    """Os argumentos de ``client.beta.messages.create``. Função pura, testável."""
    assert_no_metrics(payload, None)
    tool = tool_for(node, payload)
    req: dict[str, Any] = {
        "model": cfg.model,
        "max_tokens": cfg.max_tokens,
        "system": SYSTEM[node],
        "messages": [{"role": "user", "content": payload}],
        "tools": [tool],
        "tool_choice": {"type": "tool", "name": tool["name"]},
        "output_config": {"effort": cfg.effort},
    }
    if cfg.server_fallback:
        req["betas"] = [FALLBACK_BETA]
        req["fallbacks"] = "default"
    return req


def extract(response: Any, tool_name: str) -> str:
    """JSON canônico da entrada do tool use; recusa e ausência viram exceção."""
    if getattr(response, "stop_reason", None) == "refusal":
        raise AgentRefused("o modelo recusou a requisição")
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and block.name == tool_name:
            return json.dumps(block.input, sort_keys=True, ensure_ascii=False)
    raise AgentOutputMissing(f"resposta sem chamada a {tool_name}")


class AnthropicClient:
    """Implementa ``AgentClient`` sobre o SDK oficial."""

    def __init__(self, cfg: ClientConfig | None = None) -> None:
        import anthropic  # só importa quando o cliente real é usado

        self.cfg = cfg or ClientConfig()
        self._client = anthropic.Anthropic(
            timeout=self.cfg.timeout_s, max_retries=self.cfg.max_retries
        )

    def complete(self, node: str, payload: str, seed: int) -> str:
        req = build_request(node, payload, self.cfg)
        tool_name = req["tools"][0]["name"]
        response = self._client.beta.messages.create(**req)
        return extract(response, tool_name)
