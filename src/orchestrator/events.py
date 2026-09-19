"""Log de eventos para replay determinístico (SPEC-fase-3 §3.5).

Cada chamada a um agente registra a semente, o hash do payload enviado e o hash
da resposta, além da própria resposta. Em replay, o ``ReplayClient`` serve as
respostas gravadas — e recusa se o payload montado agora não for o mesmo de antes,
porque isso significa que o caminho divergiu.

O log é auditoria, não coordenação (R7): ninguém lê o log para decidir nada ao vivo.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from typing import Protocol


def text_hash(s: str) -> str:
    return sha256(s.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Event:
    node: str
    attempt: int
    seed: int
    payload_hash: str
    response_hash: str
    response: str


class AgentClient(Protocol):
    """O que um nó de agente usa para falar com o modelo. Uma chamada por nó."""

    def complete(self, node: str, payload: str, seed: int) -> str: ...


class ReplayDiverged(Exception):
    """O payload do replay não bate com o gravado: o caminho não é o mesmo."""


class ReplayClient:
    """Serve as respostas gravadas, na ordem, conferindo cada payload."""

    def __init__(self, events: Sequence[Event]) -> None:
        self._events = list(events)
        self._i = 0

    def complete(self, node: str, payload: str, seed: int) -> str:
        if self._i >= len(self._events):
            raise ReplayDiverged("replay pediu mais chamadas do que as gravadas")
        ev = self._events[self._i]
        self._i += 1
        if (ev.node, ev.seed, ev.payload_hash) != (node, seed, text_hash(payload)):
            raise ReplayDiverged(f"chamada {self._i}: payload ou semente diferente em {node}")
        if text_hash(ev.response) != ev.response_hash:
            raise ReplayDiverged(f"chamada {self._i}: resposta gravada adulterada")
        return ev.response


def record(node: str, attempt: int, seed: int, payload: str, response: str) -> Event:
    return Event(node, attempt, seed, text_hash(payload), text_hash(response), response)


def write_jsonl(events: Sequence[Event], path: Path) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as f:  # nunca sobrescreve
        for ev in events:
            f.write(json.dumps(asdict(ev), sort_keys=True, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[Event]:
    out: list[Event] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(Event(**json.loads(line)))
    return out
