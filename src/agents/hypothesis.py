"""Agente de hipótese (SPEC-fase-3 §3.9).

O modelo propõe **só o texto e a condição de morte**. Família, restrições da DSL
e id vêm do código: menos graus de liberdade na zona de pesquisa.

O prompt **não contém** Sharpe, métrica, ranking de famílias, exemplo de hipótese
aprovada nem pedido para "ser criativo". Contém a família, as teses já exploradas
nela, os dados disponíveis e ``INSTRUMENTABLE_METRICS``.

Validações determinísticas depois da emissão, nesta ordem — falha em qualquer uma
é nova tentativa **sem** consumir tentativa global:

1. ``who_pays`` não vazio e não circular
2. ``observable`` computável com os dados da família (referências exigidas)
3. ``kill_condition.metric`` instrumentável
4. ``horizon`` dentro de ``family.horizon_range``
5. assinatura semântica ``(família, observável, direção, bucket do horizonte)`` inédita
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from src.agents.schemas import (
    Hypothesis,
    SchemaError,
    hypothesis_from_dict,
    hypothesis_to_dict,
)
from src.catalog.families import Family
from src.catalog.metrics import INSTRUMENTABLE_METRICS
from src.dsl.ast import Constraints
from src.dsl.canonical import snap_to_bucket
from src.dsl.ops import REF_SYMBOLS
from src.ledger.ledger import Kind, Ledger, LedgerRecord
from src.orchestrator.events import AgentClient, Event, record
from src.orchestrator.projection import assert_no_metrics

NODE = "hypothesis"
MAX_HYPOTHESIS_ATTEMPTS = 5

# restrições da DSL derivadas pelo código, iguais para toda família
DEFAULT_MAX_WINDOW = 120
DEFAULT_MAX_DEPTH = 4
DEFAULT_MAX_NODES = 12
DEFAULT_MAX_FREE_PARAMS = 3


class Rejection(StrEnum):
    SCHEMA = "SCHEMA"
    CIRCULAR_PAYER = "CIRCULAR_PAYER"
    NOT_COMPUTABLE = "NOT_COMPUTABLE"
    NOT_INSTRUMENTABLE = "NOT_INSTRUMENTABLE"
    HORIZON_OUT_OF_RANGE = "HORIZON_OUT_OF_RANGE"
    DUPLICATE = "DUPLICATE"


class HypothesisRejected(Exception):
    def __init__(self, reason: Rejection, detail: str) -> None:
        super().__init__(f"{reason.value}: {detail}")
        self.reason = reason
        self.detail = detail


class HypothesisExhausted(Exception):
    """Nenhuma hipótese válida em ``MAX_HYPOTHESIS_ATTEMPTS`` tentativas."""


# ---------------------------------------------------------------- normalização

_STOPWORDS = frozenset(
    [
        "o", "a", "os", "as", "de", "do", "da", "dos", "das", "e", "em", "no", "na",
        "nos", "nas", "que", "por", "pelo", "pela", "um", "uma", "the",
    ]
)
_CIRCULAR = frozenset(
    [
        "mercado", "mercados", "trader", "traders", "reversao", "reversoes",
        "tendencia", "tendencias", "investidor", "investidores", "participante",
        "participantes", "geral", "todos", "alguem", "agente", "agentes", "players",
        "player",
    ]
)


def normalize(text: str) -> str:
    t = unicodedata.normalize("NFKD", text.lower())
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return " ".join(re.findall(r"[a-z0-9]+", t))


def is_circular(who_pays: str) -> bool:
    """Circular se, tirando artigos e preposições, só sobram palavras genéricas."""
    words = [w for w in normalize(who_pays).split() if w not in _STOPWORDS]
    return not words or all(w in _CIRCULAR for w in words)


def horizon_minutes(h: Hypothesis) -> int:
    return int(h.horizon.total_seconds() // 60)


def semantic_key(h: Hypothesis) -> str:
    bucket = snap_to_bucket(max(horizon_minutes(h), 1))
    return f"{h.family}|{normalize(h.observable)}|{h.direction.value}|{bucket}"


# ---------------------------------------------------------------- restrições


def constraints_for(family: Family, session_window: tuple[str, str]) -> Constraints:
    return Constraints(
        allowed_ops=family.allowed_ops,
        forbidden_ops=family.forbidden_ops,
        max_window=DEFAULT_MAX_WINDOW,
        max_depth=DEFAULT_MAX_DEPTH,
        max_nodes=DEFAULT_MAX_NODES,
        max_free_params=DEFAULT_MAX_FREE_PARAMS,
        required_refs=frozenset(family.data_needed & REF_SYMBOLS),
        session_mask=session_window,
    )


# ---------------------------------------------------------------- validação


def validate_hypothesis(h: Hypothesis, family: Family, seen_keys: set[str]) -> None:
    """As cinco checagens, nesta ordem. Levanta ``HypothesisRejected``."""
    if not h.who_pays.strip() or is_circular(h.who_pays):
        raise HypothesisRejected(Rejection.CIRCULAR_PAYER, "quem paga não foi nomeado")
    if not family.available or h.family != family.name:
        raise HypothesisRejected(Rejection.NOT_COMPUTABLE, "família indisponível ou trocada")
    missing = h.dsl_constraints.required_refs - family.data_needed
    if missing:
        raise HypothesisRejected(Rejection.NOT_COMPUTABLE, f"dados ausentes: {sorted(missing)}")
    if not h.observable.strip():
        raise HypothesisRejected(Rejection.NOT_COMPUTABLE, "observável vazio")
    if h.kill_condition.metric not in INSTRUMENTABLE_METRICS:
        raise HypothesisRejected(Rejection.NOT_INSTRUMENTABLE, h.kill_condition.metric)
    lo, hi = family.horizon_range
    if not lo <= horizon_minutes(h) <= hi:
        raise HypothesisRejected(
            Rejection.HORIZON_OUT_OF_RANGE, f"{horizon_minutes(h)} min fora de [{lo}, {hi}]"
        )
    if semantic_key(h) in seen_keys:
        raise HypothesisRejected(Rejection.DUPLICATE, "tese já explorada")


# ---------------------------------------------------------------- prompt e resposta

HYPOTHESIS_TOOL: dict[str, Any] = {
    "name": "emit_hypothesis",
    "description": "Registra uma hipótese falseável para a família indicada.",
    "input_schema": {
        "type": "object",
        "properties": {
            "claim": {"type": "string"},
            "who_pays": {"type": "string"},
            "observable": {"type": "string"},
            "direction": {"type": "string", "enum": ["LONG_ON_HIGH", "SHORT_ON_HIGH"]},
            "horizon": {"type": "string", "description": "ISO 8601, ex.: PT5M"},
            "session_window": {"type": "array", "items": {"type": "string"},
                               "minItems": 2, "maxItems": 2},
            "regime_filter": {"type": ["string", "null"]},
            "kill_condition": {
                "type": "object",
                "properties": {
                    "metric": {"type": "string", "enum": sorted(INSTRUMENTABLE_METRICS)},
                    "aggregation": {"type": "string", "enum": ["mediana", "media"]},
                    "window_days": {"type": "integer", "minimum": 1},
                    "operator": {"type": "string", "enum": ["<", ">"]},
                    "threshold": {"type": "number"},
                    "unit": {"type": "string"},
                },
                "required": ["metric", "aggregation", "window_days", "operator",
                             "threshold", "unit"],
                "additionalProperties": False,
            },
        },
        "required": ["claim", "who_pays", "observable", "direction", "horizon",
                     "session_window", "kill_condition"],
        "additionalProperties": False,
    },
}


def build_payload(family: Family, explored_claims: list[str]) -> str:
    body = {
        "task": "hypothesis",
        "family": {
            "name": family.name,
            "mechanism": family.mechanism,
            "typical_payer": family.typical_payer,
            "data_available": sorted(family.data_needed),
            "horizon_minutes": list(family.horizon_range),
            "session_hint": list(family.session_hint) if family.session_hint else None,
        },
        "explored_claims": explored_claims,
        "instrumentable_metrics": INSTRUMENTABLE_METRICS,
        "requirements": [
            "nomeie quem paga e por qual motivo econômico",
            "o observável precisa ser calculável com os dados disponíveis",
            "a condição de morte usa uma das métricas instrumentáveis",
            "o horizonte fica dentro da faixa da família",
            "não repita uma tese já explorada",
        ],
    }
    payload = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    assert_no_metrics(payload, None)
    return payload


def parse_response(response: str, family: Family, hypothesis_id: str) -> Hypothesis:
    try:
        raw = json.loads(response)
    except json.JSONDecodeError as e:
        raise HypothesisRejected(Rejection.SCHEMA, f"JSON inválido: {e}") from None
    if not isinstance(raw, dict):
        raise HypothesisRejected(Rejection.SCHEMA, "esperado objeto")
    window = raw.get("session_window") or (list(family.session_hint)
                                           if family.session_hint else ["09:00", "17:30"])
    full = {
        **raw,
        "id": hypothesis_id,
        "family": family.name,
        "session_window": window,
        "dsl_constraints": {"allowed_ops": [], "max_window": 1, "max_depth": 1},
    }
    try:
        h = hypothesis_from_dict(full)
    except (SchemaError, TypeError, ValueError) as e:
        raise HypothesisRejected(Rejection.SCHEMA, str(e)) from None
    return Hypothesis(**{**h.__dict__,
                         "dsl_constraints": constraints_for(family, h.session_window)})


# ---------------------------------------------------------------- geração


@dataclass(frozen=True)
class HypothesisResult:
    hypothesis: Hypothesis
    events: tuple[Event, ...]
    rejections: tuple[Rejection, ...]


def explored(ledger: Ledger, family: Family) -> tuple[list[str], set[str]]:
    claims: list[str] = []
    keys: set[str] = set()
    for r in ledger.records(Kind.HYPOTHESIS):
        assert r.payload is not None
        if r.payload.get("family") == family.name:
            claims.append(str(r.payload["claim"]))
        keys.add(str(r.payload["semantic_key"]))
    return claims, keys


def generate(client: AgentClient, family: Family, ledger: Ledger, hypothesis_id: str,
             seed: int) -> HypothesisResult:
    """Até ``MAX_HYPOTHESIS_ATTEMPTS`` propostas. Nenhuma consome tentativa global.

    A aceita é gravada como ``kind='hypothesis'`` no livro-razão.
    """
    claims, keys = explored(ledger, family)
    payload = build_payload(family, claims)
    events: list[Event] = []
    rejections: list[Rejection] = []
    for attempt in range(1, MAX_HYPOTHESIS_ATTEMPTS + 1):
        s = seed + attempt
        response = client.complete(NODE, payload, s)
        events.append(record(NODE, attempt, s, payload, response))
        try:
            h = parse_response(response, family, hypothesis_id)
            validate_hypothesis(h, family, keys)
        except HypothesisRejected as e:
            rejections.append(e.reason)
            continue
        g = ledger.genesis_info()
        ledger.append(LedgerRecord(
            trial_id=f"{hypothesis_id}/hypothesis",
            kind=Kind.HYPOTHESIS,
            data_hash=g.data_hash,
            config_hash=g.config_hash,
            hypothesis_id=hypothesis_id,
            payload={**hypothesis_to_dict(h), "semantic_key": semantic_key(h)},
        ))
        return HypothesisResult(h, tuple(events), tuple(rejections))
    raise HypothesisExhausted(f"{family.name}: {[r.value for r in rejections]}")
