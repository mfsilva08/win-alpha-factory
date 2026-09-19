"""Relatório de sessão (SPEC-fase-3 §3.13): HTML estático, arquivo único.

Conteúdo: funil, tentativas com veredito e assinatura estrutural, contador e
``head`` do livro-razão antes e depois, ``SR*`` resultante, distribuição dos
caminhos do CPCV de cada fórmula que chegou ao backtest e o bandit antes/depois.

É zona de verificação: tem métricas. Nunca é entrada de agente.
"""

from __future__ import annotations

import statistics
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from src.backtest.metrics import FoldMetrics
from src.gate.dsr import sr_star
from src.ledger.ledger import Kind, Ledger
from src.reports.charts import strip_chart

TEMPLATES = Path(__file__).with_name("templates")


def environment() -> Environment:
    return Environment(loader=FileSystemLoader(TEMPLATES), undefined=StrictUndefined,
                       autoescape=select_autoescape(["html", "j2"]))


def now_text() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")


@dataclass(frozen=True)
class SessionSummary:
    session_id: str
    count_before: int
    head_before: str
    hypothesis_ids: tuple[str, ...]
    folds: Mapping[str, tuple[FoldMetrics, ...]] = field(default_factory=dict)
    bandit_before: Mapping[str, float | None] = field(default_factory=dict)
    bandit_after: Mapping[str, float | None] = field(default_factory=dict)
    var_sr: float | None = None


def _fmt(v: float | None) -> str:
    return "—" if v is None else f"{v:.4g}"


def _attempts(ledger: Ledger, s: SessionSummary) -> list[dict[str, Any]]:
    prefix = f"{s.session_id}/"
    sigs = {r.trial_id: r.structural_sig for r in ledger.records(Kind.BACKTEST)
            if r.trial_id.startswith(prefix)}
    out: list[dict[str, Any]] = []
    for r in ledger.records(Kind.VERDICT):
        if not r.trial_id.startswith(prefix):
            continue
        assert r.verdict is not None and r.payload is not None
        base = r.trial_id.rsplit("/", 1)[0]
        out.append({"trial_id": base, "hypothesis_id": r.hypothesis_id,
                    "attempt": r.payload.get("attempt"), "verdict": r.verdict.value,
                    "sig": (sigs.get(base) or "—")[:16]})
    return out


def session_report(s: SessionSummary, ledger: Ledger, out: Path) -> Path:
    attempts = _attempts(ledger, s)
    prefix = f"{s.session_id}/"
    backtests = [r for r in ledger.records(Kind.BACKTEST) if r.trial_id.startswith(prefix)]
    crashed = sum(1 for r in backtests if r.crashed)
    count_after = ledger.count()
    if s.var_sr is not None and count_after >= 1:
        sr = f"{sr_star(s.var_sr, count_after):.3f}"
        note = f"(var_sr = {s.var_sr:.4g}, {count_after} tentativas)"
    else:
        sr, note = "pendente", "(var_sr ainda não decidido — SPEC-fase-2 §2.8)"
    paths = []
    for trial_id, folds in sorted(s.folds.items()):
        pnl = [f.net_pnl_points for f in folds]
        paths.append({
            "trial_id": trial_id, "n": len(folds),
            "median": _fmt(statistics.median(pnl)) if pnl else "—",
            "negative": sum(1 for v in pnl if v < 0),
            "pnl_svg": strip_chart(pnl),
            "sharpe_svg": strip_chart([f.sharpe for f in folds]),
        })
    families = sorted(set(s.bandit_before) | set(s.bandit_after))
    html = environment().get_template("session.html.j2").render(
        title=f"Sessão {s.session_id}",
        generated_at=now_text(),
        session_id=s.session_id,
        funnel={"hypotheses": len(s.hypothesis_ids),
                "formulas": len(attempts) + crashed,
                "evaluations": len(backtests),
                "accepted": sum(1 for a in attempts if a["verdict"] == "ACCEPTED")},
        ledger={"count_before": s.count_before, "count_after": count_after,
                "head_before": s.head_before, "head_after": ledger.head(),
                "sr_star": sr, "sr_star_note": note},
        attempts=attempts,
        paths=paths,
        bandit=[{"family": f, "before": _fmt(s.bandit_before.get(f)),
                 "after": _fmt(s.bandit_after.get(f))} for f in families],
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8", newline="\n")
    return out


def summary_from_ledger(ledger: Ledger, session_id: str) -> SessionSummary:
    """Para ``cli report``: sem as métricas em memória, só o que o livro-razão guarda."""
    prefix = f"{session_id}/"
    hyps = sorted({r.hypothesis_id for r in ledger.records()
                   if r.trial_id.startswith(prefix) and r.hypothesis_id})
    return SessionSummary(session_id, count_before=0, head_before="(não registrado)",
                          hypothesis_ids=tuple(hyps))
