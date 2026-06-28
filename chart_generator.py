"""chart_generator.py — Signal chart screenshot banata hai."""

from __future__ import annotations
import os, time
from pathlib import Path
from typing import Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import mplfinance as mpf
import pandas as pd

from ltf_confirmation import Signal
from logger import get_logger
log = get_logger(__name__)

BG=("#131722"); PANEL=("#1e2130"); BULL=("#26a69a"); BEAR=("#ef5350")
OB_B=("#26a69a"); OB_R=("#ef5350"); FVG=("#f9a825")
EC=("#42a5f5"); SC=("#ef5350"); T1=("#66bb6a"); T2=("#00e676"); MC=("#ce93d8"); TC=("#d1d4dc")


def generate_chart(df_ltf: pd.DataFrame, signal: Signal, output_dir: str = "charts") -> Optional[str]:
    try:
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        return _render(df_ltf, signal, output_dir)
    except Exception as e:
        log.error(f"Chart fail: {e}")
        return None


def _render(df: pd.DataFrame, sig: Signal, out: str) -> str:
    df = df.copy().tail(100)
    if "open_time" in df.columns:
        df = df.set_index("open_time")
    df.index = pd.DatetimeIndex(df.index)
    df = df[["open","high","low","close","volume"]]

    mc = mpf.make_marketcolors(up=BULL,down=BEAR,edge="inherit",wick={"up":BULL,"down":BEAR},volume={"up":BULL,"down":BEAR})
    style = mpf.make_mpf_style(base_mpf_style="nightclouds",marketcolors=mc,facecolor=BG,edgecolor=PANEL,
        figcolor=BG,gridcolor="#2a2e3e",gridstyle="--",gridaxis="both",rc={"font.family":"monospace","text.color":TC})

    hlines = dict(
        hlines=[sig.entry_price, sig.sl_price, sig.tp1, sig.tp2, sig.mss_level],
        colors=[EC, SC, T1, T2, MC],
        linestyle=["--","-","-","-",":"],
        linewidths=[1.2,1.0,0.9,0.9,0.8],
    )
    fig, axes = mpf.plot(df,type="candle",style=style,volume=True,hlines=hlines,
        returnfig=True,figsize=(13,7),tight_layout=True,panel_ratios=(4,1),
        datetime_format="%m-%d %H:%M",xrotation=20)
    ax = axes[0]

    ob_col = OB_B if sig.direction == "long" else OB_R
    ax.axhspan(sig.ob.ob_low, sig.ob.ob_high, alpha=0.15, color=ob_col)
    if sig.fvg_high and sig.fvg_low:
        ax.axhspan(sig.fvg_low, sig.fvg_high, alpha=0.12, color=FVG)

    x = len(df) - 1
    for price, label, color in [
        (sig.entry_price,"ENTRY",EC),(sig.sl_price,"SL",SC),
        (sig.tp1,"TP1",T1),(sig.tp2,"TP2",T2),(sig.mss_level,"MSS",MC)
    ]:
        ax.annotate(f"  {label}: {price:.5f}", xy=(x,price), xytext=(x+0.5,price),
            color=color, fontsize=7.5, va="center", fontfamily="monospace", annotation_clip=False)

    arrow = "▲ LONG" if sig.direction == "long" else "▼ SHORT"
    ax.set_title(f"{sig.symbol} | {arrow} | {sig.cascade_label}", color=TC, fontsize=11, pad=8)
    ax.legend(handles=[
        mpatches.Patch(color=ob_col, alpha=0.4, label=f"HTF OB ({sig.htf})"),
        mpatches.Patch(color=FVG, alpha=0.4, label="FVG"),
    ], loc="upper left", facecolor=PANEL, edgecolor=PANEL, labelcolor=TC, fontsize=8)

    fname = f"{out}/{sig.symbol.replace('/','_')}_{sig.ltf}_{int(time.time())}.png"
    fig.savefig(fname, dpi=120, bbox_inches="tight", facecolor=BG)
    plt.close(fig)
    return fname
