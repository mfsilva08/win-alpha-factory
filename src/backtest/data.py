"""Dados de mercado: exportação do MT5 → ``MarketFrame`` (SPEC-fase-2 §2.2, dia zero).

O que mais dá errado no dia zero está aqui, e cada item é uma checagem:

- **Timestamp.** O MT5 grava a **abertura** da barra; o ``MarketFrame`` usa o
  **fechamento**. ``read_mt5_csv`` soma um minuto, sempre
- **Série contínua.** ``stitch_by_difference`` emenda os contratos ajustando por
  diferença no dia da rolagem, e marca ``roll_day``
- **Alinhamento WIN × ES.** ``alignment_lag`` mede em qual defasagem a correlação
  dos retornos é máxima. Precisa ser **0**: se o ES estiver deslocado um minuto
  para trás, o backtest "prevê" o WIN com dado do futuro e o Sharpe explode
- **Hold-out.** ``split_holdout`` separa os últimos meses. O hold-out é gravado em
  outro diretório e nunca é lido pelo carregador comum

Nada aqui usa LLM (R2) e nada aqui decide: as checagens reportam, quem decide é você.
"""

from __future__ import annotations

import csv
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, time
from hashlib import sha256
from pathlib import Path
from typing import Any

import numpy as np

from src.dsl.errors import MarketDataError
from src.dsl.market import FloatArray, MarketFrame, TimeArray

MINUTE = np.timedelta64(1, "m")
SESSION_DEFAULT = (time(9, 0), time(18, 0))
MAX_GAP_MINUTES = 3  # dia zero, bloco 1: nenhum buraco maior que isso dentro do pregão
MIN_ALIGNMENT_CORR = 0.05  # abaixo disso a defasagem de maior correlação é ruído


@dataclass(frozen=True)
class RawBars:
    """O que veio de um arquivo, já com ``ts`` no **fechamento** da barra."""

    ts: TimeArray
    open: FloatArray
    high: FloatArray
    low: FloatArray
    close: FloatArray
    tick_volume: FloatArray
    real_volume: FloatArray

    def __len__(self) -> int:
        return int(self.ts.shape[0])


_HEADER = re.compile(r"[<>\s]")


def _norm(name: str) -> str:
    return _HEADER.sub("", name).lower()


_ALIASES = {
    "date": "date", "time": "time", "datetime": "datetime", "open": "open", "high": "high",
    "low": "low", "close": "close", "tickvol": "tick_volume", "tickvolume": "tick_volume",
    "vol": "real_volume", "volume": "real_volume", "realvolume": "real_volume",
    "spread": "spread",
}


def read_mt5_csv(path: Path) -> RawBars:
    """Lê a exportação M1 do MT5 (CSV ou TSV) e **soma um minuto**: ts é o fechamento."""
    text = path.read_text(encoding="utf-8-sig")
    sample = text[:4096]
    delimiter = "\t" if sample.count("\t") >= sample.count(",") else ","
    rows = list(csv.reader(text.splitlines(), delimiter=delimiter))
    if not rows:
        raise MarketDataError(f"{path.name}: arquivo vazio")
    cols = [_ALIASES.get(_norm(c), _norm(c)) for c in rows[0]]
    if "close" not in cols:
        raise MarketDataError(f"{path.name}: cabeçalho sem coluna de fechamento")
    idx = {c: i for i, c in enumerate(cols)}
    ts_list: list[np.datetime64] = []
    values: dict[str, list[float]] = {k: [] for k in
                                      ("open", "high", "low", "close", "tick_volume",
                                       "real_volume")}
    for line, row in enumerate(rows[1:], start=2):
        if not row or all(not c.strip() for c in row):
            continue
        try:
            if "datetime" in idx:
                stamp = row[idx["datetime"]].strip()
            else:
                stamp = f"{row[idx['date']].strip()} {row[idx['time']].strip()}"
            stamp = stamp.replace(".", "-").replace("/", "-")
            ts_list.append(np.datetime64(stamp.replace(" ", "T"), "m"))
            for key, column in values.items():
                column.append(float(row[idx[key]]) if key in idx else 0.0)
        except (KeyError, IndexError, ValueError) as e:
            raise MarketDataError(f"{path.name}: linha {line}: {e}") from None
    if not ts_list:
        raise MarketDataError(f"{path.name}: nenhuma barra")
    opens = np.array(ts_list, dtype="datetime64[m]")
    ts: TimeArray = (opens + MINUTE).astype("datetime64[m]")  # abertura -> fechamento
    arrays = {k: np.array(v, dtype=np.float64) for k, v in values.items()}
    order = np.argsort(ts, kind="stable")
    return RawBars(ts=ts[order], **{k: v[order] for k, v in arrays.items()})


# ---------------------------------------------------------------- série contínua


def stitch_by_difference(parts: Sequence[RawBars]) -> tuple[RawBars, set[date]]:
    """Emenda contratos em ordem cronológica, ajustando por diferença na rolagem.

    Cada parte é um contrato. A diferença é medida na **última barra em comum**
    entre o contrato que vence e o próximo; ela é somada a todo o histórico
    anterior, de modo que o trecho mais recente fica com os preços reais.
    Devolve a série e os dias de rolagem.
    """
    if not parts:
        raise MarketDataError("nenhum contrato para emendar")
    if len(parts) == 1:
        return parts[0], set()
    rolls: set[date] = set()
    ts = parts[-1].ts
    fields = {k: getattr(parts[-1], k).copy() for k in
              ("open", "high", "low", "close", "tick_volume", "real_volume")}
    for part in reversed(parts[:-1]):
        common = np.intersect1d(part.ts, ts)
        if common.size == 0:
            raise MarketDataError("contratos sem barra em comum: rolagem não pode ser ajustada")
        roll_at = common[-1]
        diff = float(fields["close"][np.searchsorted(ts, roll_at)]
                     - part.close[np.searchsorted(part.ts, roll_at)])
        keep_old = part.ts < roll_at
        keep_new = ts >= roll_at   # sem repetir o período de sobreposição
        rolls.add(roll_at.astype("datetime64[D]").astype(date))
        for k in ("open", "high", "low", "close"):
            fields[k] = np.concatenate([getattr(part, k)[keep_old] + diff, fields[k][keep_new]])
        for k in ("tick_volume", "real_volume"):
            fields[k] = np.concatenate([getattr(part, k)[keep_old], fields[k][keep_new]])
        ts = np.concatenate([part.ts[keep_old], ts[keep_new]])
    return RawBars(ts=ts, **fields), rolls


# ---------------------------------------------------------------- montagem do frame


def _session_mask(ts: TimeArray, session: tuple[time, time]) -> np.ndarray:
    tod = ts.astype("datetime64[m]").astype(np.int64) % 1440
    lo = session[0].hour * 60 + session[0].minute
    hi = session[1].hour * 60 + session[1].minute
    return (tod > lo) & (tod <= hi)


def build_frame(win: RawBars, refs: Mapping[str, RawBars], *,
                session: tuple[time, time] = SESSION_DEFAULT,
                roll_days: set[date] | None = None) -> tuple[MarketFrame, int]:
    """Junta WIN e referências pelo **fechamento** da barra.

    Barra do WIN sem a barra correspondente da referência é descartada — o robô
    também não opera nesse minuto (``DATA_GAP``). Devolve o frame e quantas
    barras foram descartadas.
    """
    keep = np.ones(len(win), dtype=bool)
    ref_values: dict[str, FloatArray] = {}
    for sym, bars in refs.items():
        pos = np.searchsorted(bars.ts, win.ts)
        pos_clipped = np.clip(pos, 0, len(bars) - 1)
        hit = bars.ts[pos_clipped] == win.ts
        keep &= hit
        ref_values[sym] = bars.close[pos_clipped]
    dropped = int((~keep).sum())
    ts = win.ts[keep]
    days = ts.astype("datetime64[D]").astype(date)
    roll = np.array([d in (roll_days or set()) for d in days], dtype=bool)
    frame = MarketFrame(
        ts=ts, close=win.close[keep], high=win.high[keep], low=win.low[keep],
        volume=win.real_volume[keep], trades=win.tick_volume[keep],
        vwap=(win.high[keep] + win.low[keep] + win.close[keep]) / 3.0,
        refs={sym: vals[keep] for sym, vals in ref_values.items()},
        session_mask=_session_mask(ts, session), roll_day=roll,
    )
    return frame, dropped


# ---------------------------------------------------------------- checagens


@dataclass(frozen=True)
class DataReport:
    bars: int
    days: int
    first_day: date
    last_day: date
    dropped_unaligned: int
    gaps: list[tuple[str, int]] = field(default_factory=list)  # (fechamento, minutos sem barra)
    zero_volume: int = 0
    alignment: dict[str, tuple[int, float]] = field(default_factory=dict)
    bars_per_day: dict[date, int] = field(default_factory=dict)
    duplicated: int = 0

    @property
    def ok(self) -> bool:
        return (not self.gaps and self.duplicated == 0
                and all(lag == 0 or abs(corr) < MIN_ALIGNMENT_CORR
                        for lag, corr in self.alignment.values()))

    def text(self) -> str:
        lines = [
            (f"barras: {self.bars} · pregões: {self.days} "
             f"({self.first_day} a {self.last_day})"),
            f"barras descartadas por falta de referência: {self.dropped_unaligned}",
            f"barras com volume zero: {self.zero_volume}",
            f"timestamps repetidos: {self.duplicated}",
        ]
        for sym, (lag, corr) in sorted(self.alignment.items()):
            if abs(corr) < MIN_ALIGNMENT_CORR:
                status = "correlação fraca: alinhamento não verificável por aqui"
            else:
                status = "ok" if lag == 0 else "ATENÇÃO: timestamp deslocado"
            lines.append(f"alinhamento WIN x {sym}: defasagem de maior correlação = {lag} "
                         f"(corr {corr:.3f}) [{status}]")
        if self.gaps:
            lines.append(f"buracos dentro do pregão (> {MAX_GAP_MINUTES} min): {len(self.gaps)}")
            lines += [f"  {when}: {minutes} min sem barra" for when, minutes in self.gaps[:10]]
            if len(self.gaps) > 10:
                lines.append(f"  ... e mais {len(self.gaps) - 10}")
        else:
            lines.append("buracos dentro do pregão: nenhum")
        return "\n".join(lines)


def alignment_lag(frame: MarketFrame, symbol: str, max_lag: int = 3) -> tuple[int, float]:
    """Defasagem (em barras) de maior correlação entre os retornos do WIN e da referência.

    Devolve ``(defasagem, correlação)``. 0 é o esperado. Valor diferente de 0 com
    correlação relevante indica timestamp deslocado — e, se for negativo, o
    backtest estaria olhando o futuro. Com correlação fraca (abaixo de
    ``MIN_ALIGNMENT_CORR``) a defasagem é ruído e não permite concluir nada: é o
    caso de uma referência que simplesmente não se move junto com o WIN.
    """
    if symbol not in frame.refs:
        raise MarketDataError(f"frame sem a referência {symbol}")
    win = np.diff(np.log(frame.close))
    ref = np.diff(np.log(frame.refs[symbol]))
    best_lag, best_corr = 0, -np.inf
    for lag in range(-max_lag, max_lag + 1):
        a = win[max(lag, 0):len(win) + min(lag, 0)]
        b = ref[max(-lag, 0):len(ref) + min(-lag, 0)]
        if a.size < 100 or np.std(a) == 0 or np.std(b) == 0:
            continue
        corr = float(np.corrcoef(a, b)[0, 1])
        if corr > best_corr:
            best_lag, best_corr = lag, corr
    return best_lag, (0.0 if best_corr == -np.inf else best_corr)


def check(frame: MarketFrame, dropped: int = 0) -> DataReport:
    ts = frame.ts.astype("datetime64[m]")
    minutes = ts.astype(np.int64)
    days = ts.astype("datetime64[D]").astype(date)
    per_day: dict[date, int] = {}
    for d in days:
        per_day[d] = per_day.get(d, 0) + 1
    gaps: list[tuple[str, int]] = []
    same_day = days[1:] == days[:-1]
    delta = np.diff(minutes)
    in_session = frame.session_mask[1:] & frame.session_mask[:-1]
    for i in np.nonzero(same_day & in_session & (delta > MAX_GAP_MINUTES))[0]:
        gaps.append((str(ts[i]), int(delta[i])))
    return DataReport(
        bars=len(frame), days=len(per_day), first_day=min(per_day), last_day=max(per_day),
        dropped_unaligned=dropped, gaps=gaps,
        zero_volume=int((frame.volume <= 0).sum()),
        alignment={sym: alignment_lag(frame, sym) for sym in frame.refs},
        bars_per_day=per_day,
        duplicated=int((delta <= 0).sum()),
    )


# ---------------------------------------------------------------- hold-out, hash, calendário


def split_holdout(frame: MarketFrame, months: int = 6) -> tuple[MarketFrame, MarketFrame]:
    """(pesquisa, hold-out). O hold-out são os últimos ``months`` meses do dataset."""
    if months < 1:
        raise MarketDataError("hold-out precisa de ao menos um mês")
    last = frame.ts[-1].astype("datetime64[M]")
    cut = (last - np.timedelta64(months - 1, "M")).astype("datetime64[m]")
    i = int(np.searchsorted(frame.ts.astype("datetime64[m]"), cut))
    if i == 0 or i >= len(frame):
        raise MarketDataError("período curto demais para separar o hold-out")
    return slice_frame(frame, 0, i), slice_frame(frame, i, len(frame))


def slice_frame(frame: MarketFrame, start: int, stop: int) -> MarketFrame:
    return MarketFrame(
        ts=frame.ts[start:stop], close=frame.close[start:stop], high=frame.high[start:stop],
        low=frame.low[start:stop], volume=frame.volume[start:stop],
        trades=frame.trades[start:stop], vwap=frame.vwap[start:stop],
        refs={k: v[start:stop] for k, v in frame.refs.items()},
        session_mask=frame.session_mask[start:stop], roll_day=frame.roll_day[start:stop],
    )


def data_hash(frame: MarketFrame) -> str:
    """sha256 do conteúdo do frame — é o ``data_hash`` do gênesis."""
    h = sha256()
    h.update(f"bars={len(frame)}".encode())
    h.update(frame.ts.astype("datetime64[m]").astype(np.int64).tobytes())
    for name in ("close", "high", "low", "volume", "trades", "vwap"):
        h.update(name.encode())
        h.update(np.ascontiguousarray(getattr(frame, name), dtype=np.float64).tobytes())
    for sym in sorted(frame.refs):
        h.update(sym.encode())
        h.update(np.ascontiguousarray(frame.refs[sym], dtype=np.float64).tobytes())
    h.update(np.packbits(frame.session_mask).tobytes())
    h.update(np.packbits(frame.roll_day).tobytes())
    return h.hexdigest()


def save_frame(frame: MarketFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    arrays: dict[str, np.ndarray[tuple[int, ...], np.dtype[np.generic]]] = {
        "ts": frame.ts.astype("datetime64[m]").astype(np.int64),
        "close": frame.close, "high": frame.high, "low": frame.low, "volume": frame.volume,
        "trades": frame.trades, "vwap": frame.vwap, "session_mask": frame.session_mask,
        "roll_day": frame.roll_day, "ref_symbols": np.array(sorted(frame.refs)),
        **{f"ref_{sym}": arr for sym, arr in frame.refs.items()},
    }
    savez: Any = np.savez_compressed  # o stub do numpy casa **kwargs com allow_pickle
    savez(path, **arrays)


def load_saved_frame(path: Path) -> MarketFrame:
    with np.load(path, allow_pickle=False) as z:
        symbols = [str(s) for s in z["ref_symbols"]]
        return MarketFrame(
            ts=z["ts"].astype("datetime64[m]"), close=z["close"], high=z["high"], low=z["low"],
            volume=z["volume"], trades=z["trades"], vwap=z["vwap"],
            refs={sym: z[f"ref_{sym}"] for sym in symbols},
            session_mask=z["session_mask"], roll_day=z["roll_day"],
        )


def write_calendar(report: DataReport, path: Path) -> Path:
    """``day,bars_expected`` — o calendário que o coletor usa para conferir o dia."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["day", "bars_expected"])
        for day, bars in sorted(report.bars_per_day.items()):
            w.writerow([day.isoformat(), bars])
    return path
