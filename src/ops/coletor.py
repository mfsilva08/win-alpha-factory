"""O job diário (SPEC-fase-4 §4.3). Roda de madrugada, por cron.

1. lê o CSV do pregão
2. valida o schema e a contagem de barras esperada
3. agrega as métricas do dia
4. grava o resumo diário no livro-razão (``kind='daily'``)
5. recalcula a ``kill_condition`` na janela declarada
6. compara com o limiar e decide o estado da hipótese (``kind='kill'`` ao cruzar)
7. emite alertas

**O coletor não desliga o robô.** Ele marca e alerta. Nenhuma função daqui escreve
em ``MQL5/Files`` nem fala com o terminal — há teste para isso.
"""

from __future__ import annotations

import csv
import statistics
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import Any

from src.agents.schemas import Hypothesis, KillSpec
from src.backtest.costs import CostModel
from src.codegen.telemetry import (
    Decision,
    GuardBlock,
    TelemetryError,
    TelemetryRow,
    file_name,
    read_telemetry,
)
from src.ledger.ledger import Kind, Ledger, LedgerRecord
from src.ops.alerts import Alert, AlertEvent, event

SLIPPAGE_TOLERANCE = 0.30  # ±30% do modelado
EXPECTED_GUARDS: frozenset[GuardBlock] = frozenset({GuardBlock.CUTOFF})


class KillState(StrEnum):
    ALIVE = "ALIVE"
    INSUFFICIENT = "INSUFFICIENT"
    DEAD = "DEAD"


class AlreadyCollected(Exception):
    """O dia já tem resumo no livro-razão: coletar de novo duplicaria a série."""


@dataclass(frozen=True)
class DailySummary:
    day: str
    strategy_id: str
    hypothesis_id: str
    bars_expected: int
    bars_recorded: int
    trades: int
    net_pnl_points: float
    gross_pnl_points: float
    slippage_ticks_avg: float | None
    rejections: int
    guard_blocks: dict[str, int]
    kill_metric_daily: float | None  # a agregação do dia, ainda não a móvel


@dataclass(frozen=True)
class CollectResult:
    summary: DailySummary | None
    kill_state: KillState
    kill_value: float | None
    alerts: tuple[AlertEvent, ...]


# ---------------------------------------------------------------- a condição de morte


def evaluate_kill(spec: KillSpec, series: Sequence[float]) -> tuple[KillState, float | None]:
    """Agregação móvel sobre os últimos ``window_days`` dias **com medição**."""
    window = list(series)[-spec.window_days:]
    if len(window) < spec.window_days:
        return KillState.INSUFFICIENT, None
    agg = statistics.median(window) if spec.aggregation == "mediana" else statistics.fmean(window)
    crossed = agg < spec.threshold if spec.operator == "<" else agg > spec.threshold
    return (KillState.DEAD if crossed else KillState.ALIVE), agg


# ---------------------------------------------------------------- calendário


def load_calendar(path: Path) -> dict[date, int]:
    """CSV ``day,bars_expected`` — uma linha por pregão (dia zero, bloco 1)."""
    out: dict[date, int] = {}
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            out[date.fromisoformat(row["day"])] = int(row["bars_expected"])
    return out


# ---------------------------------------------------------------- agregação


def _aggregate(values: list[float], spec: KillSpec) -> float | None:
    if not values:
        return None
    return statistics.median(values) if spec.aggregation == "mediana" else statistics.fmean(values)


def summarize(rows: Sequence[TelemetryRow], day: date, h: Hypothesis, strategy_id: str,
              bars_expected: int, costs: CostModel) -> DailySummary:
    opens = [r for r in rows if r.decision in (Decision.OPEN_LONG, Decision.OPEN_SHORT)]
    slips = [r.slippage_ticks for r in opens if r.slippage_ticks is not None]
    net = rows[-1].daily_pnl_points if rows else 0.0
    contracts = sum(r.contracts for r in opens)
    fees_points = costs.fees_brl(contracts) / costs.point_value
    guards = Counter(r.guard_blocked.value for r in rows if r.guard_blocked is not None)
    kill_values = [r.kill_metric_value for r in rows if r.kill_metric_value is not None]
    return DailySummary(
        day=day.isoformat(),
        strategy_id=strategy_id,
        hypothesis_id=h.id,
        bars_expected=bars_expected,
        bars_recorded=len(rows),
        trades=len(opens),
        net_pnl_points=net,
        gross_pnl_points=net + fees_points,
        slippage_ticks_avg=statistics.fmean(slips) if slips else None,
        rejections=sum(1 for r in rows if r.decision is Decision.REJECTED
                       or r.order_status.startswith("REJECTED")),
        guard_blocks=dict(sorted(guards.items())),
        kill_metric_daily=_aggregate(kill_values, h.kill_condition),
    )


def _series(ledger: Ledger, hypothesis_id: str) -> list[float]:
    out: list[float] = []
    for r in ledger.records(Kind.DAILY):
        if r.hypothesis_id == hypothesis_id and r.payload is not None:
            v = r.payload.get("kill_metric_daily")
            if v is not None:
                out.append(float(v))
    return out


def _already(ledger: Ledger, hypothesis_id: str, day: date) -> bool:
    return any(r.hypothesis_id == hypothesis_id and r.payload is not None
               and r.payload.get("day") == day.isoformat()
               for r in ledger.records(Kind.DAILY))


def _is_dead(ledger: Ledger, hypothesis_id: str) -> bool:
    return any(r.hypothesis_id == hypothesis_id for r in ledger.records(Kind.KILL))


def _alerts(s: DailySummary, day: date, costs: CostModel) -> list[AlertEvent]:
    out: list[AlertEvent] = []
    hid = s.hypothesis_id
    if s.bars_recorded != s.bars_expected:
        out.append(event(Alert.OPERACIONAL, day, hid,
                         f"barras gravadas {s.bars_recorded} != esperadas {s.bars_expected}"))
    if s.rejections > 0:
        out.append(event(Alert.OPERACIONAL, day, hid, f"{s.rejections} ordem(ns) rejeitada(s)"))
    unexpected = {k: v for k, v in s.guard_blocks.items()
                  if GuardBlock(k) not in EXPECTED_GUARDS}
    if unexpected:
        out.append(event(Alert.OPERACIONAL, day, hid, f"guards inesperados: {unexpected}"))
    if s.slippage_ticks_avg is not None:
        modeled = costs.slippage_ticks
        off = (abs(s.slippage_ticks_avg - modeled) / modeled if modeled
               else abs(s.slippage_ticks_avg))
        if off > SLIPPAGE_TOLERANCE:
            out.append(event(Alert.DIVERGENCIA, day, hid,
                             f"slippage médio {s.slippage_ticks_avg:.2f} ticks vs "
                             f"{modeled} modelado"))
    return out


# ---------------------------------------------------------------- o job


def collect(day: date, telemetry_dir: Path, ledger: Ledger, h: Hypothesis, strategy_id: str,
            bars_expected: int, costs: CostModel) -> CollectResult:
    if _already(ledger, h.id, day):
        raise AlreadyCollected(f"{h.id} {day}")
    path = telemetry_dir / file_name(day)
    if not path.exists():
        alert = event(Alert.OPERACIONAL, day, h.id, f"telemetria ausente: {path.name}")
        return CollectResult(None, KillState.INSUFFICIENT, None, (alert,))
    try:
        rows = read_telemetry(path)
    except TelemetryError as e:
        alert = event(Alert.OPERACIONAL, day, h.id, f"telemetria fora do schema: {e}")
        return CollectResult(None, KillState.INSUFFICIENT, None, (alert,))

    summary = summarize(rows, day, h, strategy_id, bars_expected, costs)
    g = ledger.genesis_info()
    file_hash = sha256(path.read_bytes()).hexdigest()
    payload: dict[str, Any] = asdict(summary)
    ledger.append(LedgerRecord(
        trial_id=f"daily/{h.id}/{day.isoformat()}", kind=Kind.DAILY, data_hash=file_hash,
        config_hash=g.config_hash, hypothesis_id=h.id, payload=payload,
    ))

    state, value = evaluate_kill(h.kill_condition, _series(ledger, h.id))
    alerts = _alerts(summary, day, costs)
    if state is KillState.DEAD:
        if not _is_dead(ledger, h.id):
            ledger.append(LedgerRecord(
                trial_id=f"kill/{h.id}/{day.isoformat()}", kind=Kind.KILL, data_hash=file_hash,
                config_hash=g.config_hash, hypothesis_id=h.id,
                payload={"day": day.isoformat(), "metric": h.kill_condition.metric,
                         "aggregation": h.kill_condition.aggregation,
                         "window_days": h.kill_condition.window_days,
                         "threshold": h.kill_condition.threshold, "value": value},
            ))
        alerts.append(event(Alert.DECAIMENTO, day, h.id,
                            f"{h.kill_condition.metric} {h.kill_condition.aggregation} "
                            f"= {value} cruzou {h.kill_condition.operator} "
                            f"{h.kill_condition.threshold}"))
    return CollectResult(summary, state, value, tuple(alerts))
