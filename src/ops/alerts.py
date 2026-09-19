"""Os três alertas, e por que não podem ser confundidos (SPEC-fase-4 §4.4).

| Alerta       | Gatilho                                                 | Ação                         |
|--------------|---------------------------------------------------------|------------------------------|
| OPERACIONAL  | barras gravadas != esperadas, rejeições, guard inesperado| consertar código/infra       |
| DIVERGENCIA  | slippage fora de ±30% do modelado; paridade > 1e-9      | recalibrar custo ou bug      |
| DECAIMENTO   | ``evaluate_kill`` devolve ``DEAD``                      | **desligar** — por uma pessoa|

Prejuízo **não está na lista**. Os alertas vão para um arquivo JSONL e para a
saída padrão; nenhum deles age sobre o robô.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date
from enum import StrEnum
from pathlib import Path


class Alert(StrEnum):
    OPERACIONAL = "operacional"
    DIVERGENCIA = "divergencia"
    DECAIMENTO = "decaimento"


ACTION: dict[Alert, str] = {
    Alert.OPERACIONAL: "Consertar o código ou a infraestrutura. Não mexer na estratégia.",
    Alert.DIVERGENCIA: "Recalibrar o CostModel ou corrigir a implementação. A estratégia continua.",
    Alert.DECAIMENTO: "Desligar a estratégia. Ação manual, registrada.",
}


@dataclass(frozen=True)
class AlertEvent:
    kind: Alert
    day: str
    hypothesis_id: str
    message: str

    @property
    def action(self) -> str:
        return ACTION[self.kind]


def event(kind: Alert, day: date, hypothesis_id: str, message: str) -> AlertEvent:
    return AlertEvent(kind, day.isoformat(), hypothesis_id, message)


def emit(events: Sequence[AlertEvent], log_path: Path | None) -> None:
    """Acrescenta ao log JSONL e imprime. Não faz mais nada — de propósito."""
    for e in events:
        line = {**asdict(e), "kind": e.kind.value, "action": e.action}
        print(f"[{e.kind.value.upper()}] {e.day} {e.hypothesis_id}: {e.message} — {e.action}")
        if log_path is not None:
            with log_path.open("a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(line, ensure_ascii=False, sort_keys=True) + "\n")
