"""Paridade entre o sinal que o robô gravou e o sinal recalculado offline (§3.12, §4.5).

Divergência aqui significa que o robô operando **não é** o robô testado. É alerta
de divergência, e é sério. Critério: o mesmo do M1, ``|a−b| / max(1, |a|) < 1e-9``.

Linhas com ``guard_blocked = DATA_GAP`` não entram: nelas o robô não avançou o
estado. Barras do frame sem linha correspondente também são reportadas.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import numpy as np

from src.codegen.telemetry import GuardBlock, TelemetryRow, read_telemetry
from src.dsl.ast import Node
from src.dsl.eval_vectorized import eval_vec
from src.dsl.market import MarketFrame

TOLERANCE = 1e-9


@dataclass(frozen=True)
class ParityResult:
    compared: int
    max_error: float
    missing_in_log: int  # barras do frame (dentro do período logado) sem linha
    nan_mismatch: int  # um lado NaN/vazio e o outro não
    ok: bool

    def as_dict(self) -> dict[str, object]:
        return {"compared": self.compared, "max_error": self.max_error,
                "missing_in_log": self.missing_in_log, "nan_mismatch": self.nan_mismatch,
                "ok": self.ok}


def compare(ast: Node, rows: Sequence[TelemetryRow], frame: MarketFrame) -> ParityResult:
    vec = eval_vec(ast, frame)
    index = {t: i for i, t in enumerate(frame.ts.astype("datetime64[m]").astype(datetime))}
    usable = [r for r in rows if r.guard_blocked is not GuardBlock.DATA_GAP]
    errors: list[float] = []
    nan_mismatch = 0
    seen: set[int] = set()
    for r in usable:
        i = index.get(r.bar_time)
        if i is None:
            continue
        seen.add(i)
        a = float(vec[i])
        if np.isnan(a) or r.signal is None:
            nan_mismatch += int(np.isnan(a) != (r.signal is None))
            continue
        errors.append(abs(a - r.signal) / max(1.0, abs(a)))
    if rows:
        lo, hi = rows[0].bar_time, rows[-1].bar_time
        in_period = [i for t, i in index.items() if lo <= t <= hi]
        missing = sum(1 for i in in_period if i not in seen)
    else:
        missing = 0
    max_err = max(errors) if errors else 0.0
    ok = bool(errors) and max_err < TOLERANCE and nan_mismatch == 0 and missing == 0
    return ParityResult(len(errors), max_err, missing, nan_mismatch, ok)


def parity_check(ast: Node, mq5_log: Path, frame: MarketFrame) -> ParityResult:
    """Na implantação: o log do Strategy Tester contra a AST vetorizada."""
    return compare(ast, read_telemetry(mq5_log), frame)


def weekly_parity(days: Sequence[date], telemetry_dir: Path, ast: Node,
                  frame: MarketFrame) -> ParityResult:
    """Semanal, sobre dados de produção (§4.5). ``frame`` cobre os dias e o aquecimento."""
    from src.codegen.telemetry import file_name

    rows: list[TelemetryRow] = []
    for d in sorted(days):
        path = telemetry_dir / file_name(d)
        if path.exists():
            rows.extend(read_telemetry(path))
    return compare(ast, rows, frame)
