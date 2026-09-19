"""SPEC-fase-2 §2.2 e dia zero: leitura do MT5, série contínua, checagens e hold-out."""

from __future__ import annotations

import csv
from datetime import date, time
from pathlib import Path

import numpy as np
import pytest

from src import cli
from src.backtest import data as md
from src.dsl.errors import MarketDataError
from src.dsl.market import MarketFrame
from tests.dsl_factory import BARS_PER_DAY, synthetic_frame

MT5_HEADER = ["<DATE>", "<TIME>", "<OPEN>", "<HIGH>", "<LOW>", "<CLOSE>", "<TICKVOL>",
              "<VOL>", "<SPREAD>"]


def write_mt5(path: Path, frame: MarketFrame, *, series: str = "win",
              shift_minutes: int = 0) -> Path:
    """Escreve no formato do MT5: timestamp de **abertura**, tab-separado."""
    closes = {"win": frame.close, "es": frame.refs["ES"]}[series]
    opens = frame.ts.astype("datetime64[m]") - np.timedelta64(1 - shift_minutes, "m")
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow(MT5_HEADER)
        for i, ts in enumerate(opens):
            stamp = str(ts).replace("-", ".").split("T")
            c = float(closes[i])
            w.writerow([stamp[0], stamp[1] + ":00", f"{c:.2f}", f"{c + 5:.2f}",
                        f"{c - 5:.2f}", f"{c:.2f}", int(frame.trades[i]),
                        int(frame.volume[i]), 1])
    return path


@pytest.fixture(scope="module")
def frame() -> MarketFrame:
    return synthetic_frame(n_days=8, roll_day_index=None)


@pytest.fixture(scope="module")
def files(tmp_path_factory: pytest.TempPathFactory, frame: MarketFrame) -> dict[str, Path]:
    d = tmp_path_factory.mktemp("mt5")
    return {"win": write_mt5(d / "WIN.csv", frame),
            "es": write_mt5(d / "ES.csv", frame, series="es")}


# ---------------------------------------------------------------- leitura


def test_le_mt5_e_soma_um_minuto(files: dict[str, Path], frame: MarketFrame) -> None:
    raw = md.read_mt5_csv(files["win"])
    assert len(raw) == len(frame)
    np.testing.assert_array_equal(raw.ts, frame.ts.astype("datetime64[m]"))
    np.testing.assert_allclose(raw.close, frame.close)
    assert raw.tick_volume[0] == frame.trades[0]  # trades <- TICKVOL
    assert raw.real_volume[0] == frame.volume[0]  # volume <- VOL


def test_le_csv_com_virgula_e_coluna_datetime(tmp_path: Path) -> None:
    p = tmp_path / "x.csv"
    p.write_text("datetime,open,high,low,close,volume\n"
                 "2026-09-15 10:00,100,101,99,100.5,7\n"
                 "2026-09-15 10:01,100.5,102,100,101,9\n", encoding="utf-8")
    raw = md.read_mt5_csv(p)
    assert str(raw.ts[0]) == "2026-09-15T10:01"  # fechamento
    assert raw.close[-1] == 101.0 and raw.real_volume[-1] == 9.0


@pytest.mark.parametrize("content", ["", "a,b\n1,2\n", "datetime,close\n"])
def test_arquivo_invalido(tmp_path: Path, content: str) -> None:
    p = tmp_path / "bad.csv"
    p.write_text(content, encoding="utf-8")
    with pytest.raises(MarketDataError):
        md.read_mt5_csv(p)


# ---------------------------------------------------------------- série contínua


def test_emenda_ajusta_por_diferenca_e_marca_rolagem(tmp_path: Path,
                                                     frame: MarketFrame) -> None:
    n = len(frame)
    cut = 4 * BARS_PER_DAY
    old = md.read_mt5_csv(write_mt5(tmp_path / "a.csv", _slice(frame, 0, cut + 60)))
    novo_frame = _slice(frame, cut, n)
    novo = md.read_mt5_csv(write_mt5(tmp_path / "b.csv", _bump(novo_frame, 1500.0)))
    joined, rolls = md.stitch_by_difference([old, novo])
    assert len(rolls) == 1
    assert len(joined) == n
    # o salto artificial de 1500 pontos sumiu: nenhuma variação de barra absurda
    steps = np.abs(np.diff(joined.close))
    assert steps.max() < 500.0
    # o trecho recente ficou com os preços reais do contrato novo
    np.testing.assert_allclose(joined.close[-10:], novo.close[-10:])


def test_emenda_exige_barra_em_comum(tmp_path: Path, frame: MarketFrame) -> None:
    a = md.read_mt5_csv(write_mt5(tmp_path / "a.csv", _slice(frame, 0, 500)))
    b = md.read_mt5_csv(write_mt5(tmp_path / "b.csv", _slice(frame, 600, 1200)))
    with pytest.raises(MarketDataError, match="comum"):
        md.stitch_by_difference([a, b])


def _slice(f: MarketFrame, a: int, b: int) -> MarketFrame:
    return md.slice_frame(f, a, b)


def _bump(f: MarketFrame, delta: float) -> MarketFrame:
    return MarketFrame(ts=f.ts, close=f.close + delta, high=f.high + delta, low=f.low + delta,
                       volume=f.volume, trades=f.trades, vwap=f.vwap + delta,
                       refs=dict(f.refs), session_mask=f.session_mask, roll_day=f.roll_day)


# ---------------------------------------------------------------- montagem e checagens


def test_monta_frame_e_descarta_barra_sem_referencia(files: dict[str, Path],
                                                     frame: MarketFrame) -> None:
    win = md.read_mt5_csv(files["win"])
    es = md.read_mt5_csv(files["es"])
    faltando = md.RawBars(ts=es.ts[:-30], open=es.open[:-30], high=es.high[:-30],
                          low=es.low[:-30], close=es.close[:-30],
                          tick_volume=es.tick_volume[:-30], real_volume=es.real_volume[:-30])
    built, dropped = md.build_frame(win, {"ES": faltando}, session=(time(9, 0), time(18, 0)))
    assert dropped == 30 and len(built) == len(frame) - 30
    np.testing.assert_allclose(built.vwap, (built.high + built.low + built.close) / 3.0)
    assert built.session_mask.all()  # o frame sintético inteiro está na sessão


def test_alinhamento_detecta_es_deslocado(tmp_path: Path, frame: MarketFrame) -> None:
    win = md.read_mt5_csv(write_mt5(tmp_path / "w.csv", frame))
    ok = md.read_mt5_csv(write_mt5(tmp_path / "e.csv", frame, series="es"))
    aligned, _ = md.build_frame(win, {"ES": ok})
    lag, corr = md.alignment_lag(aligned, "ES")
    assert lag == 0 and corr > 0.2
    torto = md.read_mt5_csv(write_mt5(tmp_path / "e2.csv", frame, series="es",
                                      shift_minutes=1))
    skewed, _ = md.build_frame(win, {"ES": torto})
    bad_lag, bad_corr = md.alignment_lag(skewed, "ES")
    assert bad_lag != 0 and bad_corr > 0.2
    assert not md.check(skewed).ok
    assert "ATENÇÃO" in md.check(skewed).text()


def test_checagens_reportam_buraco_e_volume_zero(frame: MarketFrame) -> None:
    report = md.check(frame)
    assert report.ok and report.gaps == [] and report.days == 8
    # o WDO sintético não anda junto com o WIN: correlação fraca não reprova o frame
    assert abs(report.alignment["WDO"][1]) < md.MIN_ALIGNMENT_CORR
    assert "não verificável" in report.text()
    assert sum(report.bars_per_day.values()) == len(frame)
    assert report.bars_per_day[report.first_day] == BARS_PER_DAY
    keep = np.ones(len(frame), dtype=bool)
    keep[100:110] = False  # dez minutos sem barra dentro do pregão
    holed = MarketFrame(ts=frame.ts[keep], close=frame.close[keep], high=frame.high[keep],
                        low=frame.low[keep], volume=np.zeros(int(keep.sum())),
                        trades=frame.trades[keep], vwap=frame.vwap[keep],
                        refs={k: v[keep] for k, v in frame.refs.items()},
                        session_mask=frame.session_mask[keep], roll_day=frame.roll_day[keep])
    r2 = md.check(holed, dropped=3)
    assert not r2.ok and len(r2.gaps) == 1 and r2.gaps[0][1] == 11
    assert r2.zero_volume == len(holed) and r2.dropped_unaligned == 3
    assert "buracos dentro do pregão" in r2.text()


# ---------------------------------------------------------------- hold-out e hash


def test_holdout_separa_os_ultimos_meses() -> None:
    f = synthetic_frame(n_days=63, roll_day_index=None)  # ~3 meses
    research, holdout = md.split_holdout(f, months=1)
    assert len(research) + len(holdout) == len(f)
    assert research.ts[-1] < holdout.ts[0]
    assert holdout.ts[0].astype("datetime64[M]") == f.ts[-1].astype("datetime64[M]")
    with pytest.raises(MarketDataError):
        md.split_holdout(f, months=99)


def test_data_hash_muda_com_qualquer_barra(frame: MarketFrame) -> None:
    h = md.data_hash(frame)
    assert h == md.data_hash(frame) and len(h) == 64
    changed = MarketFrame(ts=frame.ts, close=frame.close + np.eye(1, len(frame), 7)[0] * 5,
                          high=frame.high, low=frame.low, volume=frame.volume,
                          trades=frame.trades, vwap=frame.vwap, refs=dict(frame.refs),
                          session_mask=frame.session_mask, roll_day=frame.roll_day)
    assert md.data_hash(changed) != h
    research, holdout = md.split_holdout(synthetic_frame(n_days=63, roll_day_index=None), 1)
    assert md.data_hash(research) != md.data_hash(holdout)


def test_salva_e_recarrega(tmp_path: Path, frame: MarketFrame) -> None:
    p = tmp_path / "f.npz"
    md.save_frame(frame, p)
    back = md.load_saved_frame(p)
    assert md.data_hash(back) == md.data_hash(frame)
    np.testing.assert_array_equal(back.refs["ES"], frame.refs["ES"])
    np.testing.assert_array_equal(back.roll_day, frame.roll_day)


def test_calendario(tmp_path: Path, frame: MarketFrame) -> None:
    p = md.write_calendar(md.check(frame), tmp_path / "cal.csv")
    from src.ops.coletor import load_calendar
    cal = load_calendar(p)
    assert len(cal) == 8 and set(cal.values()) == {BARS_PER_DAY}
    assert min(cal) == date.fromisoformat(str(frame.ts[0])[:10])


# ---------------------------------------------------------------- CLI


def test_cli_data_prepara_e_separa_holdout(tmp_path: Path,
                                           capsys: pytest.CaptureFixture[str]) -> None:
    f = synthetic_frame(n_days=63, roll_day_index=None)
    win = write_mt5(tmp_path / "WIN.csv", f)
    es = write_mt5(tmp_path / "ES.csv", f, series="es")
    out = tmp_path / "data" / "frame.npz"
    holdout = tmp_path / "holdout"
    code = cli.main(["data", "--win", str(win), "--es", str(es), "--out", str(out),
                     "--holdout-out", str(holdout), "--holdout-months", "1",
                     "--calendar-out", str(tmp_path / "cal.csv")])
    text = capsys.readouterr().out
    assert code == 0
    assert out.exists() and (holdout / "holdout.npz").exists()
    assert (tmp_path / "cal.csv").exists()
    research = md.load_saved_frame(out)
    held = md.load_saved_frame(holdout / "holdout.npz")
    assert md.data_hash(research) in text and md.data_hash(held) in text
    assert research.ts[-1] < held.ts[0]
    assert "defasagem de maior correlação = 0" in text


def test_cli_data_recusa_dados_ruins(tmp_path: Path,
                                     capsys: pytest.CaptureFixture[str]) -> None:
    f = synthetic_frame(n_days=3, roll_day_index=None)
    win = write_mt5(tmp_path / "WIN.csv", f)
    es = write_mt5(tmp_path / "ES.csv", f, series="es", shift_minutes=1)
    assert cli.main(["data", "--win", str(win), "--es", str(es),
                     "--out", str(tmp_path / "f.npz")]) == 1
    assert not (tmp_path / "f.npz").exists()
    assert cli.main(["data", "--win", str(win), "--es", str(es), "--force",
                     "--out", str(tmp_path / "f.npz")]) == 0
    assert (tmp_path / "f.npz").exists()
    assert "ATENÇÃO" in capsys.readouterr().out
