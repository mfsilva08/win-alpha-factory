"""Relatório de monitoramento (SPEC-fase-4 §4.6): HTML estático, gerado pelo coletor.

- série da ``kill_metric`` agregada contra o limiar declarado
- slippage realizado contra o modelado, por semana
- PnL acumulado contra o intervalo interquartil dos caminhos do CPCV (se informado)
- rejeições e bloqueios de guard, por tipo
- estado atual de cada hipótese: ``ALIVE`` · ``INSUFFICIENT`` · ``DEAD``
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any

from src.agents.schemas import Hypothesis
from src.ledger.ledger import Kind, Ledger
from src.ops.coletor import KillState, evaluate_kill
from src.reports.charts import line_chart
from src.reports.session import environment, now_text


def _dailies(ledger: Ledger, hypothesis_id: str) -> list[dict[str, Any]]:
    return [dict(r.payload) for r in ledger.records(Kind.DAILY)
            if r.hypothesis_id == hypothesis_id and r.payload is not None]


def _rolling(values: Sequence[float | None], h: Hypothesis) -> list[float | None]:
    out: list[float | None] = []
    seen: list[float] = []
    for v in values:
        if v is not None:
            seen.append(v)
        state, agg = evaluate_kill(h.kill_condition, seen)
        out.append(agg if state is not KillState.INSUFFICIENT else None)
    return out


def _weeks(days: list[dict[str, Any]]) -> list[dict[str, str]]:
    by_week: dict[str, list[dict[str, Any]]] = {}
    for d in days:
        y, w, _ = date.fromisoformat(d["day"]).isocalendar()
        by_week.setdefault(f"{y}-S{w:02d}", []).append(d)
    out = []
    for week, ds in sorted(by_week.items()):
        trades = sum(int(d["trades"]) for d in ds)
        slips = [(float(d["slippage_ticks_avg"]), int(d["trades"])) for d in ds
                 if d.get("slippage_ticks_avg") is not None and int(d["trades"]) > 0]
        weighted = (sum(s * n for s, n in slips) / sum(n for _, n in slips)) if slips else None
        out.append({"week": week, "trades": str(trades),
                    "slippage": "—" if weighted is None else f"{weighted:.2f}"})
    return out


def monitor_report(
    ledger: Ledger,
    hypotheses: Sequence[Hypothesis],
    out: Path,
    modeled_slippage: float,
    cpcv_band: Mapping[str, tuple[float, float, int]] | None = None,
) -> Path:
    """``cpcv_band[h.id] = (q1, q3, n_dias)``: IQR do PnL total dos caminhos em n_dias."""
    killed = {r.hypothesis_id for r in ledger.records(Kind.KILL)}
    items = []
    for h in hypotheses:
        days = _dailies(ledger, h.id)
        labels = [d["day"][5:] for d in days]
        raw = [d.get("kill_metric_daily") for d in days]
        measured = [float(v) for v in raw if v is not None]
        state, agg = evaluate_kill(h.kill_condition, measured)
        if h.id in killed:
            state = KillState.DEAD
        k = h.kill_condition
        kill_svg = line_chart(
            [("diário", [None if v is None else float(v) for v in raw]),
             (f"{k.aggregation} {k.window_days}d", _rolling(
                 [None if v is None else float(v) for v in raw], h))],
            labels, hline=k.threshold, hline_label=f"limiar {k.operator} {k.threshold:g}",
        )
        cum: list[float | None] = []
        total = 0.0
        for d in days:
            total += float(d["net_pnl_points"])
            cum.append(total)
        band = None
        if cpcv_band and h.id in cpcv_band:
            q1, q3, n = cpcv_band[h.id]
            band = [(q1 * (i + 1) / n, q3 * (i + 1) / n) for i in range(len(days))]
        guards: Counter[str] = Counter()
        for d in days:
            guards.update({str(g): int(c) for g, c in d.get("guard_blocks", {}).items()})
        items.append({
            "id": h.id, "metric": k.metric, "aggregation": k.aggregation,
            "window": k.window_days, "rule": f"{k.aggregation} {k.window_days}d "
                                             f"{k.operator} {k.threshold:g} {k.unit}",
            "days": len(measured), "value": "—" if agg is None else f"{agg:.4g}",
            "state": state.value, "kill_svg": kill_svg,
            "pnl_svg": line_chart([("PnL acumulado (pontos)", cum)], labels, band=band),
            "has_band": band is not None, "weeks": _weeks(days),
            "rejections": sum(int(d["rejections"]) for d in days),
            "guards": dict(sorted(guards.items())),
        })
    html = environment().get_template("monitor.html.j2").render(
        title="Monitoramento", generated_at=now_text(), hypotheses=items,
        modeled_slippage=f"{modeled_slippage:g}",
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8", newline="\n")
    return out
