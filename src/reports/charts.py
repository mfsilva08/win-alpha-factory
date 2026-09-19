"""Gráficos em SVG inline — sem JS, sem dependência, abrem igual daqui a seis meses."""

from __future__ import annotations

import math
from collections.abc import Sequence
from html import escape

PALETTE = ("#2563eb", "#d97706", "#059669", "#7c3aed")


def _finite(xs: Sequence[float | None]) -> list[float]:
    return [x for x in xs if x is not None and math.isfinite(x)]


def line_chart(
    series: Sequence[tuple[str, Sequence[float | None]]],
    x_labels: Sequence[str],
    *,
    hline: float | None = None,
    hline_label: str = "",
    band: Sequence[tuple[float, float]] | None = None,
    width: int = 680,
    height: int = 220,
) -> str:
    """Linhas com eixo x categórico; ``None`` interrompe a linha."""
    values = [v for _, s in series for v in _finite(s)]
    if hline is not None:
        values.append(hline)
    if band:
        values += [lo for lo, _ in band] + [hi for _, hi in band]
    n = len(x_labels)
    if not values or n == 0:
        return '<p class="muted">Sem dados para o gráfico.</p>'
    lo, hi = min(values), max(values)
    if hi - lo < 1e-12:
        lo, hi = lo - 1.0, hi + 1.0
    pad_l, pad_r, pad_t, pad_b = 56, 12, 12, 28
    w, h = width - pad_l - pad_r, height - pad_t - pad_b

    def x(i: int) -> float:
        return pad_l + (w * i / (n - 1) if n > 1 else w / 2)

    def y(v: float) -> float:
        return pad_t + h * (1 - (v - lo) / (hi - lo))

    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" class="chart">']
    for frac in (0.0, 0.5, 1.0):
        tick = lo + (hi - lo) * frac
        parts.append(f'<line x1="{pad_l}" x2="{width - pad_r}" y1="{y(tick):.1f}" '
                     f'y2="{y(tick):.1f}" class="grid"/>')
        parts.append(f'<text x="{pad_l - 6}" y="{y(tick) + 4:.1f}" text-anchor="end" '
                     f'class="tick">{tick:.4g}</text>')
    if band:
        upper = " ".join(f"{x(i):.1f},{y(b[1]):.1f}" for i, b in enumerate(band))
        lower = " ".join(f"{x(i):.1f},{y(b[0]):.1f}" for i, b in reversed(list(enumerate(band))))
        parts.append(f'<polygon points="{upper} {lower}" class="band"/>')
    if hline is not None:
        parts.append(f'<line x1="{pad_l}" x2="{width - pad_r}" y1="{y(hline):.1f}" '
                     f'y2="{y(hline):.1f}" class="threshold"/>')
        parts.append(f'<text x="{width - pad_r}" y="{y(hline) - 4:.1f}" text-anchor="end" '
                     f'class="tick">{escape(hline_label)}</text>')
    for k, (name, s) in enumerate(series):
        color = PALETTE[k % len(PALETTE)]
        seg: list[str] = []
        for i, v in enumerate(s):
            if v is None or not math.isfinite(v):
                if len(seg) > 1:
                    parts.append(f'<polyline points="{" ".join(seg)}" fill="none" '
                                 f'stroke="{color}" stroke-width="2"/>')
                seg = []
                continue
            seg.append(f"{x(i):.1f},{y(v):.1f}")
        if len(seg) > 1:
            parts.append(f'<polyline points="{" ".join(seg)}" fill="none" stroke="{color}" '
                         'stroke-width="2"/>')
        elif len(seg) == 1:
            cx, cy = seg[0].split(",")
            parts.append(f'<circle cx="{cx}" cy="{cy}" r="3" fill="{color}"/>')
    step = max(1, n // 6)
    for i in range(0, n, step):
        parts.append(f'<text x="{x(i):.1f}" y="{height - 8}" text-anchor="middle" '
                     f'class="tick">{escape(x_labels[i])}</text>')
    parts.append("</svg>")
    legend = " ".join(f'<span class="key" style="--c:{PALETTE[k % len(PALETTE)]}">'
                      f"{escape(name)}</span>" for k, (name, _) in enumerate(series))
    return "".join(parts) + f'<div class="legend">{legend}</div>'


def strip_chart(values: Sequence[float], *, width: int = 680, height: int = 70,
                zero: bool = True) -> str:
    """Um ponto por caminho do CPCV: mostra a distribuição, nunca só a média."""
    vs = _finite(values)
    if not vs:
        return '<p class="muted">Sem dados.</p>'
    lo, hi = min([*vs, 0.0] if zero else vs), max([*vs, 0.0] if zero else vs)
    if hi - lo < 1e-12:
        lo, hi = lo - 1.0, hi + 1.0
    pad = 16

    def x(v: float) -> float:
        return pad + (width - 2 * pad) * (v - lo) / (hi - lo)

    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" class="chart">']
    if zero:
        parts.append(f'<line x1="{x(0):.1f}" x2="{x(0):.1f}" y1="6" y2="{height - 18}" '
                     'class="threshold"/>')
    for i, v in enumerate(vs):
        jitter = 14 + (i * 7) % (height - 38)
        parts.append(f'<circle cx="{x(v):.1f}" cy="{jitter}" r="3.5" class="dot"/>')
    parts.append(f'<text x="{pad}" y="{height - 4}" class="tick">{lo:.4g}</text>')
    parts.append(f'<text x="{width - pad}" y="{height - 4}" text-anchor="end" '
                 f'class="tick">{hi:.4g}</text>')
    parts.append("</svg>")
    return "".join(parts)
