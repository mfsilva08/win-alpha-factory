"""SPEC-fase-2 §2.7–2.11: SR*, DSR, pré-registro hasheado, PBO, robustez e colapso."""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest
from scipy.stats import norm

from src.dsl.ast import windows
from src.dsl.canonical import structural_signature
from src.dsl.parser import parse
from src.dsl.verdict import Verdict
from src.gate import config as gate_config
from src.gate.collapse import GateInputs, collapse, dsr
from src.gate.dsr import deannualize, deflated_sharpe, sr_star, var_sr
from src.gate.errors import PreregIncomplete, PreregInvalid, PreregMismatch, StatisticsError
from src.gate.pbo import pbo
from src.gate.robustness import (
    NOISE_RESAMPLES,
    RobustnessResult,
    judge,
    neighbor_variants,
    noise_ok,
)
from src.ledger.ledger import Ledger, sha256_file, sha256_hex

ROOT = Path(__file__).resolve().parents[1]
REAL_PREREG = ROOT / "docs" / "gate-prereg.md"

# ---------------------------------------------------------------- SR* e DSR


def test_sr_star_valores_da_spec() -> None:
    assert sr_star(0.60, 2400) == pytest.approx(2.71, abs=0.005)
    assert sr_star(0.60, 10) == pytest.approx(1.22, abs=0.005)


def test_sr_star_valores_do_prereg() -> None:
    assert sr_star(0.60, 500) == pytest.approx(2.36, abs=0.005)
    assert sr_star(0.60, 3000) == pytest.approx(2.75, abs=0.005)
    assert sr_star(0.60, 5000) == pytest.approx(2.86, abs=0.005)


def test_sr_star_cresce_com_tentativas_e_escala_com_desvio() -> None:
    xs = [sr_star(1.0, n) for n in (2, 10, 100, 1000, 10000)]
    assert xs == sorted(xs)
    assert sr_star(4.0, 100) == pytest.approx(2 * sr_star(1.0, 100))
    assert sr_star(0.6, 1) == 0.0


def test_dsr_e_meio_quando_sr_igual_ao_limiar() -> None:
    assert deflated_sharpe(0.1, 0.1, 500, -0.5, 6.0) == pytest.approx(0.5)


def test_dsr_normal_coincide_com_psr_fechado() -> None:
    """Retornos normais (skew 0, kurt 3): DSR = Φ((SR−SR*)·√(T−1) / √(1 + SR²/2))."""
    sr, s0, t = 0.12, 0.05, 1000
    expected = norm.cdf((sr - s0) * math.sqrt(t - 1) / math.sqrt(1 + sr**2 / 2))
    assert deflated_sharpe(sr, s0, t, 0.0, 3.0) == pytest.approx(expected)


def test_dsr_penaliza_cauda_esquerda() -> None:
    base = deflated_sharpe(0.1, 0.05, 1000, 0.0, 3.0)
    assert deflated_sharpe(0.1, 0.05, 1000, -1.0, 3.0) < base
    assert deflated_sharpe(0.1, 0.05, 1000, 0.0, 10.0) < base


def test_exemplo_anualizado_convertido() -> None:
    # 5 anos diários, Sharpe anual 2,5 contra limiar anual 1,0: evidência forte
    sr = deannualize(2.5, 252)
    s0 = deannualize(1.0, 252)
    assert deflated_sharpe(sr, s0, 1260, 0.0, 3.0) > 0.99
    # o mesmo Sharpe anual contra limiar anual 2,4 não passa de 0,95
    assert deflated_sharpe(sr, deannualize(2.4, 252), 1260, 0.0, 3.0) < 0.95


@pytest.mark.parametrize(
    ("args", "msg"),
    [((0.1, 0.0, 1, 0.0, 3.0), "T"), ((3.0, 0.0, 100, 2.0, 1.0), "denominador")],
)
def test_dsr_dominio(args: tuple[float, float, int, float, float], msg: str) -> None:
    with pytest.raises(StatisticsError, match=msg):
        deflated_sharpe(*args)


def test_var_sr() -> None:
    assert var_sr([1.0, 2.0, 3.0]) == pytest.approx(1.0)
    with pytest.raises(StatisticsError):
        var_sr([1.0])


# ---------------------------------------------------------------- pré-registro


def filled_prereg(max_trials: str = "3000") -> str:
    """O pré-registro real com os marcadores preenchidos, como ficará após o M0."""
    fills = {
        "project_id:": "win-intraday-2026",
        "escrito_em:": "2026-09-19",
        "autor:": "teste",
        "capital_estudo:": "5000.00",
        "contratos_max:": "2",
        "diretorio:": "C:/dados/holdout",
        "MAX_TRIALS:": max_trials,
    }
    out = []
    for line in REAL_PREREG.read_text(encoding="utf-8").splitlines(keepends=True):
        for key, value in fills.items():
            if line.lstrip().startswith(key) and gate_config.MARKER in line:
                line = line.replace(gate_config.MARKER, value)
        out.append(line)
    return "".join(out)


@pytest.fixture
def prereg(tmp_path: Path) -> Path:
    p = tmp_path / "gate-prereg.md"
    p.write_bytes(filled_prereg().encode("utf-8"))
    return p


@pytest.fixture
def led(tmp_path: Path, prereg: Path) -> Iterator[Ledger]:
    with Ledger(tmp_path / "l.sqlite") as lg:
        lg.genesis(sha256_hex(b"d"), sha256_file(prereg), sha256_hex(b"catalogo"))
        yield lg


def test_prereg_real_ainda_esta_incompleto(tmp_path: Path) -> None:
    """Hoje o arquivo tem 8 marcadores: o gate precisa recusar."""
    with Ledger(tmp_path / "l.sqlite") as lg:
        lg.genesis(sha256_hex(b"d"), sha256_file(REAL_PREREG), sha256_hex(b"c"))
        with pytest.raises(PreregIncomplete, match="MAX_TRIALS"):
            gate_config.load(lg, REAL_PREREG)


def test_prereg_preenchido_carrega(led: Ledger, prereg: Path) -> None:
    cfg = gate_config.load(led, prereg)
    assert cfg == gate_config.GateConfig(
        min_total_trades=400, min_trades_per_path=30, min_active_days=120,
        max_trade_concentration=0.25, max_sign_flip_frac=0.30, max_pbo=0.20, min_dsr=0.95,
        max_trials=3000, n_groups=8, k_test=2, embargo_mult=3.0,
    )


def test_prereg_alterado_depois_do_genesis(led: Ledger, prereg: Path) -> None:
    prereg.write_bytes(filled_prereg("5000").encode("utf-8"))
    with pytest.raises(PreregMismatch):
        gate_config.load(led, prereg)


def test_um_byte_de_quebra_de_linha_muda_o_hash(led: Ledger, prereg: Path) -> None:
    prereg.write_bytes(prereg.read_bytes().replace(b"\n", b"\r\n"))
    with pytest.raises(PreregMismatch):
        gate_config.load(led, prereg)


def test_marcador_na_prosa_nao_conta() -> None:
    # o cabeçalho do arquivo cita o marcador; só os blocos de código importam
    assert gate_config.MARKER in filled_prereg().split("```")[0]
    gate_config.parse(filled_prereg())


@pytest.mark.parametrize(
    ("old", "new", "exc"),
    [
        ("max_pbo:            0.20", "max_pbo:            1.20", PreregInvalid),
        ("caminhos:     28", "caminhos:     7", PreregInvalid),
        ("min_dsr:            0.95", "min_dsr:            muito", PreregInvalid),
        ("min_dsr:            0.95\n", "", PreregIncomplete),
    ],
)
def test_prereg_invalido(old: str, new: str, exc: type[Exception]) -> None:
    text = filled_prereg()
    assert old in text
    with pytest.raises(exc):
        gate_config.parse(text.replace(old, new))


def test_sem_override_por_ambiente(monkeypatch: pytest.MonkeyPatch, led: Ledger,
                                   prereg: Path) -> None:
    monkeypatch.setenv("MIN_DSR", "0.10")
    monkeypatch.setenv("GATE_MIN_DSR", "0.10")
    assert gate_config.load(led, prereg).min_dsr == 0.95


# ---------------------------------------------------------------- PBO


def test_pbo_sobre_ruido_perto_de_meio() -> None:
    rng = np.random.default_rng(1)
    p = pbo(rng.normal(0, 1, (2000, 40)), n_blocks=10)
    assert 0.3 < p < 0.7


def test_pbo_com_configuracao_realmente_melhor_perto_de_zero() -> None:
    rng = np.random.default_rng(2)
    m = rng.normal(0, 1, (2000, 40))
    m[:, 7] += 0.5
    assert pbo(m, n_blocks=10) < 0.05


def test_pbo_dominio() -> None:
    with pytest.raises(StatisticsError):
        pbo(np.zeros((100, 1)))
    with pytest.raises(StatisticsError):
        pbo(np.zeros((100, 4)), n_blocks=5)


# ---------------------------------------------------------------- robustez


def test_bateria_conjuntiva() -> None:
    good = judge(100.0, [5.0] * NOISE_RESAMPLES, [1.0, 2.0, 3.0], [10.0, 1.0], [4.0, 9.0])
    assert good.all_passed
    for field in ("noise", "subperiods", "costs", "parameters"):
        flags = {"noise": True, "subperiods": True, "costs": True, "parameters": True}
        flags[field] = False
        assert not RobustnessResult(**flags).all_passed


def test_regras_individuais() -> None:
    assert not noise_ok([-1.0] * 51 + [9.0] * 49)
    assert not judge(100.0, [1.0] * 100, [1.0, -0.1, 3.0], [1.0, 1.0], [1.0]).subperiods
    assert not judge(100.0, [1.0] * 100, [1.0, 1.0, 1.0], [1.0, 0.0], [1.0]).costs
    assert not judge(100.0, [1.0] * 100, [1.0, 1.0, 1.0], [1.0, 1.0], [2.0, -2.0]).parameters
    assert not judge(-5.0, [1.0] * 100, [1.0, 1.0, 1.0], [1.0, 1.0], [1.0]).subperiods
    with pytest.raises(StatisticsError):
        noise_ok([1.0] * 99)


def test_variantes_de_janela_saem_do_bucket() -> None:
    h001 = parse('mul(in_window("10:30","16:30"), sub(zscore(ret(ref("ES"),2),55),'
                 " zscore(ret(close(),2),55)))")
    down, up = neighbor_variants(h001)
    assert sorted(set(windows(down))) == [1, 34]  # 2 -> 1, 55 -> 34
    assert sorted(set(windows(up))) == [3, 89]  # 2 -> 3, 55 -> 89
    sig = structural_signature(h001)
    assert structural_signature(down) != sig and structural_signature(up) != sig
    # janela 1 não desce: só sobra a variante de cima
    assert len(neighbor_variants(parse("zscore(ret(close(),1),1)"))) == 1


# ---------------------------------------------------------------- colapso

CFG = gate_config.parse(filled_prereg())
PASS = GateInputs(
    total_trades=2000, min_path_trades=60, active_days=300, max_day_share=0.05,
    gross_points=20000.0, cost_points=8000.0, sign_flip_frac=0.1, pbo=0.05,
    sr=0.25, n_obs=2000, skew=0.0, kurtosis=3.0, robustness_all_passed=True,
)


def test_colapso_aceita_o_caso_bom() -> None:
    assert dsr(PASS, 3000, var_sr=0.001) > 0.95
    assert collapse(PASS, 3000, 0.001, CFG) is Verdict.ACCEPTED


@pytest.mark.parametrize(
    ("change", "verdict"),
    [
        ({"total_trades": 399}, Verdict.INSUFFICIENT_SAMPLE),
        ({"min_path_trades": 29}, Verdict.INSUFFICIENT_SAMPLE),
        ({"active_days": 119}, Verdict.INSUFFICIENT_SAMPLE),
        ({"max_day_share": 0.26}, Verdict.INSUFFICIENT_SAMPLE),
        ({"gross_points": 8000.0}, Verdict.COST_DOMINATED),
        ({"sign_flip_frac": 0.31}, Verdict.UNSTABLE_ACROSS_FOLDS),
        ({"pbo": 0.21}, Verdict.FAILED_GATE),
        ({"sr": 0.01}, Verdict.FAILED_GATE),
        ({"robustness_all_passed": False}, Verdict.FAILED_GATE),
    ],
)
def test_colapso_cada_corte(change: dict[str, object], verdict: Verdict) -> None:
    m = dataclasses.replace(PASS, **change)  # type: ignore[arg-type]
    assert collapse(m, 3000, 0.001, CFG) is verdict


def test_ordem_mais_barato_primeiro() -> None:
    m = dataclasses.replace(PASS, total_trades=10, gross_points=0.0, pbo=0.9)
    assert collapse(m, 3000, 0.001, CFG) is Verdict.INSUFFICIENT_SAMPLE


def test_mais_tentativas_endurecem_o_gate() -> None:
    m = dataclasses.replace(PASS, sr=0.12)
    assert collapse(m, 10, 0.001, CFG) is Verdict.ACCEPTED
    assert collapse(m, 3000, 0.001, CFG) is Verdict.FAILED_GATE
