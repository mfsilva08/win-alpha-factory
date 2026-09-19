"""Escolha da família por UCB1 sobre recompensas categóricas (SPEC-fase-3 §3.7).

Um *pull* é um registro ``kind='verdict'`` do livro-razão: o veredito de uma
tentativa de fórmula, com a família no payload. A escolha é determinística:
família nunca escolhida primeiro (na ordem do catálogo); depois o maior UCB, com
empate resolvido pela ordem do catálogo.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from src.catalog.families import Family
from src.catalog.reward import reward
from src.dsl.verdict import Verdict
from src.ledger.ledger import Kind, Ledger

C_EXPLORE = 0.8


class NoFamilyAvailable(Exception):
    """Nenhuma família do catálogo tem os dados de que precisa."""


def ucb(mean_reward: float, pulls: int, total: int) -> float:
    return mean_reward + C_EXPLORE * math.sqrt(math.log(total) / pulls)


def history_from_ledger(ledger: Ledger) -> list[tuple[str, Verdict]]:
    out: list[tuple[str, Verdict]] = []
    for r in ledger.records(Kind.VERDICT):
        assert r.verdict is not None and r.payload is not None
        out.append((str(r.payload["family"]), r.verdict))
    return out


def scores(families: Sequence[Family],
           history: Sequence[tuple[str, Verdict]]) -> dict[str, float | None]:
    """UCB por família disponível; ``None`` para a nunca escolhida."""
    avail = [f for f in families if f.available]
    names = {f.name for f in avail}
    pulls = {f.name: 0 for f in avail}
    total_reward = {f.name: 0.0 for f in avail}
    for fam, v in history:
        if fam in names:
            pulls[fam] += 1
            total_reward[fam] += reward(v)
    total = sum(pulls.values())
    return {
        f.name: None if pulls[f.name] == 0
        else ucb(total_reward[f.name] / pulls[f.name], pulls[f.name], total)
        for f in avail
    }


def pick(families: Sequence[Family], history: Sequence[tuple[str, Verdict]]) -> Family:
    """Maior UCB entre as disponíveis. Família nunca escolhida tem prioridade."""
    s = scores(families, history)
    avail = [f for f in families if f.available]
    if not avail:
        raise NoFamilyAvailable("nenhuma família disponível")
    best: Family | None = None
    best_score = -math.inf
    for f in avail:
        score = s[f.name]
        if score is None:
            return f
        if score > best_score:  # empate fica com o primeiro do catálogo
            best, best_score = f, score
    assert best is not None
    return best
