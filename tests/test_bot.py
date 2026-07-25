"""
Tests for the LTF tap fix + SQLite state persistence.

Run from the repo root:  python -m pytest tests/ -v

Covers the SPEC.md acceptance criteria AC1..AC7. These tests avoid importing
main.py / chart_generator.py so they do not require matplotlib/mplfinance.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd
import pytest

from ob_detector import OrderBlock, OrderBlockDetector
from ltf_confirmation import LTFConfirmationEngine
from state_store import StateStore


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def make_bull_ob(ob_id: str = "XAU/USD_1h_bull_T1") -> OrderBlock:
    """Bullish OB: body zone [104, 105], protective wick low 103."""
    return OrderBlock(
        ob_id=ob_id, symbol="XAU/USD", htf="1h", direction="bullish",
        ob_high=105.0, ob_low=104.0, wick_high=105.5, wick_low=103.0,
        candle_time="2026-07-23T10:00:00",
    )


def make_bear_ob(ob_id: str = "XAU/USD_1h_bear_T1") -> OrderBlock:
    """Bearish OB: body zone [110, 111], protective wick high 112."""
    return OrderBlock(
        ob_id=ob_id, symbol="XAU/USD", htf="1h", direction="bearish",
        ob_high=111.0, ob_low=110.0, wick_high=112.0, wick_low=109.5,
        candle_time="2026-07-23T10:00:00",
    )


def candle(
    close: float,
    open_: Optional[float] = None,
    high: Optional[float] = None,
    low: Optional[float] = None,
) -> pd.Series:
    return pd.Series({
        "open": open_ if open_ is not None else close,
        "close": close,
        "high": high if high is not None else close + 0.5,
        "low": low if low is not None else close - 0.5,
    })


# --------------------------------------------------------------------------- #
# AC1 — tap is not gated by mitigation (close inside the zone)
# --------------------------------------------------------------------------- #

def test_ac1_tap_fires_even_when_candle_closes_inside_zone():
    ob = make_bull_ob()
    det = OrderBlockDetector("XAU/USD", "1h", store=None)
    det._obs = [ob]

    # An HTF candle closes INSIDE the zone (104.5). Old code marked this
    # "mitigated" and dropped the OB from check_tap. New code must not.
    det._check_invalidation(candle(close=104.5))
    assert ob.invalidated is False

    tapped = det.check_tap(104.5)
    assert [o.ob_id for o in tapped] == [ob.ob_id]


# --------------------------------------------------------------------------- #
# AC2 — invalidation only when price closes beyond the protective wick
# --------------------------------------------------------------------------- #

def test_ac2_bull_invalidated_only_by_close_below_wick_low():
    ob = make_bull_ob()  # wick_low = 103
    assert ob.is_invalidated_by(candle(close=104.5)) is False  # inside zone
    assert ob.is_invalidated_by(candle(close=103.5)) is False  # below zone, above wick
    assert ob.is_invalidated_by(candle(close=102.9)) is True   # below wick low


def test_ac2_bear_invalidated_only_by_close_above_wick_high():
    ob = make_bear_ob()  # wick_high = 112
    assert ob.is_invalidated_by(candle(close=110.5)) is False  # inside zone
    assert ob.is_invalidated_by(candle(close=111.5)) is False  # above zone, below wick
    assert ob.is_invalidated_by(candle(close=112.1)) is True   # above wick high


def test_ac2_check_invalidation_sets_flag():
    ob = make_bull_ob()
    det = OrderBlockDetector("XAU/USD", "1h", store=None)
    det._obs = [ob]
    det._check_invalidation(candle(close=104.5))  # inside -> still active
    assert ob.invalidated is False
    det._check_invalidation(candle(close=102.0))  # beyond wick -> retired
    assert ob.invalidated is True


# --------------------------------------------------------------------------- #
# AC3 — a tapped OB is not re-tapped
# --------------------------------------------------------------------------- #

def test_ac3_tap_fires_once():
    ob = make_bull_ob()
    det = OrderBlockDetector("XAU/USD", "1h", store=None)
    det._obs = [ob]

    first = det.check_tap(104.5)
    assert len(first) == 1
    det.mark_tapped(first[0])   # latch happens after the watch is armed

    second = det.check_tap(104.5)
    assert second == []
    assert ob.tapped is True
    assert ob.tap_count == 1


def test_ac3_invalidated_ob_never_taps():
    ob = make_bull_ob()
    ob.invalidated = True
    det = OrderBlockDetector("XAU/USD", "1h", store=None)
    det._obs = [ob]
    assert det.check_tap(104.5) == []


# --------------------------------------------------------------------------- #
# AC4 — state persisted in SQLite and reloaded across instances (restart sim)
# --------------------------------------------------------------------------- #

def test_ac4_order_blocks_survive_new_detector(tmp_path):
    db = str(tmp_path / "bot.db")
    ob = make_bull_ob()
    ob.tapped = True
    ob.tap_count = 1

    det = OrderBlockDetector("XAU/USD", "1h", store=StateStore(db))
    det._obs = [ob]
    det._save_state()

    # Simulate a restart: brand-new store connection + detector on same file.
    det2 = OrderBlockDetector("XAU/USD", "1h", store=StateStore(db))
    assert len(det2._obs) == 1
    reloaded = det2._obs[0]
    assert reloaded.ob_id == ob.ob_id
    assert reloaded.tapped is True
    assert reloaded.tap_count == 1
    assert reloaded.direction == "bullish"
    assert reloaded.wick_low == 103.0


def test_ac4_watches_survive_new_engine(tmp_path):
    db = str(tmp_path / "bot.db")
    ob = make_bull_ob()

    eng = LTFConfirmationEngine("XAU/USD", "1h", "5min", store=StateStore(db))
    eng.add_watch(ob)
    assert eng.is_watching(ob.ob_id)

    # Simulate a restart.
    eng2 = LTFConfirmationEngine("XAU/USD", "1h", "5min", store=StateStore(db))
    assert eng2.is_watching(ob.ob_id)
    reloaded = eng2._watches[ob.ob_id]
    assert reloaded.ob.ob_id == ob.ob_id
    assert reloaded.ob.direction == "bullish"


def test_ac4_deleted_watch_gone_after_restart(tmp_path):
    db = str(tmp_path / "bot.db")
    ob = make_bull_ob()
    eng = LTFConfirmationEngine("XAU/USD", "1h", "5min", store=StateStore(db))
    eng.add_watch(ob)
    eng._drop_watch(ob.ob_id)
    del eng._watches[ob.ob_id]

    eng2 = LTFConfirmationEngine("XAU/USD", "1h", "5min", store=StateStore(db))
    assert not eng2.is_watching(ob.ob_id)


# --------------------------------------------------------------------------- #
# AC5 — sqlite3 is stdlib; no installable dependency added
# --------------------------------------------------------------------------- #

def test_ac5_sqlite3_is_stdlib_and_store_works(tmp_path):
    import sqlite3  # stdlib import must succeed
    assert sqlite3.sqlite_version_info >= (3, 0, 0)  # library is functional

    store = StateStore(str(tmp_path / "bot.db"))
    store.save_obs("XAU/USD", "1h", [])
    assert store.load_obs("XAU/USD", "1h") == []


def test_ac5_requirements_has_no_installable_sqlite3():
    import pathlib
    req = pathlib.Path(__file__).resolve().parent.parent / "requirements.txt"
    for raw in req.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()  # strip comments
        assert line.lower() != "sqlite3", "sqlite3 is stdlib; must not be a pip dep"


# --------------------------------------------------------------------------- #
# AC6 — detection behaviour preserved
# --------------------------------------------------------------------------- #

def _synthetic_bull_ob_frame() -> pd.DataFrame:
    """20 candles: flat baseline, a bearish OB origin at idx 12, then a strong
    bullish displacement (idx 13-14), then continuation that never closes back
    into the OB zone."""
    rows = []
    # 0..11 baseline, body ~1.0 around price 100
    for i in range(12):
        o, c = (100.0, 101.0) if i % 2 == 0 else (101.0, 100.0)
        rows.append({"open_time": f"2026-07-01T{i:02d}:00:00",
                     "open": o, "high": max(o, c) + 0.2, "low": min(o, c) - 0.2,
                     "close": c, "volume": 100})
    # 12: bearish OB origin (zone [104, 105])
    rows.append({"open_time": "2026-07-01T12:00:00",
                 "open": 105.0, "high": 105.5, "low": 103.0, "close": 104.0, "volume": 100})
    # 13,14: strong bullish displacement (body 6)
    rows.append({"open_time": "2026-07-01T13:00:00",
                 "open": 104.0, "high": 110.5, "low": 103.8, "close": 110.0, "volume": 100})
    rows.append({"open_time": "2026-07-01T14:00:00",
                 "open": 110.0, "high": 116.5, "low": 109.8, "close": 116.0, "volume": 100})
    # 15..19: continuation well above the zone (never closes into [104,105])
    for i, base in enumerate([117.0, 118.0, 117.5, 118.5, 119.0], start=15):
        rows.append({"open_time": f"2026-07-01T{i:02d}:00:00",
                     "open": base - 0.5, "high": base + 0.5, "low": base - 1.0,
                     "close": base, "volume": 100})
    return pd.DataFrame(rows)


def test_ac6_detection_still_finds_bullish_ob():
    df = _synthetic_bull_ob_frame()
    det = OrderBlockDetector("XAU/USD", "1h", store=None)
    new = det.update(df)
    directions = {ob.direction for ob in new}
    assert "bullish" in directions
    bull = next(ob for ob in new if ob.direction == "bullish")
    # Zone is the OB candle's full range (wick to wick): candle 12 is
    # open=105 high=105.5 low=103 close=104, so the zone is [103, 105.5],
    # not the body [104, 105].
    assert bull.ob_low == pytest.approx(103.0)
    assert bull.ob_high == pytest.approx(105.5)
    # The freshly detected, untapped OB taps when price returns into the zone.
    assert len(det.check_tap(104.5)) == 1


def test_ac6_telegram_messages_unchanged():
    import telegram_notifier
    import inspect
    src = inspect.getsource(telegram_notifier)
    assert "Watching for LTF tap..." in src
    assert "*OB TAPPED*" in src


# --------------------------------------------------------------------------- #
# AC7 — is_watching exists and the tap loop does not raise
# --------------------------------------------------------------------------- #

def test_ac7_is_watching():
    ob = make_bull_ob()
    eng = LTFConfirmationEngine("XAU/USD", "1h", "5min", store=None)
    assert eng.is_watching(ob.ob_id) is False
    eng.add_watch(ob)
    assert eng.is_watching(ob.ob_id) is True


def test_ac7_tap_loop_runs_without_raising():
    """Reproduces the main.py tap loop: check_tap -> is_watching -> add_watch."""
    ob = make_bull_ob()
    det = OrderBlockDetector("XAU/USD", "1h", store=None)
    det._obs = [ob]
    eng = LTFConfirmationEngine("XAU/USD", "1h", "5min", store=None)

    tapped = det.check_tap(104.5)
    for o in tapped:
        if eng.is_watching(o.ob_id):
            continue
        eng.add_watch(o)
        det.mark_tapped(o)

    assert eng.is_watching(ob.ob_id) is True
    assert ob.tapped is True
