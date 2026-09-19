"""Modelo de custo do WIN, lido de ``config/costs.yaml`` (SPEC-fase-2 §2.1).

Os valores nunca ficam no código. O modelo é enviado ao motor em cada requisição,
para que o custo aplicado seja sempre o do arquivo versionado.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from src.backtest.errors import CostConfigError

_FIELDS = (
    "point_value",
    "tick_size",
    "slippage_ticks",
    "exchange_fee",
    "brokerage",
    "margin_per_contract",
)


@dataclass(frozen=True)
class CostModel:
    point_value: float  # R$ por ponto do WIN
    tick_size: int  # pontos
    slippage_ticks: int  # por perna, pessimista
    exchange_fee: float  # taxas B3, por contrato por lado
    brokerage: float  # por contrato por lado
    margin_per_contract: float

    def __post_init__(self) -> None:
        for name in _FIELDS:
            v = getattr(self, name)
            if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v):
                raise CostConfigError(f"{name}: valor numérico inválido ({v!r})")
            if v < 0:
                raise CostConfigError(f"{name}: não pode ser negativo")
        for name in ("tick_size", "slippage_ticks"):
            if not isinstance(getattr(self, name), int):
                raise CostConfigError(f"{name}: precisa ser inteiro")
        if self.point_value <= 0 or self.tick_size <= 0 or self.margin_per_contract <= 0:
            raise CostConfigError("point_value, tick_size e margin_per_contract devem ser > 0")

    def round_trip_points(self) -> int:
        return 2 * self.slippage_ticks * self.tick_size

    def fees_brl(self, contracts: int) -> float:
        return (self.exchange_fee + self.brokerage) * contracts * 2

    def scaled(self, factor: float) -> CostModel:
        """Custos multiplicados por ``factor`` — bateria de robustez (1,5× e 2,0×).

        ``slippage_ticks`` é inteiro: arredonda para cima, nunca a favor.
        """
        if factor <= 0:
            raise CostConfigError("fator de custo deve ser positivo")
        return CostModel(
            point_value=self.point_value,
            tick_size=self.tick_size,
            slippage_ticks=math.ceil(self.slippage_ticks * factor),
            exchange_fee=self.exchange_fee * factor,
            brokerage=self.brokerage * factor,
            margin_per_contract=self.margin_per_contract,
        )

    def as_dict(self) -> dict[str, float | int]:
        return {name: getattr(self, name) for name in _FIELDS}


def load_costs(path: Path) -> CostModel:
    try:
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as e:
        raise CostConfigError(f"não foi possível ler {path}: {e}") from None
    if not isinstance(raw, dict):
        raise CostConfigError(f"{path}: esperado um mapeamento YAML")
    missing = [f for f in _FIELDS if f not in raw]
    if missing:
        raise CostConfigError(f"{path}: campos ausentes {missing}")
    extra = sorted(set(raw) - set(_FIELDS))
    if extra:
        raise CostConfigError(f"{path}: campos desconhecidos {extra}")
    return CostModel(**{f: raw[f] for f in _FIELDS})
