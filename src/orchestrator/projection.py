"""O firewall (R1, SPEC-fase-3 §3.2) — o componente de maior risco do projeto.

Três camadas, cada uma suficiente sozinha:

1. **Estrutura.** ``ResearchView`` não tem campo de métricas. O dado não existe no
   objeto que é serializado.
2. **Serialização.** ``serialize_for_api`` só aceita ``ResearchView`` e escreve
   uma lista fechada de campos. ``TrialState`` nunca é serializado.
3. **Varredura.** ``assert_no_metrics`` procura, no payload final, nomes de métrica
   e os próprios números das métricas privadas da tentativa. Falha é
   ``AssertionError`` levantado explicitamente — não some com ``python -O``.

``detail`` só atravessa em ``INVALID_AST`` e ``PROOF_FAILED``: verdades sintáticas
e lógicas, não estatísticas.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, fields
from typing import Literal, overload

from src.agents.schemas import Hypothesis, constraints_to_dict, hypothesis_to_dict
from src.backtest.metrics import FoldMetrics
from src.dsl.ast import Constraints, Node
from src.dsl.canonical import serialize
from src.dsl.verdict import Verdict
from src.gate.config import GateConfig
from src.orchestrator.state import Metrics, TrialState, Zone

DETAIL_ALLOWED: frozenset[Verdict] = frozenset({Verdict.INVALID_AST, Verdict.PROOF_FAILED})


@dataclass(frozen=True)
class ResearchView:
    hypothesis: Hypothesis
    formula_ast: Node | None
    verdict: Verdict | None
    detail: str | None
    attempt: int
    blocked_sigs: frozenset[str]
    constraints: Constraints
    # NÃO existe campo de métricas nesta classe


@dataclass(frozen=True)
class VerificationView:
    formula_ast: Node
    metrics: Metrics | None
    n_trials: int
    cfg: GateConfig


@overload
def project(st: TrialState, z: Literal[Zone.RESEARCH]) -> ResearchView: ...


@overload
def project(
    st: TrialState, z: Literal[Zone.VERIFICATION], *, n_trials: int, cfg: GateConfig
) -> VerificationView: ...


def project(
    st: TrialState, z: Zone, *, n_trials: int | None = None, cfg: GateConfig | None = None
) -> ResearchView | VerificationView:
    if z is Zone.RESEARCH:
        return ResearchView(
            hypothesis=st.hypothesis,
            formula_ast=st.formula_ast,
            verdict=st.verdict,
            detail=st.detail if st.verdict in DETAIL_ALLOWED else None,
            attempt=st.attempt,
            blocked_sigs=st.blocked_sigs,
            constraints=st.constraints,
        )
    if st.formula_ast is None or n_trials is None or cfg is None:
        raise ValueError("a visão de verificação exige fórmula, n_trials e cfg")
    return VerificationView(st.formula_ast, st.metrics, n_trials, cfg)


# ---------------------------------------------------------------- serialização


def serialize_for_api(view: ResearchView) -> str:
    """JSON determinístico com a lista fechada de campos da ``ResearchView``."""
    if type(view) is not ResearchView:
        raise TypeError("só ResearchView pode ser enviada à API de pesquisa")
    body = {
        "hypothesis": hypothesis_to_dict(view.hypothesis),
        "formula": serialize(view.formula_ast) if view.formula_ast is not None else None,
        "verdict": view.verdict.value if view.verdict is not None else None,
        "detail": view.detail,
        "attempt": view.attempt,
        "blocked_sigs": sorted(view.blocked_sigs),
        "constraints": constraints_to_dict(view.constraints),
    }
    return json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


# ---------------------------------------------------------------- varredura

_METRIC_WORDS = ("sharpe", "pnl", "drawdown", "sortino", "kurtosis", "deflated")
_METRIC_FIELDS = tuple(f.name for f in fields(FoldMetrics) if "_" in f.name)
_SMALL_INT = 1000  # inteiros menores colidem com janelas, horários e limites


def _number_forms(v: float) -> set[str]:
    a = abs(v)
    if not math.isfinite(a):
        return set()
    if a.is_integer():
        return {str(int(a))} if a >= _SMALL_INT else set()
    forms = {repr(a), f"{a:.2f}", f"{a:.3f}", f"{a:.4f}".rstrip("0")}
    return {s for s in forms if s not in ("0.00", "0.000", "0.")}


def _metric_numbers(m: Metrics) -> set[str]:
    out: set[str] = set()
    for f in m.folds:
        for name in (x.name for x in fields(FoldMetrics)):
            v = getattr(f, name)
            if isinstance(v, int | float) and not isinstance(v, bool) and name != "path_id":
                out |= _number_forms(float(v))
    return out


def assert_no_metrics(payload: str, metrics: Metrics | None) -> None:
    """Levanta ``AssertionError`` se o payload carregar nome ou número de métrica."""
    low = payload.lower()
    for word in (*_METRIC_WORDS, *_METRIC_FIELDS):
        if word in low:
            raise AssertionError(f"payload de pesquisa contém termo de métrica: {word!r}")
    if metrics is None:
        return
    for s in _metric_numbers(metrics):
        if re.search(rf"(?<![\d.]){re.escape(s)}(?!\d)", payload):
            raise AssertionError("payload de pesquisa contém um número das métricas privadas")


def research_payload(st: TrialState) -> str:
    """O único caminho de ``TrialState`` até a API de pesquisa."""
    payload = serialize_for_api(project(st, Zone.RESEARCH))
    assert_no_metrics(payload, st.metrics)
    return payload
