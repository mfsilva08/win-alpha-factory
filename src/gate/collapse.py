"""O colapso: métricas agregadas viram **um** veredito categórico (SPEC-fase-2 §2.11).

Ordem: do mais barato e mais acionável ao mais caro. PBO, DSR e robustez colapsam
para o mesmo ``FAILED_GATE`` de propósito — saber qual falhou é informação
quantitativa sobre a superfície (R1).

``GateInputs`` já chega agregado. Como agregar as partições do CPCV (28 partições
ou 7 caminhos) é decisão pendente — ver SPEC-fase-2 §2.3.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.dsl.verdict import Verdict
from src.gate.config import GateConfig
from src.gate.dsr import deflated_sharpe, sr_star


@dataclass(frozen=True)
class GateInputs:
    total_trades: int
    min_path_trades: int  # o menor número de operações entre os caminhos
    active_days: int
    max_day_share: float  # maior dia / PnL total
    gross_points: float
    cost_points: float
    sign_flip_frac: float  # fração de caminhos com sinal oposto ao agregado
    pbo: float
    sr: float  # Sharpe por observação (não anualizado)
    n_obs: int  # T do DSR, na mesma frequência de ``sr``
    skew: float
    kurtosis: float  # não excedente (normal = 3)
    robustness_all_passed: bool


def dsr(m: GateInputs, n_trials: int, var_sr: float) -> float:
    return deflated_sharpe(m.sr, sr_star(var_sr, n_trials), m.n_obs, m.skew, m.kurtosis)


def collapse(m: GateInputs, n_trials: int, var_sr: float, cfg: GateConfig) -> Verdict:
    """``n_trials`` vem de ``ledger.count()`` — nunca de contador local."""
    # triagem de sanidade: amostra pequena ou concentrada demais para julgar
    if m.total_trades < cfg.min_total_trades:
        return Verdict.INSUFFICIENT_SAMPLE
    if m.min_path_trades < cfg.min_trades_per_path:
        return Verdict.INSUFFICIENT_SAMPLE
    if m.active_days < cfg.min_active_days:
        return Verdict.INSUFFICIENT_SAMPLE
    if m.max_day_share > cfg.max_trade_concentration:
        return Verdict.INSUFFICIENT_SAMPLE
    if m.gross_points <= m.cost_points:
        return Verdict.COST_DOMINATED
    if m.sign_flip_frac > cfg.max_sign_flip_frac:
        return Verdict.UNSTABLE_ACROSS_FOLDS
    if m.pbo > cfg.max_pbo:
        return Verdict.FAILED_GATE
    if dsr(m, n_trials, var_sr) < cfg.min_dsr:
        return Verdict.FAILED_GATE
    if not m.robustness_all_passed:
        return Verdict.FAILED_GATE
    return Verdict.ACCEPTED
