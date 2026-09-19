"""Tipos de hipótese e condição de morte (SPEC-fase-3 §3.8).

Só os dados e a leitura do YAML escrito à mão. As validações semânticas da
hipótese (``who_pays`` circular, horizonte da família, métrica instrumentável) e
o schema de tool use por tentativa são do M6.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

import yaml

from src.dsl.ast import Constraints, parse_hhmm
from src.dsl.errors import DslTypeError
from src.dsl.ops import Op


class SchemaError(Exception):
    """Hipótese ou condição de morte fora do schema."""


class Direction(StrEnum):
    LONG_ON_HIGH = "LONG_ON_HIGH"
    SHORT_ON_HIGH = "SHORT_ON_HIGH"


@dataclass(frozen=True)
class KillSpec:
    metric: str  # coluna que o robô sabe gravar
    aggregation: Literal["mediana", "media"]
    window_days: int
    operator: Literal["<", ">"]
    threshold: float
    unit: str


@dataclass(frozen=True)
class Hypothesis:
    id: str
    family: str
    claim: str
    who_pays: str
    observable: str
    direction: Direction
    horizon: timedelta
    session_window: tuple[str, str]
    regime_filter: str | None
    kill_condition: KillSpec
    dsl_constraints: Constraints


_ISO_DURATION = re.compile(r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$")


def parse_duration(s: str) -> timedelta:
    """Subconjunto de ISO 8601 usado nas hipóteses: ``PT3M``, ``PT1H30M``, ``PT45S``."""
    m = _ISO_DURATION.match(s)
    if m is None or not any(m.groups()):
        raise SchemaError(f"duração ISO 8601 inválida: {s!r}")
    h, mi, se = (int(g) if g else 0 for g in m.groups())
    d = timedelta(hours=h, minutes=mi, seconds=se)
    if d <= timedelta(0):
        raise SchemaError("duração deve ser positiva")
    return d


def format_duration(d: timedelta) -> str:
    total = int(d.total_seconds())
    h, rest = divmod(total, 3600)
    mi, se = divmod(rest, 60)
    return "PT" + (f"{h}H" if h else "") + (f"{mi}M" if mi else "") + (f"{se}S" if se else "")


def _req(raw: dict[str, Any], key: str) -> Any:
    if key not in raw or raw[key] is None:
        raise SchemaError(f"campo obrigatório ausente: {key}")
    return raw[key]


def _ops(names: Any, key: str) -> frozenset[Op]:
    if not isinstance(names, list):
        raise SchemaError(f"{key}: esperado lista")
    try:
        ops = frozenset(Op[str(n)] for n in names)
    except KeyError as e:
        raise SchemaError(f"{key}: operador desconhecido {e}") from None
    if Op.CONST in ops:
        raise SchemaError(f"{key}: CONST é interno")
    return ops


def _window(raw: Any, key: str) -> tuple[str, str]:
    if isinstance(raw, dict):
        pair = (raw.get("start"), raw.get("end"))
    elif isinstance(raw, list) and len(raw) == 2:
        pair = (raw[0], raw[1])
    else:
        raise SchemaError(f"{key}: esperado início e fim")
    a, b = str(pair[0]), str(pair[1])
    try:
        if parse_hhmm(a) >= parse_hhmm(b):
            raise SchemaError(f"{key}: início deve ser anterior ao fim")
    except DslTypeError as e:
        raise SchemaError(f"{key}: {e}") from None
    return a, b


def hypothesis_from_dict(raw: dict[str, Any]) -> Hypothesis:
    k = _req(raw, "kill_condition")
    agg = _req(k, "aggregation")
    op = _req(k, "operator")
    if agg not in ("mediana", "media"):
        raise SchemaError("kill_condition.aggregation deve ser mediana ou media")
    if op not in ("<", ">"):
        raise SchemaError("kill_condition.operator deve ser < ou >")
    window_days = int(_req(k, "window_days"))
    if window_days < 1:
        raise SchemaError("kill_condition.window_days deve ser >= 1")
    kill = KillSpec(
        metric=str(_req(k, "metric")),
        aggregation=agg,
        window_days=window_days,
        operator=op,
        threshold=float(_req(k, "threshold")),
        unit=str(_req(k, "unit")),
    )
    dc = _req(raw, "dsl_constraints")
    constraints = Constraints(
        allowed_ops=_ops(_req(dc, "allowed_ops"), "allowed_ops"),
        forbidden_ops=_ops(dc.get("forbidden_ops", []), "forbidden_ops"),
        max_window=int(_req(dc, "max_window")),
        max_depth=int(_req(dc, "max_depth")),
        max_nodes=int(dc.get("max_nodes", 12)),
        max_free_params=int(dc.get("max_free_params", 3)),
        required_refs=frozenset(str(r) for r in dc.get("required_refs", [])),
        session_mask=_window(dc["session_mask"], "session_mask") if dc.get("session_mask")
        else None,
    )
    try:
        direction = Direction(_req(raw, "direction"))
    except ValueError:
        raise SchemaError(f"direction desconhecida: {raw['direction']!r}") from None
    regime = raw.get("regime_filter")
    return Hypothesis(
        id=str(_req(raw, "id")),
        family=str(_req(raw, "family")),
        claim=str(_req(raw, "claim")).strip(),
        who_pays=str(_req(raw, "who_pays")).strip(),
        observable=str(_req(raw, "observable")).strip(),
        direction=direction,
        horizon=parse_duration(str(_req(raw, "horizon"))),
        session_window=_window(_req(raw, "session_window"), "session_window"),
        regime_filter=None if regime is None else str(regime),
        kill_condition=kill,
        dsl_constraints=constraints,
    )


def load_hypothesis(path: Path) -> Hypothesis:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise SchemaError(f"{path}: esperado mapeamento YAML")
    return hypothesis_from_dict(raw)
