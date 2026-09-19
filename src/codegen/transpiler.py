"""AST tipada → ``.mq5`` (SPEC-fase-3 §3.12). Compilação, não geração (R2).

Determinístico: a mesma AST, hipótese, regras e custos produzem o mesmo texto,
byte a byte. O estado incremental emitido espelha ``dsl/eval_incremental.py``
operador por operador; os guards espelham ``codegen/guards.py``.

Recusas, todas na bancada e não seis meses depois:

- ``NonInstrumentableKillCondition``: métrica da ``kill_condition`` que o robô não
  sabe medir (SPEC-fase-4 §4.2)
- ``NonTranspilable``: operador sem equivalente fiel em MQL5. Hoje só ``vwap``
  (as barras M1 do MT5 não trazem VWAP). ``volume`` vira ``real_volume`` e
  ``trades`` vira ``tick_volume`` — o exportador de dados precisa usar os mesmos
  campos, ou a paridade falha
- AST malformada ou com raiz fora de Z/Bool
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import time
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from src.agents.schemas import Direction, Hypothesis
from src.backtest.costs import CostModel
from src.backtest.engine import TradeRules
from src.catalog.metrics import INSTRUMENTABLE_METRICS
from src.dsl.ast import ROOT_UNITS, Node, check_arg_kinds, infer_unit, max_window_of, parse_hhmm
from src.dsl.canonical import exact_signature, serialize, structural_signature
from src.dsl.ops import Op

TEMPLATES = Path(__file__).with_name("templates")


class TranspileError(Exception):
    """Base das recusas do transpilador."""


class NonInstrumentableKillCondition(TranspileError):
    """A ``kill_condition`` usa métrica que o robô não grava."""


class NonTranspilable(TranspileError):
    """Operador sem tradução fiel para o MQL5."""


@dataclass(frozen=True)
class RobotParams:
    """Parâmetros de operação: não mudam a estratégia, ficam como ``input`` no EA."""

    ref_es: str = "ES"
    ref_wdo: str = "WDO$"
    daily_loss_points: float = 1500.0
    max_rejections: int = 3
    magic: int = 20260919


_FIELDS = {Op.CLOSE: "F_CLOSE", Op.HIGH: "F_HIGH", Op.LOW: "F_LOW",
           Op.VOLUME: "F_VOLUME", Op.TRADES: "F_TRADES"}
_LAG = {Op.LAG: "LM_LAG", Op.DELTA: "LM_DELTA", Op.RET: "LM_RET"}
_BIN = {Op.ADD: "B_ADD", Op.SUB: "B_SUB", Op.MUL: "B_MUL", Op.DIV: "B_DIV",
        Op.GT: "B_GT", Op.LT: "B_LT", Op.AND_: "B_AND", Op.OR_: "B_OR"}


def _int(n: Node, i: int) -> int:
    a = n.args[i]
    assert isinstance(a, int)
    return a


def _str(n: Node, i: int) -> str:
    a = n.args[i]
    assert isinstance(a, str)
    return a


def flatten(ast: Node) -> list[str]:
    """Construtores em pós-ordem: cada filho existe antes do pai. A raiz é a última."""
    lines: list[str] = []

    def ref(i: int) -> str:
        return f"g_nodes[{i}]"

    def visit(n: Node) -> int:
        op = n.op
        if op in _FIELDS:
            line = f"new CField({_FIELDS[op]})"
        elif op is Op.VWAP:
            raise NonTranspilable("vwap: as barras M1 do MT5 não trazem VWAP")
        elif op is Op.REF:
            line = f"new CRef({'R_ES' if _str(n, 0) == 'ES' else 'R_WDO'})"
        elif op is Op.CONST:
            line = f"new CConst({float(_int(n, 0))!r})"
        elif op is Op.IN_WINDOW:
            line = f"new CInWindow({parse_hhmm(_str(n, 0))}, {parse_hhmm(_str(n, 1))})"
        elif op is Op.MINUTES_SINCE_OPEN:
            line = "new CMinutesSinceOpen()"
        else:
            kids = [visit(a) for a in n.args if isinstance(a, Node)]
            c = ref(kids[0])
            if op in _LAG:
                line = f"new CLag({c}, {_int(n, 1)}, {_LAG[op]})"
            elif op is Op.ROLLING_MEAN:
                line = f"new CRollingMean({c}, {_int(n, 1)})"
            elif op is Op.ROLLING_STD:
                line = f"new CRollingStd({c}, {_int(n, 1)})"
            elif op is Op.ZSCORE:
                line = f"new CZScore({c}, {_int(n, 1)})"
            elif op in (Op.ROLLING_MAX, Op.ROLLING_MIN):
                line = f"new CExtreme({c}, {_int(n, 1)}, {'true' if op is Op.ROLLING_MAX else 'false'})"
            elif op is Op.EMA:
                line = f"new CEma({c}, {_int(n, 1)})"
            elif op is Op.RANK_TS:
                line = f"new CRankTs({c}, {_int(n, 1)})"
            elif op is Op.CLIP:
                line = f"new CClip({c}, {float(_int(n, 1))!r}, {float(_int(n, 2))!r})"
            elif op in _BIN:
                line = f"new CBinary({_BIN[op]}, {c}, {ref(kids[1])})"
            else:
                raise NonTranspilable(f"operador sem tradução: {op.value}")
        lines.append(line)
        return len(lines) - 1

    visit(ast)
    return lines


def _minutes(t: time) -> int:
    return t.hour * 60 + t.minute


def _refs(ast: Node) -> set[str]:
    out: set[str] = set()

    def walk(n: Node) -> None:
        if n.op is Op.REF:
            out.add(_str(n, 0))
        for a in n.args:
            if isinstance(a, Node):
                walk(a)

    walk(ast)
    return out


def transpile(ast: Node, h: Hypothesis, rules: TradeRules, costs: CostModel,
              params: RobotParams | None = None) -> str:
    """O texto do ``.mq5``. Levanta ``TranspileError`` antes de emitir qualquer coisa."""
    if h.kill_condition.metric not in INSTRUMENTABLE_METRICS:
        raise NonInstrumentableKillCondition(h.kill_condition.metric)
    check_arg_kinds(ast)
    if infer_unit(ast) not in ROOT_UNITS:
        raise TranspileError("a raiz deve ter unidade Z ou Bool")
    p = params or RobotParams()
    refs = _refs(ast)
    env = Environment(loader=FileSystemLoader(TEMPLATES), undefined=StrictUndefined,
                      keep_trailing_newline=True, autoescape=False)
    return env.get_template("robo.mq5.j2").render(
        name=f"robo_{h.id}",
        hypothesis_id=h.id,
        family=h.family,
        ast_hash=exact_signature(ast),
        structural_sig=structural_signature(ast),
        formula=serialize(ast),
        ref_es=p.ref_es,
        ref_wdo=p.ref_wdo,
        daily_loss_points=repr(float(p.daily_loss_points)),
        max_rejections=p.max_rejections,
        magic=p.magic,
        threshold=repr(float(rules.threshold)),
        contracts=rules.contracts,
        direction=1 if h.direction is Direction.LONG_ON_HIGH else -1,
        max_holding_min=int(rules.max_holding.total_seconds() // 60),
        stop_points=rules.stop_points or 0,
        target_points=rules.target_points or 0,
        cutoff_min=_minutes(rules.cutoff),
        session_start_min=_minutes(rules.session[0]),
        session_end_min=_minutes(rules.session[1]),
        tick_size=costs.tick_size,
        point_value=repr(float(costs.point_value)),
        margin_per_contract=repr(float(costs.margin_per_contract)),
        warmup_bars=max(max_window_of(ast) * 3, 1),
        kill_metric=h.kill_condition.metric,
        needs_es="ES" in refs,
        needs_wdo="WDO" in refs,
        nodes=flatten(ast),
    )


@dataclass(frozen=True)
class DeploymentPackage:
    mq5: Path
    manifest: Path


def package(ast: Node, h: Hypothesis, rules: TradeRules, costs: CostModel, out_dir: Path,
            gate_config_hash: str, parity: dict[str, object] | None,
            params: RobotParams | None = None) -> DeploymentPackage:
    """``.mq5`` + manifesto (hash da AST, config do gate, certificado de prova, paridade)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    source = transpile(ast, h, rules, costs, params)
    mq5 = out_dir / f"robo_{h.id}.mq5"
    mq5.write_text(source, encoding="utf-8", newline="\n")
    manifest = out_dir / f"robo_{h.id}.manifest.json"
    body = {
        "hypothesis_id": h.id,
        "ast_hash": exact_signature(ast),
        "structural_sig": structural_signature(ast),
        "formula": serialize(ast),
        "gate_config_hash": gate_config_hash,
        "proof_certificate": None,  # verificação formal fora do escopo (ADR-006)
        "parity": parity,
        "kill_condition": h.kill_condition.metric,
    }
    manifest.write_text(json.dumps(body, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                        encoding="utf-8", newline="\n")
    return DeploymentPackage(mq5, manifest)
