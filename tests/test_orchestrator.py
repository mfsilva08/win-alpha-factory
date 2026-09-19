"""SPEC-fase-3 §3.3–3.5: roteamento puro, orçamentos, grafo e replay."""

from __future__ import annotations

import dataclasses
import random
from collections.abc import Iterator
from datetime import time, timedelta
from pathlib import Path
from typing import Any

import pytest

from src.agents.schemas import load_hypothesis
from src.backtest.costs import load_costs
from src.backtest.engine import BacktestRequest, CvConfig, TradeRules
from src.backtest.metrics import BacktestOutcome
from src.backtest.runner import BacktestConfig, run
from src.dsl.parser import parse, validate
from src.dsl.verdict import Verdict
from src.gate.config import GateConfig
from src.ledger.ledger import Ledger, sha256_hex
from src.orchestrator import budgets
from src.orchestrator.budgets import Budget, BudgetExhausted, BudgetMismatch
from src.orchestrator.events import (
    AgentClient,
    ReplayClient,
    ReplayDiverged,
    read_jsonl,
    write_jsonl,
)
from src.orchestrator.graph import Nodes, build_graph, call_agent, run_trial
from src.orchestrator.router import RoutingError, route_formula, route_gate, route_proof
from src.orchestrator.state import Metrics, ProofStatus, TrialState
from tests.test_backtest import fold

ROOT = Path(__file__).resolve().parents[1]
H001 = load_hypothesis(ROOT / "docs" / "exemplos" / "h001.yaml")
VALID = ('mul(in_window("10:30","16:30"), sub(zscore(ret(ref("ES"),2),55),'
         " zscore(ret(close(),2),55)))")
VALID_2 = ('mul(in_window("10:30","16:30"), sub(zscore(ret(ref("ES"),3),34),'
           " zscore(ret(close(),3),34)))")
INVALID = 'sub(close(), ref("ES"))'  # raiz em Price


def st(**over: Any) -> TrialState:
    s = TrialState(trial_id="t0001", hypothesis=H001, budget=Budget(max_attempts=5),
                   constraints=H001.dsl_constraints)
    for k, v in over.items():
        setattr(s, k, v)
    return s


# ---------------------------------------------------------------- roteamento


@pytest.mark.parametrize("attempt", [0, 1, 4, 5, 99])
def test_failed_gate_sempre_abandona(attempt: int) -> None:
    assert route_gate(st(verdict=Verdict.FAILED_GATE, attempt=attempt)) == "abandon"


def test_rotas_do_gate() -> None:
    assert route_gate(st(verdict=Verdict.ACCEPTED)) == "codegen"
    for v in (Verdict.INSUFFICIENT_SAMPLE, Verdict.COST_DOMINATED,
              Verdict.UNSTABLE_ACROSS_FOLDS):
        assert route_gate(st(verdict=v, attempt=2)) == "retry"
        assert route_gate(st(verdict=v, attempt=5)) == "abandon"
    with pytest.raises(RoutingError):
        route_gate(st(verdict=Verdict.INVALID_AST))
    with pytest.raises(RoutingError):
        route_gate(st(verdict=None))


def test_rotas_da_formula() -> None:
    ast = parse(VALID)
    assert route_formula(st(formula_ast=ast, verdict=None)) == "proof"
    assert route_formula(st(formula_ast=ast, verdict=None, attempt=5)) == "proof"
    for v in (Verdict.INVALID_AST, Verdict.TOO_COMPLEX, Verdict.REDUNDANT):
        assert route_formula(st(verdict=v, attempt=4)) == "retry"
        assert route_formula(st(verdict=v, attempt=5)) == "abandon"
    with pytest.raises(RoutingError):
        route_formula(st(verdict=None))
    with pytest.raises(RoutingError):
        route_formula(st(verdict=Verdict.FAILED_GATE))


def test_rotas_da_prova() -> None:
    assert route_proof(st(proof_status=ProofStatus.PASSED)) == "backtest"
    assert route_proof(st(proof_status=ProofStatus.SKIPPED)) == "backtest"
    assert route_proof(st(proof_status=ProofStatus.FAILED, attempt=1)) == "retry"
    assert route_proof(st(proof_status=ProofStatus.FAILED, attempt=5)) == "abandon"
    with pytest.raises(RoutingError):
        route_proof(st(proof_status=ProofStatus.NOT_STARTED))


def test_roteamento_deterministico() -> None:
    s = st(verdict=Verdict.COST_DOMINATED, attempt=2)
    assert {route_gate(s) for _ in range(100)} == {"retry"}


# ---------------------------------------------------------------- orçamentos

DATA = sha256_hex(b"d")


@pytest.fixture
def led(tmp_path: Path) -> Iterator[Ledger]:
    with Ledger(tmp_path / "l.sqlite") as lg:
        lg.genesis(DATA, sha256_hex(b"p"), sha256_hex(b"c"))
        yield lg


class OkEngine:
    def evaluate(self, req: BacktestRequest) -> BacktestOutcome:
        return BacktestOutcome(req.test_id, req.data_hash, tuple(fold(i) for i in range(28)))


CFG = BacktestConfig(
    rules=TradeRules(1.5, 2, timedelta(minutes=30), None, None, time(17, 50),
                     (time(10, 30), time(16, 30))),
    costs=load_costs(ROOT / "config" / "costs.yaml"),
    cv=CvConfig(8, 2, timedelta(minutes=3), 3.0),
)


def test_budget_exhausted_no_teto_global(led: Ledger) -> None:
    b = Budget(max_trials_global=3)
    for i in range(3):
        budgets.check(led, b)
        run(parse(VALID), "h001", f"t{i}", CFG, led, OkEngine())
    with pytest.raises(BudgetExhausted):
        budgets.check(led, b)


def test_orcamento_bate_com_prereg() -> None:
    cfg = GateConfig(400, 30, 120, 0.25, 0.30, 0.20, 0.95, 3000, 8, 2, 3.0)
    budgets.check_consistent(Budget(max_trials_global=3000), cfg)
    with pytest.raises(BudgetMismatch):
        budgets.check_consistent(Budget(max_trials_global=5000), cfg)


def test_teto_de_formulas_por_hipotese() -> None:
    budgets.check_formulas(24, Budget())
    with pytest.raises(BudgetExhausted):
        budgets.check_formulas(25, Budget())


# ---------------------------------------------------------------- o grafo


class ScriptedLLM:
    """Responde uma lista fixa de fórmulas, em ordem."""

    def __init__(self, answers: list[str]) -> None:
        self.answers = list(answers)
        self.payloads: list[str] = []

    def complete(self, node: str, payload: str, seed: int) -> str:
        self.payloads.append(payload)
        return self.answers.pop(0)


class NoisyLLM:
    """Não determinístico de propósito: só o replay pode reproduzi-lo."""

    def complete(self, node: str, payload: str, seed: int) -> str:
        return random.SystemRandom().choice([VALID, VALID_2, INVALID])


def formula_node(client: AgentClient) -> Any:
    def node(s: TrialState) -> dict[str, Any]:
        cur = dataclasses.replace(s, attempt=s.attempt + 1)
        response, events = call_agent(cur, client, "formula")
        ast = parse(response)
        r = validate(ast, s.constraints)
        return {"attempt": cur.attempt, "formula_ast": ast if r.ok else None,
                "verdict": r.verdict, "detail": r.detail, "events": events,
                "proof_status": ProofStatus.NOT_STARTED, "metrics": None}
    return node


def backtest_node(ledger: Ledger, b: Budget) -> Any:
    def node(s: TrialState) -> dict[str, Any]:
        budgets.check(ledger, b)
        assert s.formula_ast is not None
        res = run(s.formula_ast, s.hypothesis.id, f"{s.trial_id}.{s.attempt}", CFG, ledger,
                  OkEngine())
        return {"metrics": Metrics(res.metrics)}
    return node


def gate_node(script: list[Verdict]) -> Any:
    def node(s: TrialState) -> dict[str, Any]:
        return {"verdict": script.pop(0)}
    return node


def nodes(client: AgentClient, ledger: Ledger, gate_script: list[Verdict],
          b: Budget | None = None) -> Nodes:
    return Nodes(
        hypothesis=lambda s: {},
        formula=formula_node(client),
        proof=lambda s: {"proof_status": ProofStatus.SKIPPED},
        backtest=backtest_node(ledger, b or Budget()),
        gate=gate_node(gate_script),
        codegen=lambda s: {"detail": None},
    )


def test_caminho_completo_ate_failed_gate(led: Ledger) -> None:
    llm = ScriptedLLM([INVALID, VALID, VALID_2])
    app = build_graph(nodes(llm, led, [Verdict.COST_DOMINATED, Verdict.FAILED_GATE]))
    out = run_trial(app, st())
    assert out.verdict is Verdict.FAILED_GATE
    assert out.attempt == 3
    # INVALID_AST não consumiu tentativa global; os dois backtests sim
    assert led.count() == 2
    # o retry depois do gate levou o veredito, mas nenhum número
    assert '"verdict":"COST_DOMINATED"' in llm.payloads[2]


def test_accepted_vai_para_codegen(led: Ledger) -> None:
    app = build_graph(nodes(ScriptedLLM([VALID]), led, [Verdict.ACCEPTED]))
    out = run_trial(app, st())
    assert out.verdict is Verdict.ACCEPTED and led.count() == 1


def test_teto_de_tentativas_encerra(led: Ledger) -> None:
    app = build_graph(nodes(ScriptedLLM([INVALID] * 10), led, []))
    out = run_trial(app, st())
    assert out.attempt == 5 and out.verdict is Verdict.INVALID_AST
    assert led.count() == 0


def test_budget_exhausted_interrompe_o_grafo(led: Ledger) -> None:
    b = Budget(max_trials_global=1)
    app = build_graph(nodes(ScriptedLLM([VALID, VALID_2]), led,
                            [Verdict.COST_DOMINATED], b))
    with pytest.raises(BudgetExhausted):
        run_trial(app, st())
    assert led.count() == 1


# ---------------------------------------------------------------- replay


def test_replay_reproduz_a_mesma_ast(led: Ledger, tmp_path: Path) -> None:
    gates = [Verdict.COST_DOMINATED] * 4 + [Verdict.FAILED_GATE]
    original = run_trial(build_graph(nodes(NoisyLLM(), led, list(gates))), st())
    log = tmp_path / "events.jsonl"
    write_jsonl(original.events, log)

    with Ledger(tmp_path / "replay.sqlite") as led2:
        led2.genesis(DATA, sha256_hex(b"p"), sha256_hex(b"c"))
        replayed = run_trial(
            build_graph(nodes(ReplayClient(read_jsonl(log)), led2, list(gates))), st()
        )
    assert replayed.formula_ast == original.formula_ast
    assert replayed.events == original.events
    assert replayed.attempt == original.attempt


def test_replay_divergente_e_detectado(led: Ledger) -> None:
    original = run_trial(build_graph(nodes(ScriptedLLM([VALID]), led, [Verdict.ACCEPTED])),
                         st())
    other = dataclasses.replace(H001, claim="outra tese")
    with pytest.raises(ReplayDiverged):
        call_agent(st(hypothesis=other, attempt=1), ReplayClient(original.events), "formula")
