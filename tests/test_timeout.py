"""
Tests for Round 4 — watch window = N lower-timeframe candles (SPEC.md AC19–AC22).

Run from the repo root:  python -m pytest tests/ -v
"""

from __future__ import annotations

import time

from ob_detector import OrderBlock
from ltf_confirmation import DEFAULT_TIMEOUT_CANDLES, LTFConfirmationEngine, PendingWatch


def engine(ltf: str, candles: int = DEFAULT_TIMEOUT_CANDLES) -> LTFConfirmationEngine:
    return LTFConfirmationEngine("XAU/USD", "1h", ltf, timeout_candles=candles, store=None)


def bull_ob() -> OrderBlock:
    return OrderBlock(
        ob_id="XAU/USD_1h_bull_T", symbol="XAU/USD", htf="1h", direction="bullish",
        ob_high=105.0, ob_low=104.0, wick_high=105.5, wick_low=103.0, candle_time="t",
    )


# --------------------------------------------------------------------------- #
# AC19 — window scales with the LTF (3 candles by default)
# --------------------------------------------------------------------------- #

def test_ac19_timeout_scales_with_ltf():
    assert engine("5min").timeout_seconds == 3 * 5 * 60      # 15 min
    assert engine("15min").timeout_seconds == 3 * 15 * 60    # 45 min
    assert engine("30min").timeout_seconds == 3 * 30 * 60    # 90 min


def test_ac19_default_is_three_candles():
    assert DEFAULT_TIMEOUT_CANDLES == 3
    assert engine("5min").timeout_candles == 3


def test_ac19_the_old_flat_window_is_gone():
    """A flat 15 min would give the 30min cascade under one candle — regression
    guard: its window must now be well beyond 15 minutes."""
    assert engine("30min").timeout_seconds > 15 * 60


# --------------------------------------------------------------------------- #
# AC20 — candle count is configurable
# --------------------------------------------------------------------------- #

def test_ac20_candle_count_configurable():
    assert engine("5min", candles=5).timeout_seconds == 5 * 5 * 60   # 25 min
    assert engine("15min", candles=2).timeout_seconds == 2 * 15 * 60  # 30 min


# --------------------------------------------------------------------------- #
# AC21 — unknown timeframe degrades safely
# --------------------------------------------------------------------------- #

def test_ac21_unknown_timeframe_falls_back_without_crashing():
    eng = engine("7min")                       # not in TIMEFRAME_MINUTES
    assert eng.timeout_seconds > 0             # built, did not raise


# --------------------------------------------------------------------------- #
# AC22 — expiry follows the scaled window
# --------------------------------------------------------------------------- #

def test_ac22_expiry_uses_the_scaled_window():
    eng = engine("30min")                      # window = 90 min
    now = time.time()

    still_alive = PendingWatch(ob=bull_ob(), tap_time=now - 80 * 60,
                               timeout_seconds=eng.timeout_seconds)
    expired = PendingWatch(ob=bull_ob(), tap_time=now - 95 * 60,
                           timeout_seconds=eng.timeout_seconds)

    assert still_alive.is_expired() is False   # 80 min < 90 min window
    assert expired.is_expired() is True        # 95 min > 90 min window


def test_ac22_new_watch_inherits_engine_window():
    eng = engine("15min")                      # window = 45 min
    eng.add_watch(bull_ob())
    watch = eng._watches[bull_ob().ob_id]
    assert watch.timeout_seconds == 45 * 60
    assert watch.is_expired() is False         # just tapped
