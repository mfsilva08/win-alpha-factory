"""Limiares do gate, lidos do pré-registro hasheado (R6, SPEC-fase-2 §2.7).

Sem override por variável de ambiente, sem parâmetro de função, sem exceção:
o único caminho para mudar um limiar é editar ``docs/gate-prereg.md``, o que muda
o sha256 e faz ``load`` recusar — outro projeto, outro gênesis.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from math import comb
from pathlib import Path

from src.gate.errors import PreregIncomplete, PreregInvalid, PreregMismatch
from src.ledger.ledger import Ledger, sha256_file

PREREG_PATH = Path(__file__).resolve().parents[2] / "docs" / "gate-prereg.md"
MARKER = "<<< DECIDIR >>>"

_FENCE = re.compile(r"^```[^\n]*\n(.*?)^```", re.MULTILINE | re.DOTALL)
_KEY_VALUE = re.compile(r"^([A-Za-z_][\w-]*)\s*:\s*(.*?)\s*(?:#.*)?$")


@dataclass(frozen=True)
class GateConfig:
    min_total_trades: int
    min_trades_per_path: int
    min_active_days: int
    max_trade_concentration: float
    max_sign_flip_frac: float
    max_pbo: float
    min_dsr: float
    max_trials: int
    n_groups: int
    k_test: int
    embargo_mult: float


def prereg_values(text: str) -> dict[str, str]:
    """Pares ``chave: valor`` dos blocos de código. O texto fora dos blocos é prosa."""
    values: dict[str, str] = {}
    for block in _FENCE.findall(text):
        for line in block.splitlines():
            m = _KEY_VALUE.match(line.strip())
            if m is None:
                continue
            key, value = m.group(1), m.group(2)
            if key in values:
                raise PreregInvalid(f"chave repetida no pré-registro: {key}")
            values[key] = value
    return values


def _markers_left(text: str) -> list[str]:
    """Marcadores dentro dos blocos de código. A prosa do cabeçalho cita o marcador."""
    left: list[str] = []
    for block in _FENCE.findall(text):
        for line in block.splitlines():
            if MARKER in line:
                left.append(line.split(":", 1)[0].strip())
    return left


def _int(v: dict[str, str], key: str) -> int:
    try:
        return int(v[key].replace(".", "").replace("_", ""))
    except KeyError:
        raise PreregIncomplete(f"campo ausente: {key}") from None
    except ValueError:
        raise PreregInvalid(f"{key}: esperado inteiro, veio {v[key]!r}") from None


def _float(v: dict[str, str], key: str) -> float:
    try:
        x = float(v[key].replace(",", "."))
    except KeyError:
        raise PreregIncomplete(f"campo ausente: {key}") from None
    except ValueError:
        raise PreregInvalid(f"{key}: esperado número, veio {v[key]!r}") from None
    if not math.isfinite(x):
        raise PreregInvalid(f"{key}: não finito")
    return x


def parse(text: str) -> GateConfig:
    left = _markers_left(text)
    if left:
        raise PreregIncomplete(f"campos ainda por decidir: {left}")
    v = prereg_values(text)
    cfg = GateConfig(
        min_total_trades=_int(v, "min_total_trades"),
        min_trades_per_path=_int(v, "min_trades_per_path"),
        min_active_days=_int(v, "min_active_days"),
        max_trade_concentration=_float(v, "max_trade_concentration"),
        max_sign_flip_frac=_float(v, "max_sign_flip_frac"),
        max_pbo=_float(v, "max_pbo"),
        min_dsr=_float(v, "min_dsr"),
        max_trials=_int(v, "MAX_TRIALS"),
        n_groups=_int(v, "n_grupos"),
        k_test=_int(v, "k_teste"),
        embargo_mult=_float(v, "embargo_mult"),
    )
    for name in ("max_trade_concentration", "max_sign_flip_frac", "max_pbo", "min_dsr"):
        x = getattr(cfg, name)
        if not 0.0 <= x <= 1.0:
            raise PreregInvalid(f"{name} fora de [0, 1]")
    for name in ("min_total_trades", "min_trades_per_path", "min_active_days", "max_trials"):
        if getattr(cfg, name) < 1:
            raise PreregInvalid(f"{name} deve ser >= 1")
    if not 1 <= cfg.k_test < cfg.n_groups:
        raise PreregInvalid("exige 1 <= k_teste < n_grupos")
    if cfg.embargo_mult < 0:
        raise PreregInvalid("embargo_mult negativo")
    caminhos = _int(v, "caminhos")
    if caminhos != comb(cfg.n_groups, cfg.k_test):
        raise PreregInvalid(f"caminhos ({caminhos}) != C(n_grupos, k_teste)")
    return cfg


def load(ledger: Ledger, path: Path = PREREG_PATH) -> GateConfig:
    """Lê o pré-registro, confere o sha256 dos bytes contra o gênesis e interpreta.

    Levanta ``PreregIncomplete`` se sobrar marcador, ``PreregMismatch`` se o
    arquivo não for o que o gênesis registrou.
    """
    text = path.read_text(encoding="utf-8")
    if _markers_left(text):
        parse(text)  # levanta PreregIncomplete com a lista
    if sha256_file(path) != ledger.genesis_info().prereg_sha256:
        raise PreregMismatch("gate-prereg.md difere do registrado no gênesis: outro projeto")
    return parse(text)
