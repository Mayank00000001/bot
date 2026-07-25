"""
Tests for Round 5 — wick-to-wick zone + no blow-through taps (SPEC.md AC23–AC25).

Run from the repo root:  python -m pytest tests/ -v
"""

from __future__ import annotations

from typing import List, Tuple

import pandas as pd
import pytest

from ob_detector import OrderBlock, OrderBlockDetector


def _frame(rows: List[Tuple[float, float, float, float]]) -> pd.DataFrame:
    return pd.DataFrame([
        {"open_time": f"2026-07-01T{i:02d}:00:00", "open": o, "high": h, "low": lo,
         "close": c, "volume": 100}
        for i, (o, h, lo, c) in enumerate(rows)
    ])


def _bull_ob_frame() -> pd.DataFrame:
    """Bearish origin candle at idx 12 (open=105 high=105.5 low=103 close=104),
    then a strong bullish displacement, then continuation above the zone."""
    rows: List[Tuple[float, float, float, float]] = []
    for i in range(12):                       # flat baseline, body ~1
        o, c = (100.0, 101.0) if i % 2 == 0 else (101.0, 100.0)
        rows.append((o, max(o, c) + 0.2, min(o, c) - 0.2, c))
    rows.append((105.0, 105.5, 103.0, 104.0))  # 12: bearish OB origin
    rows.append((104.0, 110.5, 103.8, 110.0))  # 13: strong bullish displacement
    rows.append((110.0, 116.5, 109.8, 116.0))  # 14: strong bullish displacement
    for base in [117.0, 118.0, 117.5, 118.5, 119.0]:   # 15-19: continuation
        rows.append((base - 0.5, base + 0.5, base - 1.0, base))
    return _frame(rows)


def _zone(low: float, high: float) -> OrderBlock:
    return OrderBlock(
        ob_id="X_1h_bull_T", symbol="XAU/USD", htf="1h", direction="bullish",
        ob_high=high, ob_low=low, wick_high=high, wick_low=low, candle_time="t",
    )


# --------------------------------------------------------------------------- #
# AC23 — zone is the candle's full range (wick to wick), not the body
# --------------------------------------------------------------------------- #

def test_ac23_zone_is_wick_to_wick():
    det = OrderBlockDetector("XAU/USD", "1h", store=None)
    new = det.update(_bull_ob_frame())
    bull = next(ob for ob in new if ob.direction == "bullish")
    # OB candle: open=105 high=105.5 low=103 close=104
    assert bull.ob_low == pytest.approx(103.0)    # candle low (not body 104)
    assert bull.ob_high == pytest.approx(105.5)   # candle high (not body 105)


# --------------------------------------------------------------------------- #
# AC24 — a candle that blows through the zone is not a tap
# --------------------------------------------------------------------------- #

def test_ac24_blowthrough_is_not_a_tap():
    ob = _zone(104.0, 105.0)
    assert ob.contains_range(100.0, 110.0) is False   # engulfs the whole zone
    assert ob.contains_range(104.5, 106.0) is True    # wick into the zone
    assert ob.contains_range(103.5, 104.8) is True    # wick into the zone from below
    assert ob.contains_range(106.0, 108.0) is False   # no overlap at all


def test_ac24_edge_touch_still_taps():
    ob = _zone(104.0, 105.0)
    # A candle whose high exactly reaches the zone low is a touch, not a blow-through.
    assert ob.contains_range(103.0, 104.0) is True


# --------------------------------------------------------------------------- #
# AC25 — price tap works on the full-range zone
# --------------------------------------------------------------------------- #

def test_ac25_contains_price_full_range():
    ob = _zone(103.0, 105.5)
    assert ob.contains_price(104.0) is True
    assert ob.contains_price(105.5) is True    # inclusive at the edge
    assert ob.contains_price(102.0) is False
