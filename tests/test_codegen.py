"""SPEC-fase-3 §3.12 e SPEC-fase-4 §4.1–4.2: transpilador, telemetria e paridade."""

from __future__ import annotations

import dataclasses
import json
import re
from datetime import time, timedelta
from pathlib import Path

import pytest

from src.agents.schemas import load_hypothesis
from src.backtest.costs import load_costs
from src.backtest.engine import TradeRules
from src.catalog.metrics import INSTRUMENTABLE_METRICS
from src.codegen.parity import compare, parity_check, weekly_parity
from src.codegen.telemetry import (
    TELEMETRY_COLUMNS,
    GuardBlock,
    TelemetryError,
    TelemetryRow,
    read_telemetry,
    write_telemetry,
)
from src.codegen.transpiler import (
    NonInstrumentableKillCondition,
    NonTranspilable,
    TranspileError,
    flatten,
    package,
    transpile,
)
from src.dsl.ast import node_count
from src.dsl.market import MarketFrame
from src.dsl.parser import parse
from tests.dsl_factory import synthetic_frame
from tests.robot_sim import simulate, write_days

ROOT = Path(__file__).resolve().parents[1]
H001 = load_hypothesis(ROOT / "docs" / "exemplos" / "h001.yaml")
AST = parse('mul(in_window("10:30","16:30"), sub(zscore(ret(ref("ES"),2),55),'
            " zscore(ret(close(),2),55)))")
RULES = TradeRules(1.5, 2, timedelta(minutes=30), 150, 300, time(17, 50),
                   (time(10, 30), time(16, 30)))
COSTS = load_costs(ROOT / "config" / "costs.yaml")
FrameRows = tuple[MarketFrame, list[TelemetryRow]]


@pytest.fixture(scope="module")
def mq5() -> str:
    return transpile(AST, H001, RULES, COSTS)


# ---------------------------------------------------------------- texto gerado


def test_deterministico(mq5: str) -> None:
    assert transpile(AST, H001, RULES, COSTS) == mq5


def test_nunca_le_indice_zero(mq5: str) -> None:
    calls = re.findall(r"\b(?:iClose|iOpen|iHigh|iLow|iTime|iVolume|iBarShift|CopyRates)\(([^;]*?)\)",
                       mq5)
    assert calls
    for args in calls:
        assert not re.search(r",\s*0\s*(,|$)", args), args
    assert "iTime(_Symbol, PERIOD_M1, 1)" in mq5
    assert "if(shift < 1) return false;" in mq5


def test_ontick_sai_na_primeira_linha(mq5: str) -> None:
    body = mq5.split("void OnTick(void)", 1)[1]
    first = next(ln.strip() for ln in body.splitlines() if ln.strip() not in ("", "{"))
    assert first.startswith("if(iTime(_Symbol, PERIOD_M1, 1) == g_last_processed)")
    assert "return;" in first


def test_guards_presentes(mq5: str) -> None:
    guard = mq5.split("string GuardOrder(", 1)[1].split("\n  }", 1)[0]
    for name in ("BREAKER", "DAILY_LOSS", "CUTOFF", "MARGIN", "GRID"):
        assert f'return "{name}"' in guard
    assert "ToGrid(" in mq5 and "MaxLots()" in guard


def test_cabecalho_da_telemetria_bate_com_o_schema(mq5: str) -> None:
    header = mq5.split("if(!exists)", 1)[1].split(");", 1)[0]
    assert tuple(re.findall(r'"([a-z_]+)"', header)) == TELEMETRY_COLUMNS


def test_uma_linha_por_barra_inclusive_sem_ordem(mq5: str) -> None:
    body = mq5.split("void ProcessBar(", 1)[1].split("\nint OnInit", 1)[0]
    assert body.rstrip().endswith("LogDecision(b, sig, decision, lots, ref_price, fill, slip, status, guard);\n  }\n\n//+------------------------------------------------------------------+") or \
        "LogDecision(b, sig, decision, lots, ref_price, fill, slip, status, guard);" in body
    assert '"DATA_GAP"' in body


def test_constantes_congeladas(mq5: str) -> None:
    assert "#define SIGNAL_THRESHOLD    1.5" in mq5
    assert "#define CUTOFF_MIN          1070" in mq5
    assert "#define DIRECTION           1" in mq5
    assert "#define WARMUP_BARS         165" in mq5
    assert f'#define KILL_METRIC_NAME    "{H001.kill_condition.metric}"' in mq5
    assert "InpRefES" in mq5 and "iBarShift(InpRefES" in mq5
    assert "iBarShift(InpRefWDO" not in mq5  # h001 não usa WDO


def test_chaves_e_parenteses_balanceados(mq5: str) -> None:
    code = re.sub(r'"(?:\\.|[^"\\])*"', '""', re.sub(r"//[^\n]*", "", mq5))
    assert code.count("{") == code.count("}")
    assert code.count("(") == code.count(")")


def test_sem_rede_nem_llm(mq5: str) -> None:
    for forbidden in ("WebRequest", "SocketCreate", "http", "anthropic", "InternetOpen"):
        assert forbidden not in mq5


# ---------------------------------------------------------------- recusas e cobertura


def test_kill_condition_nao_instrumentavel() -> None:
    bad = dataclasses.replace(H001, kill_condition=dataclasses.replace(
        H001.kill_condition, metric="book_imbalance"))
    with pytest.raises(NonInstrumentableKillCondition):
        transpile(AST, bad, RULES, COSTS)


def test_vwap_e_raiz_invalida_recusados() -> None:
    with pytest.raises(NonTranspilable):
        transpile(parse("zscore(vwap(),5)"), H001, RULES, COSTS)
    with pytest.raises(TranspileError):
        transpile(parse("rolling_mean(close(),5)"), H001, RULES, COSTS)


@pytest.mark.parametrize("metric", sorted(INSTRUMENTABLE_METRICS))
def test_toda_metrica_instrumentavel_transpila(metric: str) -> None:
    h = dataclasses.replace(H001, kill_condition=dataclasses.replace(H001.kill_condition,
                                                                     metric=metric))
    out = transpile(AST, h, RULES, COSTS)
    assert f'"{metric}"' in out


def test_todos_os_operadores_transpilam() -> None:
    text = ('and_(or_(gt(rank_ts(delta(high(),3),8), clip(zscore(ema(low(),5),13),-2,2)),'
            ' lt(rolling_max(volume(),5), rolling_min(trades(),5))),'
            ' and_(gt(div(rolling_std(close(),21), rolling_mean(ref("WDO"),21)),'
            ' ret(lag(close(),2),3)),'
            ' gt(minutes_since_open(), add(lag(volume(),2), volume()))))')
    ast = parse(text)
    lines = flatten(ast)
    assert len(lines) == node_count(ast)
    for cls in ("CRankTs", "CLag", "CClip", "CZScore", "CEma", "CExtreme", "CRollingStd",
                "CRollingMean", "CRef(R_WDO)", "CMinutesSinceOpen", "CBinary(B_DIV",
                "CBinary(B_AND", "CBinary(B_OR", "CBinary(B_LT", "CField(F_TRADES)"):
        assert any(cls in ln for ln in lines), cls
    out = transpile(ast, H001, RULES, COSTS)
    assert "iBarShift(InpRefWDO" in out and "iBarShift(InpRefES" not in out


def test_pos_ordem_raiz_por_ultimo() -> None:
    lines = flatten(AST)
    assert lines[-1].startswith("new CBinary(B_MUL")
    assert lines[0] == "new CInWindow(630, 990)"
    for i, ln in enumerate(lines):
        for ref in re.findall(r"g_nodes\[(\d+)\]", ln):
            assert int(ref) < i


def test_pacote_de_implantacao(tmp_path: Path) -> None:
    pkg = package(AST, H001, RULES, COSTS, tmp_path, "cfg" * 8, {"ok": True})
    assert pkg.mq5.read_text(encoding="utf-8") == transpile(AST, H001, RULES, COSTS)
    manifest = json.loads(pkg.manifest.read_text(encoding="utf-8"))
    assert manifest["parity"] == {"ok": True} and manifest["proof_certificate"] is None
    assert manifest["kill_condition"] == "lag_ms"


# ---------------------------------------------------------------- telemetria e paridade


@pytest.fixture(scope="module")
def frame_and_rows() -> FrameRows:
    f = synthetic_frame(n_days=6, roll_day_index=3)
    return f, simulate(AST, f)


def test_telemetria_ida_e_volta(tmp_path: Path, frame_and_rows: FrameRows) -> None:
    _, rows = frame_and_rows
    p = tmp_path / "t.csv"
    write_telemetry(p, rows[:100])
    assert read_telemetry(p) == rows[:100]


def test_telemetria_fora_do_schema(tmp_path: Path) -> None:
    p = tmp_path / "t.csv"
    p.write_text("ts,bar_time\n", encoding="utf-8")
    with pytest.raises(TelemetryError):
        read_telemetry(p)


def test_paridade_com_robo_simulado(tmp_path: Path,
                                    frame_and_rows: FrameRows) -> None:
    frame, rows = frame_and_rows
    res = compare(AST, rows, frame)
    assert res.ok and res.compared > 2000 and res.max_error < 1e-9
    days = write_days(rows, tmp_path)
    weekly = weekly_parity(days, tmp_path, AST, frame)
    assert weekly.ok and weekly.compared == res.compared
    log = tmp_path / "tester.csv"
    write_telemetry(log, rows)
    assert parity_check(AST, log, frame).ok


def test_paridade_detecta_robo_diferente(frame_and_rows: FrameRows) -> None:
    frame, rows = frame_and_rows
    i = next(k for k, r in enumerate(rows) if r.signal is not None)
    bad = list(rows)
    sig = bad[i + 500].signal
    assert sig is not None
    bad[i + 500] = dataclasses.replace(bad[i + 500], signal=sig + 1e-6)
    assert not compare(AST, bad, frame).ok
    missing = bad[:10] + bad[11:]
    assert compare(AST, missing, frame).missing_in_log == 1


def test_data_gap_fica_fora_da_comparacao(frame_and_rows: FrameRows) -> None:
    frame, rows = frame_and_rows
    gap = list(rows)
    gap[800] = dataclasses.replace(gap[800], signal=None, guard_blocked=GuardBlock.DATA_GAP)
    res = compare(AST, gap, frame)
    assert res.missing_in_log == 1 and res.nan_mismatch == 0
