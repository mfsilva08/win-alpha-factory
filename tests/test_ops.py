"""SPEC-fase-4 e ADR-008: coletor, alertas, relatórios e CLI."""

from __future__ import annotations

import dataclasses
import inspect
import json
import re
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest

import src.ops.coletor as coletor_module
from src import cli
from src.agents.schemas import KillSpec, load_hypothesis
from src.backtest.costs import load_costs
from src.backtest.runner import run
from src.catalog.families import STARTER, catalog_sha256, select
from src.codegen.telemetry import Decision, TelemetryRow, file_name, write_telemetry
from src.dsl.parser import parse
from src.dsl.verdict import Verdict
from src.ledger.ledger import Kind, Ledger, sha256_file, sha256_hex
from src.ops.alerts import Alert, emit
from src.ops.coletor import AlreadyCollected, KillState, collect, evaluate_kill, load_calendar
from src.orchestrator.budgets import Budget
from src.orchestrator.state import TrialState
from src.reports.monitor import monitor_report
from src.reports.session import SessionSummary, session_report
from tests.dsl_factory import BARS_PER_DAY, synthetic_frame
from tests.robot_sim import simulate
from tests.test_gate import filled_prereg
from tests.test_orchestrator import CFG as BT_CFG
from tests.test_orchestrator import OkEngine

ROOT = Path(__file__).resolve().parents[1]
COSTS = load_costs(ROOT / "config" / "costs.yaml")
H001 = load_hypothesis(ROOT / "docs" / "exemplos" / "h001.yaml")
AST = parse('mul(in_window("10:30","16:30"), sub(zscore(ret(ref("ES"),2),55),'
            " zscore(ret(close(),2),55)))")
# condição de morte curta, para testar em poucos dias
HK = dataclasses.replace(H001, kill_condition=KillSpec("spread_ticks", "mediana", 3, ">", 2.0,
                                                       "ticks"))


@pytest.fixture
def led(tmp_path: Path) -> Iterator[Ledger]:
    with Ledger(tmp_path / "l.sqlite") as lg:
        lg.genesis(sha256_hex(b"d"), sha256_hex(b"p"), catalog_sha256(select(STARTER)))
        yield lg


@pytest.fixture(scope="module")
def days_rows() -> dict[date, list[TelemetryRow]]:
    f = synthetic_frame(n_days=4, roll_day_index=None)
    by_day: dict[date, list[TelemetryRow]] = {}
    for r in simulate(AST, f, threshold=1e9):  # limiar inalcançável: dias sem operação
        by_day.setdefault(r.bar_time.date(), []).append(r)
    return by_day


def write_day(tmp: Path, rows: list[TelemetryRow], kill: float | None = 1.0,
              **change: object) -> Path:
    out = [dataclasses.replace(r, kill_metric_value=kill, **change) for r in rows]  # type: ignore[arg-type]
    p = tmp / file_name(rows[0].bar_time.date())
    write_telemetry(p, out)
    return p


# ---------------------------------------------------------------- critérios do M8


def test_uma_linha_por_barra_inclusive_sem_operacao(
    tmp_path: Path, led: Ledger, days_rows: dict[date, list[TelemetryRow]]
) -> None:
    day, rows = next(iter(days_rows.items()))
    assert len(rows) == BARS_PER_DAY
    assert all(r.decision is Decision.NONE for r in rows)
    write_day(tmp_path, rows)
    res = collect(day, tmp_path, led, HK, "s1", BARS_PER_DAY, COSTS)
    assert res.summary is not None and res.summary.bars_recorded == BARS_PER_DAY
    assert res.summary.trades == 0 and res.alerts == ()
    assert led.count(Kind.DAILY) == 1 and led.count() == 0


def test_coletor_detecta_contagem_de_barras_errada(
    tmp_path: Path, led: Ledger, days_rows: dict[date, list[TelemetryRow]]
) -> None:
    day, rows = next(iter(days_rows.items()))
    write_day(tmp_path, rows[:-7])
    res = collect(day, tmp_path, led, HK, "s1", BARS_PER_DAY, COSTS)
    assert [a.kind for a in res.alerts] == [Alert.OPERACIONAL]
    assert "533" in res.alerts[0].message


def test_evaluate_kill_insuficiente_ate_a_janela() -> None:
    spec = KillSpec("lag_ms", "mediana", 3, "<", 20000.0, "ms")
    assert evaluate_kill(spec, [])[0] is KillState.INSUFFICIENT
    assert evaluate_kill(spec, [1.0, 2.0])[0] is KillState.INSUFFICIENT
    assert evaluate_kill(spec, [30000.0, 30000.0, 30000.0]) == (KillState.ALIVE, 30000.0)
    assert evaluate_kill(spec, [30000.0, 1.0, 1.0, 1.0]) == (KillState.DEAD, 1.0)
    media = dataclasses.replace(spec, aggregation="media", operator=">", threshold=2.0)
    assert evaluate_kill(media, [1.0, 2.0, 4.0]) == (KillState.DEAD, 7.0 / 3)


def test_cruzamento_grava_kill_e_so_uma_vez(
    tmp_path: Path, led: Ledger, days_rows: dict[date, list[TelemetryRow]]
) -> None:
    states = []
    for (day, rows), kill in zip(days_rows.items(), (1.0, 3.0, 3.0, 3.0), strict=True):
        write_day(tmp_path, rows, kill=kill)
        states.append(collect(day, tmp_path, led, HK, "s1", BARS_PER_DAY, COSTS))
    assert [s.kill_state for s in states] == [KillState.INSUFFICIENT, KillState.INSUFFICIENT,
                                              KillState.DEAD, KillState.DEAD]
    kills = led.records(Kind.KILL)
    assert len(kills) == 1 and kills[0].payload is not None
    assert kills[0].payload["value"] == 3.0 and kills[0].payload["day"] == states[2].summary.day  # type: ignore[union-attr]
    assert [a.kind for a in states[2].alerts] == [Alert.DECAIMENTO]
    assert led.count() == 0 and led.verify_chain()


def test_coletor_nao_desliga_o_robo(
    tmp_path: Path, led: Ledger, days_rows: dict[date, list[TelemetryRow]]
) -> None:
    for (day, rows), kill in zip(days_rows.items(), (5.0, 5.0, 5.0, 5.0), strict=True):
        write_day(tmp_path, rows, kill=kill)
    before = {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in tmp_path.iterdir()}
    for day in days_rows:
        collect(day, tmp_path, led, HK, "s1", BARS_PER_DAY, COSTS)
    after = {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in tmp_path.iterdir()
             if p.suffix == ".csv"}
    assert after == {k: v for k, v in before.items() if k.endswith(".csv")}
    src = inspect.getsource(coletor_module)
    for forbidden in ("PositionClose", "ExpertRemove", "subprocess", "os.remove", "unlink",
                      "shutdown", "terminate", "write_text", "write_bytes", '"w"'):
        assert forbidden not in src, forbidden


def test_tres_alertas_distintos_por_gatilhos_distintos(
    tmp_path: Path, led: Ledger, days_rows: dict[date, list[TelemetryRow]]
) -> None:
    items = list(days_rows.items())
    assert len({a.value for a in Alert}) == 3
    # operacional: rejeições, sem mexer em custo nem em kill
    d0, r0 = items[0]
    rej = [dataclasses.replace(r, decision=Decision.REJECTED, order_status="REJECTED_10006")
           if i == 100 else r for i, r in enumerate(r0)]
    write_day(tmp_path, rej)
    assert {a.kind for a in collect(d0, tmp_path, led, HK, "s", BARS_PER_DAY, COSTS).alerts} == {
        Alert.OPERACIONAL}
    # divergência: slippage de 3 ticks contra 1 modelado
    d1, r1 = items[1]
    slip = [dataclasses.replace(r, decision=Decision.OPEN_LONG, contracts=1, slippage_ticks=3.0,
                                order_status="FILLED") if i == 200 else r
            for i, r in enumerate(r1)]
    write_day(tmp_path, slip)
    assert {a.kind for a in collect(d1, tmp_path, led, HK, "s", BARS_PER_DAY, COSTS).alerts} == {
        Alert.DIVERGENCIA}
    # decaimento: só o cruzamento da kill_condition
    d2, r2 = items[2]
    write_day(tmp_path, r2, kill=9.0)
    hk2 = dataclasses.replace(HK, kill_condition=dataclasses.replace(HK.kill_condition,
                                                                     window_days=1))
    assert {a.kind for a in collect(d2, tmp_path, led, hk2, "s", BARS_PER_DAY, COSTS).alerts} == {
        Alert.DECAIMENTO}


def test_prejuizo_nao_e_gatilho(
    tmp_path: Path, led: Ledger, days_rows: dict[date, list[TelemetryRow]]
) -> None:
    day, rows = next(iter(days_rows.items()))
    write_day(tmp_path, rows, daily_pnl_points=-1400.0)
    res = collect(day, tmp_path, led, HK, "s", BARS_PER_DAY, COSTS)
    assert res.alerts == () and res.summary is not None
    assert res.summary.net_pnl_points == -1400.0


def test_arquivo_ausente_ou_invalido_e_coleta_dupla(
    tmp_path: Path, led: Ledger, days_rows: dict[date, list[TelemetryRow]]
) -> None:
    day, rows = next(iter(days_rows.items()))
    res = collect(day, tmp_path, led, HK, "s", BARS_PER_DAY, COSTS)
    assert res.summary is None and [a.kind for a in res.alerts] == [Alert.OPERACIONAL]
    (tmp_path / file_name(day)).write_text("lixo\n", encoding="utf-8")
    assert collect(day, tmp_path, led, HK, "s", BARS_PER_DAY, COSTS).summary is None
    write_day(tmp_path, rows)
    collect(day, tmp_path, led, HK, "s", BARS_PER_DAY, COSTS)
    with pytest.raises(AlreadyCollected):
        collect(day, tmp_path, led, HK, "s", BARS_PER_DAY, COSTS)


def test_alertas_em_jsonl(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from src.ops.alerts import event
    log = tmp_path / "alerts.jsonl"
    emit([event(Alert.DECAIMENTO, date(2026, 9, 15), "h001", "lag_ms cruzou")], log)
    line = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert line["kind"] == "decaimento" and "Desligar" in line["action"]
    assert "[DECAIMENTO]" in capsys.readouterr().out


def test_calendario(tmp_path: Path) -> None:
    p = tmp_path / "cal.csv"
    p.write_text("day,bars_expected\n2026-09-15,540\n2026-09-16,300\n", encoding="utf-8")
    assert load_calendar(p) == {date(2026, 9, 15): 540, date(2026, 9, 16): 300}


# ---------------------------------------------------------------- relatórios


def assert_single_file(html: str) -> None:
    assert html.startswith("<!doctype html>")
    assert "<svg" in html
    assert not re.search(r"<script|<link\b|src=\"http|href=\"http|@import", html)


def test_relatorio_de_sessao(tmp_path: Path, led: Ledger) -> None:
    from src.agents.formula import record_verdict
    head, count = led.head(), led.count()
    folds = {}
    for i, v in enumerate((Verdict.COST_DOMINATED, Verdict.FAILED_GATE)):
        tid = f"s9/h001/{i + 1}"
        res = run(AST, "h001", tid, BT_CFG, led, OkEngine())
        folds[tid] = res.metrics
        st = TrialState("s9/h001", H001, Budget(), H001.dsl_constraints, attempt=i + 1)
        record_verdict(led, st, v, "INTERMERCADO_SP500")
    st = TrialState("s9/h001", H001, Budget(), H001.dsl_constraints, attempt=3)
    record_verdict(led, st, Verdict.INVALID_AST, "INTERMERCADO_SP500")
    summary = SessionSummary("s9", count, head, ("h001",), folds,
                             {"INTERMERCADO_SP500": None}, {"INTERMERCADO_SP500": 1.2},
                             var_sr=0.6)
    html = session_report(summary, led, tmp_path / "sessao.html").read_text(encoding="utf-8")
    assert_single_file(html)
    assert led.head() in html and head in html
    assert html.count("FAILED_GATE") >= 1 and "INVALID_AST" in html
    assert "fórmulas emitidas" in html and "sessão" in html.lower()
    assert re.search(r'<div class="v">2</div><div class="k">avaliações', html)
    assert re.search(r'<div class="v">3</div><div class="k">fórmulas', html)


def test_relatorio_de_monitoramento(
    tmp_path: Path, led: Ledger, days_rows: dict[date, list[TelemetryRow]]
) -> None:
    for (day, rows), kill in zip(days_rows.items(), (1.0, 3.0, 3.0, 3.0), strict=True):
        write_day(tmp_path, rows, kill=kill)
        collect(day, tmp_path, led, HK, "s1", BARS_PER_DAY, COSTS)
    out = monitor_report(led, [HK], tmp_path / "monitor.html", 1.0,
                         cpcv_band={HK.id: (-100.0, 400.0, 60)})
    html = out.read_text(encoding="utf-8")
    assert_single_file(html)
    assert "DEAD" in html and "spread_ticks" in html and 'class="band"' in html


# ---------------------------------------------------------------- CLI


def test_cli_genesis_recusa_prereg_incompleto(tmp_path: Path) -> None:
    code = cli.main(["genesis", "--ledger", str(tmp_path / "l.sqlite"),
                     "--data-hash", sha256_hex(b"d")])
    assert code == 2
    assert not (tmp_path / "l.sqlite").exists()  # recusa antes de abrir o banco


def test_cli_fluxo(tmp_path: Path, days_rows: dict[date, list[TelemetryRow]],
                   capsys: pytest.CaptureFixture[str]) -> None:
    prereg = tmp_path / "gate-prereg.md"
    prereg.write_bytes(filled_prereg().encode("utf-8"))
    db = str(tmp_path / "l.sqlite")
    assert cli.main(["genesis", "--ledger", db, "--data-hash", sha256_hex(b"d"),
                     "--prereg", str(prereg)]) == 0
    with Ledger(db) as led:
        assert led.genesis_info().prereg_sha256 == sha256_file(prereg)
    assert cli.main(["verify", "--ledger", db]) == 0
    hyp = str(ROOT / "docs" / "exemplos" / "h001.yaml")
    assert cli.main(["session", "--ledger", db, "--hypothesis", hyp]) == 2
    day, rows = next(iter(days_rows.items()))
    tele = tmp_path / "tele"
    tele.mkdir()
    write_day(tele, rows)
    mon = tmp_path / "monitor.html"
    assert cli.main(["collect", "--ledger", db, "--day", day.isoformat(), "--hypothesis", hyp,
                     "--telemetry-dir", str(tele), "--expected-bars", str(BARS_PER_DAY),
                     "--monitor-out", str(mon)]) == 0
    assert mon.exists()
    assert cli.main(["report", "--ledger", db, "--session", "s1",
                     "--out", str(tmp_path / "r.html")]) == 0
    assert cli.main(["monitor", "--ledger", db, "--hypothesis", hyp,
                     "--out", str(tmp_path / "m.html")]) == 0
    out = capsys.readouterr().out
    assert "decisões pendentes" in out and "íntegra" in out
