"""A única interface de comando (ADR-008). O atrito do CLI é proteção, não defeito.

    uv run python -m src.cli genesis  --ledger C:/dados/ledger.sqlite --data-hash <sha256>
    uv run python -m src.cli verify   --ledger C:/dados/ledger.sqlite
    uv run python -m src.cli session  --ledger ... --hypothesis docs/exemplos/h001.yaml
    uv run python -m src.cli report   --ledger ... --session <id>
    uv run python -m src.cli collect  --ledger ... --day 2026-09-15 --hypothesis ... \\
                                      --telemetry-dir ... --expected-bars 540
    uv run python -m src.cli monitor  --ledger ... --hypothesis ...
    uv run python -m src.cli data     --win WIN_2024.csv --win WIN_2025.csv --es ES.csv \
                                      --out data/frame.npz --holdout-out C:/dados/holdout

Códigos de saída: 0 ok · 1 erro de uso ou dado · 2 bloqueado por decisão pendente.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import date, time
from pathlib import Path

from src.agents.schemas import load_hypothesis
from src.backtest.costs import load_costs
from src.backtest.errors import EngineNotConfigured
from src.catalog.families import STARTER, catalog_sha256, select
from src.gate import config as gate_config
from src.gate.errors import PreregIncomplete, PreregInvalid, PreregMismatch
from src.ledger.ledger import Ledger, sha256_file

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_COSTS = ROOT / "config" / "costs.yaml"


def _families(arg: str | None) -> tuple[str, ...]:
    return tuple(a.strip() for a in arg.split(",")) if arg else STARTER


def cmd_genesis(a: argparse.Namespace) -> int:
    text = Path(a.prereg).read_text(encoding="utf-8")
    gate_config.parse(text)  # recusa pré-registro incompleto antes de hashear
    fams = select(_families(a.families))
    with Ledger(a.ledger) as led:
        led.genesis(a.data_hash, sha256_file(Path(a.prereg)), catalog_sha256(fams))
        g = led.genesis_info()
    print(f"gênesis gravado\n  config_hash: {g.config_hash}\n  prereg:      {g.prereg_sha256}"
          f"\n  catálogo:    {g.catalog_sha256} ({', '.join(f.name for f in fams)})")
    return 0


def cmd_verify(a: argparse.Namespace) -> int:
    with Ledger(a.ledger) as led:
        ok = led.verify_chain()
        print(f"cadeia {'íntegra' if ok else 'ADULTERADA'} · tentativas {led.count()} · "
              f"head {led.head()}")
    return 0 if ok else 1


def cmd_session(a: argparse.Namespace) -> int:
    from src.backtest.api_engine import ApiBacktestEngine, ApiEngineConfig
    from src.orchestrator.session import GatePending

    h = load_hypothesis(Path(a.hypothesis))
    pending: list[str] = []
    try:
        ApiBacktestEngine(ApiEngineConfig(base_url=a.backtest_url or ""))
    except EngineNotConfigured as e:
        pending.append(f"motor de backtest: {e}")
    pending.append(f"gate: {GatePending.__doc__}")
    print("sessão bloqueada — decisões pendentes:")
    for p in pending:
        print(f"  - {p}")
    print(f"hipótese {h.id} ({h.family}) lida e válida; nada foi gravado no livro-razão.")
    return 2


def _parse_session(text: str) -> tuple[time, time]:
    try:
        a, b = text.split("-")
        return time.fromisoformat(a.strip()), time.fromisoformat(b.strip())
    except ValueError:
        raise SystemExit("--session usa o formato HH:MM-HH:MM") from None


def cmd_data(a: argparse.Namespace) -> int:
    """Lê a exportação do MT5, emenda contratos, confere e separa o hold-out."""
    from src.backtest import data as md

    win_parts = [md.read_mt5_csv(Path(p)) for p in a.win]
    win, rolls = md.stitch_by_difference(win_parts)
    refs = {}
    for sym, paths in (("ES", a.es or []), ("WDO", a.wdo or [])):
        if paths:
            part = [md.read_mt5_csv(Path(p)) for p in paths]
            refs[sym], _ = md.stitch_by_difference(part)
    frame, dropped = md.build_frame(win, refs, session=_parse_session(a.session),
                                    roll_days=rolls)
    report = md.check(frame, dropped)
    print(report.text())
    if rolls:
        print(f"dias de rolagem marcados: {', '.join(str(d) for d in sorted(rolls))}")
    if not report.ok and not a.force:
        print("\nchecagens falharam. Corrija a exportação ou use --force.", file=sys.stderr)
        return 1
    if a.calendar_out:
        print(f"calendário: {md.write_calendar(report, Path(a.calendar_out))}")
    if a.holdout_out:
        research, holdout = md.split_holdout(frame, a.holdout_months)
        if a.out and Path(a.holdout_out).resolve() == Path(a.out).resolve().parent:
            print("o hold-out precisa ficar em outro diretório", file=sys.stderr)
            return 1
        hpath = Path(a.holdout_out) / "holdout.npz"
        md.save_frame(holdout, hpath)
        print(f"hold-out ({len(holdout)} barras): {hpath}")
        print(f"  data_hash do hold-out: {md.data_hash(holdout)}")
        frame = research
    if a.out:
        md.save_frame(frame, Path(a.out))
        print(f"pesquisa ({len(frame)} barras): {a.out}")
    print(f"data_hash da pesquisa: {md.data_hash(frame)}")
    return 0


def cmd_report(a: argparse.Namespace) -> int:
    from src.reports.session import session_report, summary_from_ledger

    with Ledger(a.ledger) as led:
        out = session_report(summary_from_ledger(led, a.session), led,
                             Path(a.out or f"relatorios/sessao_{a.session}.html"))
    print(f"relatório: {out}")
    return 0


def cmd_collect(a: argparse.Namespace) -> int:
    from src.ops.alerts import emit
    from src.ops.coletor import collect, load_calendar
    from src.reports.monitor import monitor_report

    day = date.fromisoformat(a.day)
    h = load_hypothesis(Path(a.hypothesis))
    costs = load_costs(Path(a.costs))
    if a.expected_bars is not None:
        expected = int(a.expected_bars)
    elif a.calendar:
        cal = load_calendar(Path(a.calendar))
        if day not in cal:
            print(f"{day} não é pregão no calendário", file=sys.stderr)
            return 1
        expected = cal[day]
    else:
        print("informe --expected-bars ou --calendar", file=sys.stderr)
        return 1
    with Ledger(a.ledger) as led:
        res = collect(day, Path(a.telemetry_dir), led, h, a.strategy_id or h.id, expected, costs)
        emit(res.alerts, Path(a.alerts_log) if a.alerts_log else None)
        out = monitor_report(led, [h], Path(a.monitor_out), float(costs.slippage_ticks))
    print(f"{h.id} {day}: {res.kill_state.value} · monitor: {out}")
    return 0


def cmd_monitor(a: argparse.Namespace) -> int:
    from src.reports.monitor import monitor_report

    hyps = [load_hypothesis(Path(p)) for p in a.hypothesis]
    costs = load_costs(Path(a.costs))
    with Ledger(a.ledger) as led:
        out = monitor_report(led, hyps, Path(a.out), float(costs.slippage_ticks))
    print(f"monitor: {out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m src.cli", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("genesis", help="grava o registro zero do livro-razão")
    g.add_argument("--ledger", required=True)
    g.add_argument("--data-hash", required=True)
    g.add_argument("--prereg", default=str(gate_config.PREREG_PATH))
    g.add_argument("--families", help="famílias separadas por vírgula (padrão: STARTER)")
    g.set_defaults(fn=cmd_genesis)

    v = sub.add_parser("verify", help="confere a cadeia de hash")
    v.add_argument("--ledger", required=True)
    v.set_defaults(fn=cmd_verify)

    s = sub.add_parser("session", help="roda uma hipótese pelo grafo")
    s.add_argument("--ledger", required=True)
    s.add_argument("--hypothesis", required=True)
    s.add_argument("--backtest-url")
    s.set_defaults(fn=cmd_session)

    r = sub.add_parser("report", help="relatório HTML de uma sessão")
    r.add_argument("--ledger", required=True)
    r.add_argument("--session", required=True)
    r.add_argument("--out")
    r.set_defaults(fn=cmd_report)

    c = sub.add_parser("collect", help="job diário do coletor")
    c.add_argument("--ledger", required=True)
    c.add_argument("--day", required=True)
    c.add_argument("--hypothesis", required=True)
    c.add_argument("--telemetry-dir", required=True)
    c.add_argument("--expected-bars", type=int)
    c.add_argument("--calendar")
    c.add_argument("--strategy-id")
    c.add_argument("--costs", default=str(DEFAULT_COSTS))
    c.add_argument("--alerts-log")
    c.add_argument("--monitor-out", default="relatorios/monitor.html")
    c.set_defaults(fn=cmd_collect)

    m = sub.add_parser("monitor", help="relatório de monitoramento")
    m.add_argument("--ledger", required=True)
    m.add_argument("--hypothesis", required=True, action="append")
    m.add_argument("--costs", default=str(DEFAULT_COSTS))
    m.add_argument("--out", default="relatorios/monitor.html")
    m.set_defaults(fn=cmd_monitor)

    d = sub.add_parser("data", help="prepara e confere a exportação do MT5")
    d.add_argument("--win", required=True, action="append",
                   help="um arquivo por contrato, em ordem cronológica")
    d.add_argument("--es", action="append")
    d.add_argument("--wdo", action="append")
    d.add_argument("--session", default="09:00-18:00")
    d.add_argument("--out", help="frame de pesquisa (.npz)")
    d.add_argument("--holdout-out", help="diretório do hold-out — fora de data/")
    d.add_argument("--holdout-months", type=int, default=6)
    d.add_argument("--calendar-out")
    d.add_argument("--force", action="store_true", help="grava mesmo com checagem falhando")
    d.set_defaults(fn=cmd_data)
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        code: int = args.fn(args)
    except (PreregIncomplete, PreregMismatch, PreregInvalid) as e:
        print(f"pré-registro: {e}", file=sys.stderr)
        return 2
    return code


if __name__ == "__main__":
    raise SystemExit(main())
