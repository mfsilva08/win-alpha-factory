"""Probabilidade de overfitting do backtest por CSCV (Bailey et al., 2015) — §2.9.

Entrada: matriz de desempenho ``perf`` de forma (T, N) — T observações no tempo,
N configurações candidatas. Para cada combinação de metade dos S blocos como
dentro da amostra, escolhe a melhor configuração ali e mede o posto relativo
``ω`` dela fora da amostra. PBO é a fração de combinações com ``logit(ω) <= 0``,
isto é, em que a campeã cai na metade de baixo.

Quais são as N configurações de uma fórmula é decisão pendente do M4 (ver
SPEC-fase-2 §2.9). Esta função é só o algoritmo.
"""

from __future__ import annotations

import math
from itertools import combinations

import numpy as np
import numpy.typing as npt
from scipy.stats import rankdata

from src.gate.errors import StatisticsError


def _sharpe(block: npt.NDArray[np.float64]) -> npt.NDArray[np.float64]:
    mean = block.mean(axis=0)
    std = block.std(axis=0, ddof=1)
    out: npt.NDArray[np.float64] = np.where(std > 1e-12, mean / np.where(std > 1e-12, std, 1.0), 0.0)
    return out


def pbo(perf: npt.ArrayLike, n_blocks: int = 16) -> float:
    m = np.asarray(perf, dtype=np.float64)
    if m.ndim != 2:
        raise StatisticsError("perf deve ser uma matriz (T, N)")
    t, n = m.shape
    if n < 2:
        raise StatisticsError("PBO exige ao menos duas configurações")
    if n_blocks < 2 or n_blocks % 2:
        raise StatisticsError("n_blocks deve ser par e >= 2")
    if t < 2 * n_blocks:
        raise StatisticsError("observações insuficientes para os blocos")
    if not np.all(np.isfinite(m)):
        raise StatisticsError("perf contém NaN ou inf")
    blocks = np.array_split(np.arange(t), n_blocks)
    below = 0
    total = 0
    for is_ids in combinations(range(n_blocks), n_blocks // 2):
        is_rows = np.concatenate([blocks[i] for i in is_ids])
        oos_rows = np.concatenate([blocks[i] for i in range(n_blocks) if i not in is_ids])
        best = int(np.argmax(_sharpe(m[is_rows])))
        ranks = rankdata(_sharpe(m[oos_rows]))
        omega = float(ranks[best]) / (n + 1)
        if math.log(omega / (1 - omega)) <= 0:
            below += 1
        total += 1
    return below / total
