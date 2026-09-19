"""Robô simulado em Python: gera a telemetria que o ``.mq5`` gravaria.

Usa o mesmo ``IncrementalState`` e os mesmos guards de ``codegen/guards.py``.
Serve para testar paridade e coletor sem MetaTrader.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from pathlib import Path

from src.codegen.guards import GuardLimits, decide, guard_order, must_flatten, to_grid
from src.codegen.telemetry import Decision, TelemetryRow, file_name, write_telemetry
from src.dsl.ast import Node
from src.dsl.eval_incremental import IncrementalState
from src.dsl.market import MarketFrame

LIMITS = GuardLimits(cutoff_min=17 * 60 + 50, session=(9 * 60, 17 * 60 + 55), tick_size=5,
                     margin_per_contract=155.0, daily_loss_points=1500.0, max_rejections=3)


def simulate(ast: Node, frame: MarketFrame, threshold: float = 1.5, direction: int = 1,
             kill_metric: str = "spread_ticks", kill_value: float = 1.0,
             equity: float = 5000.0, max_holding_min: int = 30) -> list[TelemetryRow]:
    st = IncrementalState(ast)
    rows: list[TelemetryRow] = []
    position, entry, entry_t = 0, 0.0, datetime(1970, 1, 1)  # noqa: DTZ001 hora local da B3
    for i, bar in enumerate(frame.bars()):
        st.update(bar)
        sig = st.signal()
        tod = bar.ts.hour * 60 + bar.ts.minute
        decision, lots, guard = Decision.NONE, 0, None
        if position != 0:
            held = int((bar.ts - entry_t).total_seconds() // 60)
            if must_flatten(tod, held, LIMITS, max_holding_min):
                decision, position = Decision.CLOSE, 0
        elif i >= st.warmup_bars():
            d = decide(None if math.isnan(sig) else sig, threshold, direction, position)
            if d in (Decision.OPEN_LONG, Decision.OPEN_SHORT):
                side = 1 if d is Decision.OPEN_LONG else -1
                px = to_grid(bar.close, 5, side)
                guard = guard_order(LIMITS, tod, 1, equity, px, 0.0, 0)
                if guard is None:
                    decision, lots, position, entry, entry_t = d, 1, side, px, bar.ts
        rows.append(TelemetryRow(
            ts=f"{bar.ts.isoformat()}Z", bar_time=bar.ts,
            signal=None if math.isnan(sig) else sig, threshold=threshold, decision=decision,
            contracts=lots, ref_price=bar.close, fill_price=entry if lots else None,
            slippage_ticks=0.0 if lots else None, order_status="FILLED" if lots else "",
            position=position, daily_pnl_points=0.0, guard_blocked=guard,
            kill_metric_name=kill_metric, kill_metric_value=kill_value,
        ))
    return rows


def write_days(rows: list[TelemetryRow], out: Path) -> list[date]:
    """Um arquivo por pregão, como o robô."""
    by_day: dict[date, list[TelemetryRow]] = {}
    for r in rows:
        by_day.setdefault(r.bar_time.date(), []).append(r)
    for d, rs in by_day.items():
        write_telemetry(out / file_name(d), rs)
    return sorted(by_day)
