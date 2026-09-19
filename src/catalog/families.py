"""As famílias de hipótese — artefato humano (SPEC-fase-3 §3.6, catalogo-completo.md).

O sistema nunca cria, altera ou remove uma família. Acrescentar uma é decisão
consciente: muda ``catalog_sha256`` e, com ele, o ``config_hash`` do gênesis —
outro projeto.

``FAMILIES`` transcreve as oito do catálogo. ``STARTER`` são as três recomendadas
para a primeira sessão; qual conjunto entra no gênesis é decisão do M0.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256

from src.dsl.ops import Op


class CatalogError(Exception):
    """Família inexistente ou catálogo inconsistente."""


@dataclass(frozen=True)
class Family:
    name: str
    mechanism: str
    typical_payer: str
    data_needed: frozenset[str]
    horizon_range: tuple[int, int]  # minutos
    allowed_ops: frozenset[Op]
    forbidden_ops: frozenset[Op]
    session_hint: tuple[str, str] | None
    available: bool = True  # False quando falta o dado


Z, RET, LAG, RSTD, RMEAN = Op.ZSCORE, Op.RET, Op.LAG, Op.ROLLING_STD, Op.ROLLING_MEAN
SUB, MUL, DIV, GT, LT = Op.SUB, Op.MUL, Op.DIV, Op.GT, Op.LT
INW, REF, MSO, EMA, CLIP, RANK = (Op.IN_WINDOW, Op.REF, Op.MINUTES_SINCE_OPEN, Op.EMA,
                                  Op.CLIP, Op.RANK_TS)

FAMILIES: tuple[Family, ...] = (
    Family(
        name="INTERMERCADO_SP500",
        mechanism="atraso na transmissão de choque do S&P futuro",
        typical_payer="market maker que alarga spread na incerteza e não repassa",
        data_needed=frozenset({"WIN", "ES"}),
        horizon_range=(2, 10),
        allowed_ops=frozenset({Z, RET, LAG, RSTD, SUB, MUL, INW, REF}),
        forbidden_ops=frozenset({RANK}),
        session_hint=("10:30", "16:30"),
    ),
    Family(
        name="ABERTURA_E_GAP",
        mechanism="informação do overnight ainda não precificada",
        typical_payer="quem precisa executar na abertura, independente do preço",
        data_needed=frozenset({"WIN"}),
        horizon_range=(5, 30),
        allowed_ops=frozenset({Z, RET, LAG, RMEAN, RSTD, SUB, GT, INW, MSO}),
        forbidden_ops=frozenset({RANK, REF}),
        session_hint=("09:00", "11:00"),
    ),
    Family(
        name="REGIME_DE_VOL",
        mechanism="mudança de regime altera o comportamento do preço",
        typical_payer="quem opera com parâmetro fixo através da mudança",
        data_needed=frozenset({"WIN"}),
        horizon_range=(20, 60),
        allowed_ops=frozenset({Z, RSTD, EMA, RET, DIV, GT, INW, CLIP}),
        forbidden_ops=frozenset({RANK, REF}),
        session_hint=None,
    ),
    Family(
        name="INTERMERCADO_DOLAR",
        mechanism="fluxo estrangeiro move o dólar antes do índice",
        typical_payer="hedger local que precisa ajustar exposição",
        data_needed=frozenset({"WIN", "WDO"}),
        horizon_range=(5, 20),
        allowed_ops=frozenset({Z, RET, LAG, RSTD, SUB, MUL, INW, REF}),
        forbidden_ops=frozenset({RANK}),
        session_hint=("09:00", "17:00"),
    ),
    Family(
        name="REVERSAO_HORARIA",
        mechanism="exagero em horários de baixa liquidez",
        typical_payer="retail alavancado, liquidado em cascata",
        data_needed=frozenset({"WIN"}),
        horizon_range=(10, 40),
        allowed_ops=frozenset({Z, RET, RMEAN, RSTD, SUB, LT, GT, INW, MSO}),
        forbidden_ops=frozenset({RANK, REF}),
        session_hint=None,
    ),
    Family(
        name="LIQUIDEZ_E_HORARIO",
        mechanism="spread e profundidade variam previsivelmente ao longo do dia",
        typical_payer="quem executa no horário errado por conveniência",
        data_needed=frozenset({"WIN"}),
        horizon_range=(5, 20),
        allowed_ops=frozenset({Z, RMEAN, RSTD, DIV, INW, MSO, GT}),
        forbidden_ops=frozenset({RANK, REF}),
        session_hint=None,
    ),
    Family(
        name="ROLAGEM_DE_VENCIMENTO",
        mechanism="distorção de preço na virada do contrato",
        typical_payer="quem rola tarde e paga o spread alargado",
        data_needed=frozenset({"WIN"}),
        horizon_range=(1440, 4320),  # 1 a 3 dias, em minutos
        allowed_ops=frozenset({Z, RET, LAG, RMEAN, SUB, GT}),
        forbidden_ops=frozenset({RANK, REF, INW}),
        session_hint=None,
    ),
    Family(
        name="FLUXO_DE_AGRESSAO",
        mechanism="desequilíbrio entre agressores de compra e venda",
        typical_payer="market maker que não reprecifica a tempo",
        data_needed=frozenset({"WIN", "BOOK"}),
        horizon_range=(1, 5),
        allowed_ops=frozenset({Z, DIV, RMEAN, GT, INW}),
        forbidden_ops=frozenset({RANK}),
        session_hint=None,
        available=False,  # MT5 não guarda histórico de book
    ),
)

STARTER: tuple[str, ...] = ("INTERMERCADO_SP500", "ABERTURA_E_GAP", "REGIME_DE_VOL")


def select(names: Sequence[str]) -> tuple[Family, ...]:
    by_name = {f.name: f for f in FAMILIES}
    missing = [n for n in names if n not in by_name]
    if missing:
        raise CatalogError(f"famílias inexistentes: {missing}")
    if len(set(names)) != len(names):
        raise CatalogError("família repetida")
    return tuple(by_name[n] for n in names)


def get(families: Sequence[Family], name: str) -> Family:
    for f in families:
        if f.name == name:
            return f
    raise CatalogError(f"família fora do catálogo: {name}")


def catalog_sha256(families: Sequence[Family]) -> str:
    """Hash canônico do catálogo que entra no ``config_hash`` do gênesis."""
    body = [
        {
            "name": f.name,
            "mechanism": f.mechanism,
            "typical_payer": f.typical_payer,
            "data_needed": sorted(f.data_needed),
            "horizon_range": list(f.horizon_range),
            "allowed_ops": sorted(op.name for op in f.allowed_ops),
            "forbidden_ops": sorted(op.name for op in f.forbidden_ops),
            "session_hint": list(f.session_hint) if f.session_hint else None,
            "available": f.available,
        }
        for f in families
    ]
    text = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return sha256(text.encode("utf-8")).hexdigest()
