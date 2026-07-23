"""
Tests for Round 3 — confirmation = displacement + MSS (FVG optional).

Run from the repo root:  python -m pytest tests/ -v
"""

from __future__ import annotations

from typing import List, Optional, Tuple

import pandas as pd

from ob_detector import OrderBlock
from ltf_confirmation import LTFConfirmationEngine, Signal


def bull_ob() -> OrderBlock:
    return OrderBlock(
        ob_id="XAU/USD_1h_bull_T", symbol="XAU/USD", htf="1h", direction="bullish",
        ob_high=105.0, ob_low=104.0, wick_high=105.5, wick_low=103.0, candle_time="t",
    )


def frame(rows: List[Tuple[float, float, float, float]]) -> pd.DataFrame:
    return pd.DataFrame([
        {"open_time": f"2026-07-01T{i:02d}:00:00", "open": o, "high": h, "low": lo,
         "close": c, "volume": 100}
        for i, (o, h, lo, c) in enumerate(rows)
    ])


def flat(n: int, base: float = 100.0) -> List[Tuple[float, float, float, float]]:
    """n small alternating candles (body 1) — no displacement, flat highs."""
    rows = []
    for i in range(n):
        o, c = (base, base + 1) if i % 2 == 0 else (base + 1, base)
        rows.append((o, max(o, c) + 0.2, min(o, c) - 0.2, c))
    return rows


STRONG_BULL = (102.0, 108.2, 101.8, 108.0)  # body 6, closes at 108


def engine() -> LTFConfirmationEngine:
    return LTFConfirmationEngine("XAU/USD", "1h", "5min", store=None)


# --------------------------------------------------------------------------- #
# AC15 — FVG is no longer required
# --------------------------------------------------------------------------- #

def test_ac15_signal_fires_on_displacement_and_mss_without_fvg():
    # Displacement is the LAST candle → _find_fvg has no candle after it → no FVG.
    df = frame(flat(24) + [STRONG_BULL])
    eng = engine()
    eng.add_watch(bull_ob())
    sigs = eng.process(df)
    assert len(sigs) == 1                       # before the fix: no signal (FVG gate)
    assert sigs[0].fvg_high == 0.0 and sigs[0].fvg_low == 0.0


# --------------------------------------------------------------------------- #
# AC16 — displacement still required
# --------------------------------------------------------------------------- #

def test_ac16_no_signal_without_displacement():
    df = frame(flat(24))                        # all bodies ~1, nothing strong
    eng = engine()
    eng.add_watch(bull_ob())
    assert eng.process(df) == []


# --------------------------------------------------------------------------- #
# AC17 — MSS still required
# --------------------------------------------------------------------------- #

def test_ac17_no_signal_when_mss_not_broken():
    # A pivot high at 110 sits above the displacement close (108) → MSS fails.
    rows = flat(20)
    rows.append((101.0, 110.0, 100.9, 101.3))   # candle 20: swing-high spike, tiny body
    rows += flat(3, base=101.0)                 # candles 21-23: small
    rows.append(STRONG_BULL)                    # candle 24: strong bull, close 108 < 110
    df = frame(rows)
    eng = engine()
    eng.add_watch(bull_ob())
    assert eng.process(df) == []


# --------------------------------------------------------------------------- #
# AC18 — FVG recorded as confluence when present + shown only when present
# --------------------------------------------------------------------------- #

def test_ac18_fvg_recorded_when_present(monkeypatch):
    df = frame(flat(24) + [STRONG_BULL])        # displacement + MSS
    eng = engine()
    # Force an FVG at the displacement → it must be captured onto the signal.
    monkeypatch.setattr(eng, "_find_fvg", lambda *a, **k: (103.0, 101.5))
    eng.add_watch(bull_ob())
    sigs = eng.process(df)
    assert len(sigs) == 1
    assert sigs[0].fvg_high == 103.0 and sigs[0].fvg_low == 101.5


def test_find_fvg_bullish_still_detects_gap():
    eng = engine()
    # bullish FVG: high[disp-1] < low[disp+1]
    df = frame([(99.0, 100.0, 98.0, 99.5), (100.0, 106.0, 99.0, 105.0), (104.0, 105.0, 102.0, 104.5)])
    assert eng._find_fvg(df, 1, "bullish") == (102.0, 100.0)


# --- Telegram: FVG line shown only when present ---------------------------- #

class _Resp:
    def json(self):
        return {"ok": True}
    text = ""


class _CapturePost:
    def __init__(self):
        self.payload: Optional[dict] = None

    def __call__(self, url, json=None, timeout=None):
        self.payload = json
        return _Resp()


def _sig(fvg_high: float, fvg_low: float) -> Signal:
    return Signal(
        symbol="XAU/USD", direction="long", htf="1h", ltf="5min",
        cascade_label="1h OB → 5min MSS", ob=bull_ob(),
        entry_price=108.0, sl_price=103.0, tp1=113.0, tp2=118.0,
        fvg_high=fvg_high, fvg_low=fvg_low, mss_level=101.2,
    )


def test_ac18_signal_shows_fvg_line_only_when_present(monkeypatch):
    import telegram_notifier
    tg = telegram_notifier.TelegramNotifier("token", "chat")

    cap = _CapturePost()
    monkeypatch.setattr(telegram_notifier.requests, "post", cap)
    tg.send_signal(_sig(103.0, 101.5))          # FVG present
    assert cap.payload is not None and "FVG" in cap.payload["text"]

    cap2 = _CapturePost()
    monkeypatch.setattr(telegram_notifier.requests, "post", cap2)
    tg.send_signal(_sig(0.0, 0.0))              # no FVG
    assert cap2.payload is not None and "FVG" not in cap2.payload["text"]
