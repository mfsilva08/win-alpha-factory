"""Schema da telemetria que o robô grava (SPEC-fase-4 §4.1).

Uma linha **por barra**, tenha havido ordem ou não: dias sem operação são
exatamente os que permitem medir decaimento. CSV com cabeçalho, um arquivo por
pregão, ``win_YYYYMMDD.csv``, em ``MQL5/Files/telemetry/``.
"""

from __future__ import annotations

import csv
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from itertools import pairwise
from pathlib import Path

TELEMETRY_COLUMNS: tuple[str, ...] = (
    "ts",  # ISO 8601 com timezone, momento da gravação
    "bar_time",  # fechamento da barra avaliada, hora local da B3
    "signal",  # valor do sinal calculado (vazio no aquecimento ou com dado faltando)
    "threshold",  # limiar em vigor
    "decision",  # NONE | OPEN_LONG | OPEN_SHORT | CLOSE | REJECTED
    "contracts",  # tamanho decidido, 0 se não operou
    "ref_price",  # último negócio no momento da decisão
    "fill_price",  # preço efetivo, vazio se não houve ordem
    "slippage_ticks",  # (fill - ref) / tick_size, com sinal contra nós
    "order_status",  # vazio | FILLED | PARTIAL | REJECTED_<motivo>
    "position",  # posição após a barra
    "daily_pnl_points",  # acumulado do dia, em pontos
    "guard_blocked",  # vazio | CUTOFF | MARGIN | GRID | DAILY_LOSS | BREAKER | DATA_GAP
    "kill_metric_name",  # nome da métrica declarada na hipótese
    "kill_metric_value",  # valor medido nesta barra, vazio se não aplicável
)


class TelemetryError(Exception):
    """Arquivo de telemetria fora do schema."""


class Decision(StrEnum):
    NONE = "NONE"
    OPEN_LONG = "OPEN_LONG"
    OPEN_SHORT = "OPEN_SHORT"
    CLOSE = "CLOSE"
    REJECTED = "REJECTED"


class GuardBlock(StrEnum):
    CUTOFF = "CUTOFF"
    MARGIN = "MARGIN"
    GRID = "GRID"
    DAILY_LOSS = "DAILY_LOSS"
    BREAKER = "BREAKER"
    DATA_GAP = "DATA_GAP"  # referência sem barra no mesmo minuto: estado não avança


@dataclass(frozen=True)
class TelemetryRow:
    ts: str
    bar_time: datetime
    signal: float | None
    threshold: float
    decision: Decision
    contracts: int
    ref_price: float
    fill_price: float | None
    slippage_ticks: float | None
    order_status: str
    position: int
    daily_pnl_points: float
    guard_blocked: GuardBlock | None
    kill_metric_name: str
    kill_metric_value: float | None


def file_name(day: date) -> str:
    return f"win_{day:%Y%m%d}.csv"


def _opt_float(s: str) -> float | None:
    if s.strip() == "":
        return None
    v = float(s)
    return None if math.isnan(v) else v


def _row(raw: dict[str, str], line: int) -> TelemetryRow:
    try:
        return TelemetryRow(
            ts=raw["ts"],
            bar_time=datetime.fromisoformat(raw["bar_time"]),
            signal=_opt_float(raw["signal"]),
            threshold=float(raw["threshold"]),
            decision=Decision(raw["decision"]),
            contracts=int(raw["contracts"]),
            ref_price=float(raw["ref_price"]),
            fill_price=_opt_float(raw["fill_price"]),
            slippage_ticks=_opt_float(raw["slippage_ticks"]),
            order_status=raw["order_status"],
            position=int(raw["position"]),
            daily_pnl_points=float(raw["daily_pnl_points"]),
            guard_blocked=GuardBlock(raw["guard_blocked"]) if raw["guard_blocked"] else None,
            kill_metric_name=raw["kill_metric_name"],
            kill_metric_value=_opt_float(raw["kill_metric_value"]),
        )
    except (ValueError, KeyError) as e:
        raise TelemetryError(f"linha {line}: {e}") from None


def read_telemetry(path: Path) -> list[TelemetryRow]:
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if tuple(reader.fieldnames or ()) != TELEMETRY_COLUMNS:
            raise TelemetryError(f"{path.name}: cabeçalho fora do schema")
        rows = [_row(r, i + 2) for i, r in enumerate(reader)]
    times = [r.bar_time for r in rows]
    if any(b <= a for a, b in pairwise(times)):
        raise TelemetryError(f"{path.name}: bar_time fora de ordem ou repetido")
    return rows


def _fmt(v: float | None) -> str:
    return "" if v is None else repr(float(v))


def write_telemetry(path: Path, rows: Sequence[TelemetryRow]) -> None:
    """Escrita no mesmo formato do robô — usada por simulações e testes."""
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(TELEMETRY_COLUMNS)
        for r in rows:
            w.writerow([
                r.ts, r.bar_time.isoformat(timespec="minutes"), _fmt(r.signal),
                repr(r.threshold), r.decision.value, r.contracts, repr(r.ref_price),
                _fmt(r.fill_price), _fmt(r.slippage_ticks), r.order_status, r.position,
                repr(r.daily_pnl_points), r.guard_blocked.value if r.guard_blocked else "",
                r.kill_metric_name, _fmt(r.kill_metric_value),
            ])
