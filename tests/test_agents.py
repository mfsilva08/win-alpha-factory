"""SPEC-fase-3 §3.6–3.11: catálogo, recompensa, bandit, hipótese, fórmula e cliente."""

from __future__ import annotations

import dataclasses
import inspect
import json
import re
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import src.catalog.bandit as bandit_module
from src.agents import client as agent_client
from src.agents import formula as formula_agent
from src.agents import hypothesis as hyp_agent
from src.agents.schemas import hypothesis_to_dict, load_hypothesis
from src.catalog import bandit, families
from src.catalog.families import FAMILIES, STARTER, CatalogError, catalog_sha256, get, select
from src.catalog.reward import REWARD, reward
from src.dsl.ast import Node
from src.dsl.canonical import serialize, structural_signature
from src.dsl.errors import DslParseError
from src.dsl.ops import Op
from src.dsl.parser import parse
from src.dsl.verdict import Verdict
from src.gate.collapse import GateInputs
from src.gate.config import GateConfig
from src.ledger.ledger import Kind, Ledger, LedgerRecord, sha256_hex
from src.orchestrator.budgets import Budget
from src.orchestrator.session import GatePending, SessionDeps, build_nodes, run_hypothesis
from src.orchestrator.state import Metrics, TrialState
from tests.test_backtest import fold
from tests.test_orchestrator import CFG as BT_CFG
from tests.test_orchestrator import OkEngine

ROOT = Path(__file__).resolve().parents[1]
H001 = load_hypothesis(ROOT / "docs" / "exemplos" / "h001.yaml")
SP500 = get(FAMILIES, "INTERMERCADO_SP500")
GATE = GateConfig(400, 30, 120, 0.25, 0.30, 0.20, 0.95, 3000, 8, 2, 3.0)


@pytest.fixture
def led(tmp_path: Path) -> Iterator[Ledger]:
    with Ledger(tmp_path / "l.sqlite") as lg:
        lg.genesis(sha256_hex(b"d"), sha256_hex(b"p"), catalog_sha256(select(STARTER)))
        yield lg


class ScriptedLLM:
    def __init__(self, answers: list[str]) -> None:
        self.answers = list(answers)
        self.payloads: list[str] = []

    def complete(self, node: str, payload: str, seed: int) -> str:
        self.payloads.append(payload)
        return self.answers.pop(0) if self.answers else self.fallback()

    def fallback(self) -> str:
        raise AssertionError("sem resposta roteirizada")


def formula_json(text: str) -> str:
    def to(n: Node | int | str) -> Any:
        return {"op": n.op.value, "args": [to(a) for a in n.args]} if isinstance(n, Node) else n
    return json.dumps({"formula": to(parse(text))})


VALID = ('mul(in_window("10:30","16:30"), sub(zscore(ret(ref("ES"),2),55),'
         " zscore(ret(close(),2),55)))")

# ---------------------------------------------------------------- catálogo


def test_catalogo_tem_as_oito_e_o_inicial() -> None:
    assert len(FAMILIES) == 8
    assert [f.name for f in select(STARTER)] == list(STARTER)
    assert not get(FAMILIES, "FLUXO_DE_AGRESSAO").available
    with pytest.raises(CatalogError):
        select(["NAO_EXISTE"])
    with pytest.raises(CatalogError):
        select([STARTER[0], STARTER[0]])


def test_hash_do_catalogo_estavel_e_sensivel() -> None:
    base = catalog_sha256(select(STARTER))
    assert base == catalog_sha256(select(STARTER))
    changed = [dataclasses.replace(f, horizon_range=(2, 11)) if f.name == SP500.name else f
               for f in select(STARTER)]
    assert catalog_sha256(changed) != base
    assert catalog_sha256(select(list(reversed(STARTER)))) != base  # ordem importa


def test_familias_nao_usam_rank_e_respeitam_dados() -> None:
    for f in FAMILIES:
        assert Op.RANK_TS in f.forbidden_ops
        assert not (f.allowed_ops & f.forbidden_ops)
        if "ES" not in f.data_needed and "WDO" not in f.data_needed:
            assert Op.REF not in f.allowed_ops


# ---------------------------------------------------------------- recompensa e bandit


def test_recompensa_cobre_todo_veredito_e_so_veredito() -> None:
    assert set(REWARD) == set(Verdict)
    assert list(inspect.signature(reward).parameters) == ["v"]
    assert REWARD[Verdict.ACCEPTED] == 1.0 and REWARD[Verdict.FAILED_GATE] == 0.8
    src = inspect.getsource(bandit_module).lower()
    assert "sharpe" not in src and "pnl" not in src


def test_ucb_escolhe_intermercado_sp500_no_cenario() -> None:
    history = (
        [("INTERMERCADO_SP500", v) for v in (Verdict.FAILED_GATE, Verdict.UNSTABLE_ACROSS_FOLDS,
                                               Verdict.COST_DOMINATED)]
        + [("ABERTURA_E_GAP", Verdict.INVALID_AST)] * 8
        + [("ABERTURA_E_GAP", Verdict.COST_DOMINATED)] * 2
        + [("REGIME_DE_VOL", Verdict.TOO_COMPLEX)] * 6
        + [("REGIME_DE_VOL", Verdict.INSUFFICIENT_SAMPLE)] * 2
    )
    assert bandit.pick(select(STARTER), history).name == "INTERMERCADO_SP500"
    s = bandit.scores(select(STARTER), history)
    assert s["INTERMERCADO_SP500"] == pytest.approx(
        bandit.ucb(1.9 / 3, 3, 21)
    )


def test_familia_nunca_escolhida_tem_prioridade_e_indisponivel_nunca_sai() -> None:
    history = [("INTERMERCADO_SP500", Verdict.ACCEPTED)] * 5
    assert bandit.pick(select(STARTER), history).name == "ABERTURA_E_GAP"
    only_book = [get(FAMILIES, "FLUXO_DE_AGRESSAO")]
    with pytest.raises(bandit.NoFamilyAvailable):
        bandit.pick(only_book, [])
    assert bandit.pick(FAMILIES, []).available


def test_historico_vem_dos_registros_de_veredito(led: Ledger) -> None:
    st = TrialState("t1", H001, Budget(), H001.dsl_constraints, attempt=1)
    formula_agent.record_verdict(led, st, Verdict.COST_DOMINATED, SP500.name)
    assert bandit.history_from_ledger(led) == [(SP500.name, Verdict.COST_DOMINATED)]
    assert led.count() == 0


# ---------------------------------------------------------------- hipótese

GOOD: dict[str, Any] = {
    "claim": "Após choque do S&P, o WIN reprecifica com atraso de 1 a 3 minutos.",
    "who_pays": "Market makers do WIN, que alargam o spread e não repassam o movimento.",
    "observable": "diferença de z-scores do retorno de 2 minutos entre ES e WIN",
    "direction": "LONG_ON_HIGH",
    "horizon": "PT3M",
    "session_window": ["10:30", "16:30"],
    "regime_filter": None,
    "kill_condition": {"metric": "lag_ms", "aggregation": "mediana", "window_days": 90,
                       "operator": "<", "threshold": 20000.0, "unit": "ms"},
}


@pytest.mark.parametrize(
    ("who", "circular"),
    [("o mercado", True), ("Os traders", True), ("reversão", True), ("a tendência", True),
     ("", True), ("investidores em geral", True),
     ("market makers que alargam o spread", False), ("hedger local do dólar", False)],
)
def test_quem_paga_circular(who: str, circular: bool) -> None:
    assert hyp_agent.is_circular(who) is circular


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"who_pays": "o mercado"}, hyp_agent.Rejection.CIRCULAR_PAYER),
        ({"kill_condition": {**GOOD["kill_condition"], "metric": "book_imbalance"}},
         hyp_agent.Rejection.NOT_INSTRUMENTABLE),
        ({"horizon": "PT45M"}, hyp_agent.Rejection.HORIZON_OUT_OF_RANGE),
        ({"observable": "   "}, hyp_agent.Rejection.NOT_COMPUTABLE),
    ],
)
def test_validacoes_da_hipotese(change: dict[str, Any], reason: hyp_agent.Rejection) -> None:
    h = hyp_agent.parse_response(json.dumps({**GOOD, **change}), SP500, "h9")
    with pytest.raises(hyp_agent.HypothesisRejected) as e:
        hyp_agent.validate_hypothesis(h, SP500, set())
    assert e.value.reason is reason


def test_restricoes_derivadas_da_familia() -> None:
    h = hyp_agent.parse_response(json.dumps(GOOD), SP500, "h9")
    c = h.dsl_constraints
    assert c.allowed_ops == SP500.allowed_ops and c.required_refs == {"ES"}
    assert c.session_mask == ("10:30", "16:30")


def test_hipotese_circular_rejeitada_sem_consumir_tentativa(led: Ledger) -> None:
    llm = ScriptedLLM([json.dumps({**GOOD, "who_pays": "os traders"}), json.dumps(GOOD)])
    res = hyp_agent.generate(llm, SP500, led, "h100", seed=1)
    assert res.rejections == (hyp_agent.Rejection.CIRCULAR_PAYER,)
    assert led.count() == 0
    assert led.count(Kind.HYPOTHESIS) == 1
    # a mesma tese não passa de novo
    llm2 = ScriptedLLM([json.dumps(GOOD)] * hyp_agent.MAX_HYPOTHESIS_ATTEMPTS)
    with pytest.raises(hyp_agent.HypothesisExhausted):
        hyp_agent.generate(llm2, SP500, led, "h101", seed=1)
    assert "Após choque do S&P" in llm2.payloads[0]  # tese explorada vai no prompt
    assert led.count() == 0


def test_prompt_de_hipotese_nao_tem_metrica() -> None:
    payload = hyp_agent.build_payload(SP500, ["tese antiga"])
    body = json.loads(payload)
    assert set(body) == {"task", "family", "explored_claims", "instrumentable_metrics",
                         "requirements"}
    assert "criativ" not in payload.lower()


# ---------------------------------------------------------------- fórmula


def test_schema_contem_so_operadores_da_hipotese() -> None:
    schema = formula_agent.tool_schema(H001.dsl_constraints)
    enum = set(schema["input_schema"]["$defs"]["node"]["properties"]["op"]["enum"])
    expected = {"zscore", "ret", "lag", "rolling_std", "sub", "mul", "in_window", "ref",
                "close", "high", "low", "volume", "trades", "vwap"}
    assert enum == expected
    assert "rank_ts" not in enum and "ema" not in enum and "const" not in enum


def test_ast_do_json() -> None:
    assert formula_agent.parse_response(formula_json(VALID)) == parse(VALID)
    for bad in ['{"formula": {"op": "nope", "args": []}}', "não é json",
                '{"formula": {"op": "close", "args": [1.5]}}', '{"x": 1}',
                '{"formula": {"op": "close", "args": [], "extra": 1}}']:
        with pytest.raises(DslParseError):
            formula_agent.parse_response(bad)


def st_for(**over: Any) -> TrialState:
    s = TrialState("t1", H001, Budget(max_attempts=5), H001.dsl_constraints)
    for k, v in over.items():
        setattr(s, k, v)
    return s


def test_realimentacao_por_veredito() -> None:
    c = formula_agent.feedback(st_for(verdict=Verdict.TOO_COMPLEX), SP500)["constraints"]
    assert c.max_depth == H001.dsl_constraints.max_depth - 1
    c = formula_agent.feedback(st_for(verdict=Verdict.UNSTABLE_ACROSS_FOLDS), SP500)["constraints"]
    assert c.max_window == 60
    h = formula_agent.feedback(st_for(verdict=Verdict.COST_DOMINATED), SP500)["hypothesis"]
    assert h.horizon == timedelta(minutes=5)  # 3 -> 5
    long = dataclasses.replace(H001, horizon=timedelta(minutes=10))
    h = formula_agent.feedback(st_for(verdict=Verdict.COST_DOMINATED, hypothesis=long),
                               SP500)["hypothesis"]
    assert h.horizon == timedelta(minutes=10)  # limitado à família
    assert formula_agent.feedback(st_for(verdict=Verdict.INVALID_AST), SP500) == {}


def test_redundante_bloqueia_e_bane_raiz(led: Ledger) -> None:
    from src.backtest.runner import run
    run(parse(VALID), "h001", "antes/1", BT_CFG, led, OkEngine())
    node = formula_agent.make_node(ScriptedLLM([formula_json(VALID)]), led, SP500)
    out = node(st_for())
    assert out["verdict"] is Verdict.REDUNDANT
    assert structural_signature(parse(VALID)) in out["blocked_sigs"]
    assert Op.MUL in out["constraints"].forbidden_ops
    assert led.count() == 1  # REDUNDANT não é tentativa


# ---------------------------------------------------------------- sessão


def aggregate(m: Metrics) -> GateInputs:
    return GateInputs(2000, 60, 300, 0.05, 20000.0, 30000.0, 0.1, 0.05, 0.1, 2000, 0.0, 3.0,
                      True)  # custo maior que o bruto: COST_DOMINATED


def deps(led: Ledger, llm: ScriptedLLM, **over: Any) -> SessionDeps:
    d = SessionDeps(ledger=led, engine=OkEngine(), client=llm, backtest_cfg=BT_CFG,
                    gate_cfg=GATE, budget=Budget(max_attempts=5), aggregate=aggregate,
                    var_sr=lambda: 0.001, codegen=lambda st: {})
    return dataclasses.replace(d, **over)


def test_sessao_sem_decisoes_do_gate_recusa(led: Ledger) -> None:
    with pytest.raises(GatePending):
        build_nodes(deps(led, ScriptedLLM([]), aggregate=None), SP500)


def test_count_nao_cresce_em_retry_de_invalid_ast(led: Ledger) -> None:
    llm = ScriptedLLM([formula_json('sub(close(), ref("ES"))')] * 2
                      + ['{"quebrado": true}']
                      + [formula_json(VALID)]
                      + [formula_json(VALID.replace("55", "89"))])
    out = run_hypothesis(deps(led, llm), SP500, H001, "s1/h001")
    assert out.attempt == 5
    assert led.count() == 2  # só as duas fórmulas válidas foram ao backtest
    verdicts = [r.verdict for r in led.records(Kind.VERDICT)]
    assert verdicts == [Verdict.INVALID_AST] * 3 + [Verdict.COST_DOMINATED] * 2


def number_forms(v: float) -> set[str]:
    a = abs(v)
    if a.is_integer():
        return {str(int(a))} if a >= 1000 else set()
    return {repr(a), f"{a:.2f}", f"{a:.3f}"}


class MetricEngine(OkEngine):
    """Métricas com números inconfundíveis, para a auditoria achar qualquer vazamento."""

    def evaluate(self, req: Any) -> Any:
        out = super().evaluate(req)
        folds = tuple(dataclasses.replace(
            fold(i), sharpe=1.7349 + i / 1000, net_pnl_points=73519.0 + i,
            gross_pnl_points=77689.0 + i, cost_points=4170.0, hit_rate=0.5813,
            skew=-0.3971, kurtosis=4.4417, max_dd_points=5147.5, max_day_share=0.0917)
            for i in range(28))
        return dataclasses.replace(out, folds=folds)


def test_auditoria_de_50_payloads(led: Ledger) -> None:
    windows = [2, 3, 5, 8, 13, 21, 34, 55, 89, 1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 1] * 3
    answers = [formula_json(VALID.replace("55", str(w)).replace(",2)", f",{(i % 3) + 1})"))
               for i, w in enumerate(windows)]
    llm = ScriptedLLM(answers)
    d = deps(led, llm, engine=MetricEngine())
    for i in range(10):
        h = dataclasses.replace(H001, id=f"h{i:03d}")
        run_hypothesis(d, SP500, h, f"s1/h{i:03d}")
    assert len(llm.payloads) == 50
    forms: set[str] = set()
    for f in MetricEngine().evaluate(
        SimpleNamespace(test_id="x", data_hash="y")).folds:
        for v in f.__dict__.values():
            if isinstance(v, float):
                forms |= number_forms(v)
    for p in llm.payloads:
        assert not re.search(r"sharpe|pnl|drawdown", p, re.IGNORECASE)
        for s in forms:
            assert not re.search(rf"(?<![\d.]){re.escape(s)}(?!\d)", p), s


# ---------------------------------------------------------------- cliente


def formula_payload() -> str:
    from src.orchestrator.projection import research_payload
    return research_payload(st_for(formula_ast=parse(VALID), verdict=Verdict.COST_DOMINATED))


def test_requisicao_do_cliente() -> None:
    req = agent_client.build_request("formula", formula_payload(), agent_client.ClientConfig())
    assert req["model"] == "claude-opus-5"
    assert req["tool_choice"] == {"type": "tool", "name": "emit_formula"}
    assert req["fallbacks"] == "default" and req["betas"] == ["server-side-fallback-2026-07-01"]
    enum = req["tools"][0]["input_schema"]["$defs"]["node"]["properties"]["op"]["enum"]
    assert "ema" not in enum
    assert req["messages"][0]["content"] == formula_payload()
    h = agent_client.build_request("hypothesis", hyp_agent.build_payload(SP500, []),
                                   agent_client.ClientConfig(server_fallback=False))
    assert h["tool_choice"]["name"] == "emit_hypothesis" and "fallbacks" not in h


def test_cliente_barra_metrica_antes_de_enviar() -> None:
    bad = json.dumps({"hypothesis": hypothesis_to_dict(H001), "sharpe": 1.2})
    with pytest.raises(AssertionError):
        agent_client.build_request("formula", bad, agent_client.ClientConfig())


def test_extracao_da_resposta() -> None:
    block = SimpleNamespace(type="tool_use", name="emit_formula", input={"formula": {"op": "close",
                                                                                    "args": []}})
    ok = SimpleNamespace(stop_reason="tool_use", content=[SimpleNamespace(type="thinking"), block])
    assert json.loads(agent_client.extract(ok, "emit_formula"))["formula"]["op"] == "close"
    with pytest.raises(agent_client.AgentRefused):
        agent_client.extract(SimpleNamespace(stop_reason="refusal", content=[]), "emit_formula")
    with pytest.raises(agent_client.AgentOutputMissing):
        agent_client.extract(SimpleNamespace(stop_reason="end_turn", content=[]), "emit_formula")


def test_registro_de_hipotese_exige_payload(led: Ledger) -> None:
    from src.ledger.ledger import InvalidRecord
    g = led.genesis_info()
    with pytest.raises(InvalidRecord):
        led.append(LedgerRecord("x", Kind.HYPOTHESIS, g.data_hash, g.config_hash, "h1"))
    with pytest.raises(InvalidRecord):
        led.append(LedgerRecord("y", Kind.VERDICT, g.data_hash, g.config_hash, "h1",
                                payload={"family": "X"}))


def test_serializacao_e_familias_importadas() -> None:
    assert families.STARTER == STARTER
    assert serialize(parse(VALID)).startswith("(mul")
