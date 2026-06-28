"""
ltf_confirmation.py — MSS + FVG + Displacement sequential confirmation.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import pandas as pd

from ob_detector import OrderBlock
from logger import get_logger

log = get_logger(__name__)

DISPLACEMENT_MULTIPLIER = 1.5
SWING_PIVOT_BARS = 2
MIN_CANDLES = 20


@dataclass
class Signal:
    symbol: str
    direction: str        # "long" | "short"
    htf: str
    ltf: str
    cascade_label: str
    ob: OrderBlock
    entry_price: float
    sl_price: float
    tp1: float            # 1:2 R:R
    tp2: float            # 1:3 R:R
    fvg_high: float
    fvg_low: float
    mss_level: float
    timestamp: float = field(default_factory=time.time)


@dataclass
class PendingWatch:
    ob: OrderBlock
    tap_time: float
    timeout_seconds: int = 900
    displacement_confirmed: bool = False
    fvg_confirmed: bool = False
    displacement_candle_idx: int = -1
    fvg_high: float = 0.0
    fvg_low: float = 0.0
    swing_level: float = 0.0

    def is_expired(self) -> bool:
        return (time.time() - self.tap_time) > self.timeout_seconds


class LTFConfirmationEngine:

    def __init__(
        self,
        symbol: str, htf: str, ltf: str,
        sl_buffer_pct: float = 0.0005,
        signal_timeout_minutes: int = 15,
    ) -> None:
        self.symbol = symbol
        self.htf = htf
        self.ltf = ltf
        self.sl_buffer_pct = sl_buffer_pct
        self.timeout_seconds = signal_timeout_minutes * 60
        self._watches: Dict[str, PendingWatch] = {}

    def add_watch(self, ob: OrderBlock) -> None:
        if ob.ob_id in self._watches:
            return
        self._watches[ob.ob_id] = PendingWatch(
            ob=ob, tap_time=time.time(),
            timeout_seconds=self.timeout_seconds,
        )
        log.info(f"[LTF] 👀 Watch — {self.symbol} {ob.direction.upper()} {self.htf}→{self.ltf}")

    def process(self, df: pd.DataFrame) -> List[Signal]:
        if len(df) < MIN_CANDLES:
            return []
        # Clean expired
        for oid in [k for k, w in self._watches.items() if w.is_expired()]:
            log.info(f"[LTF] ⏰ Expired: {oid}")
            del self._watches[oid]

        signals = []
        for ob_id, watch in list(self._watches.items()):
            sig = self._evaluate(watch, df)
            if sig:
                signals.append(sig)
                del self._watches[ob_id]
        return signals

    def active_count(self) -> int:
        return len(self._watches)

    def _evaluate(self, watch: PendingWatch, df: pd.DataFrame) -> Optional[Signal]:
        # Phase 2: Displacement
        if not watch.displacement_confirmed:
            idx = self._find_displacement(df, watch.ob.direction)
            if idx == -1:
                return None
            watch.displacement_confirmed = True
            watch.displacement_candle_idx = idx

        # Phase 3: FVG
        if not watch.fvg_confirmed:
            fvg = self._find_fvg(df, watch.displacement_candle_idx, watch.ob.direction)
            if fvg is None:
                return None
            watch.fvg_confirmed = True
            watch.fvg_high, watch.fvg_low = fvg
            watch.swing_level = self._get_swing_level(df, watch.ob.direction)
            log.debug(f"[LTF] Phase 3 ✓ FVG [{watch.fvg_low:.5f}–{watch.fvg_high:.5f}]")

        # Phase 4: MSS
        if not self._check_mss(df, watch.swing_level, watch.ob.direction):
            return None

        return self._build_signal(watch, df)

    def _find_displacement(self, df: pd.DataFrame, direction: str) -> int:
        for i in range(len(df) - 1, max(11, len(df) - 20), -1):
            c = df.iloc[i]
            body = abs(c["close"] - c["open"])
            avg = (df["close"].iloc[i-10:i] - df["open"].iloc[i-10:i]).abs().mean()
            if avg == 0:
                continue
            is_dir = (c["close"] > c["open"]) if direction == "bullish" else (c["close"] < c["open"])
            if body >= avg * DISPLACEMENT_MULTIPLIER and is_dir:
                return i
        return -1

    def _find_fvg(self, df: pd.DataFrame, disp_idx: int, direction: str) -> Optional[Tuple[float, float]]:
        n = disp_idx
        if n < 1 or n + 1 >= len(df):
            return None
        c_before = df.iloc[n - 1]
        c_after  = df.iloc[n + 1]
        if direction == "bullish" and c_before["high"] < c_after["low"]:
            return c_after["low"], c_before["high"]
        if direction == "bearish" and c_before["low"] > c_after["high"]:
            return c_before["low"], c_after["high"]
        return None

    def _get_swing_level(self, df: pd.DataFrame, direction: str) -> float:
        highs = df["high"].values
        lows  = df["low"].values
        n = len(df)
        if direction == "bullish":
            for i in range(n - 3, SWING_PIVOT_BARS, -1):
                if highs[i] > highs[i-1] and highs[i] > highs[i-2] and highs[i] > highs[i+1] and highs[i] > highs[i+2]:
                    return highs[i]
            return df["high"].iloc[-6:-1].max()
        else:
            for i in range(n - 3, SWING_PIVOT_BARS, -1):
                if lows[i] < lows[i-1] and lows[i] < lows[i-2] and lows[i] < lows[i+1] and lows[i] < lows[i+2]:
                    return lows[i]
            return df["low"].iloc[-6:-1].min()

    def _check_mss(self, df: pd.DataFrame, swing_level: float, direction: str) -> bool:
        close = df["close"].iloc[-1]
        return close > swing_level if direction == "bullish" else close < swing_level

    def _build_signal(self, watch: PendingWatch, df: pd.DataFrame) -> Signal:
        ob = watch.ob
        entry = float(df["close"].iloc[-1])
        if ob.direction == "bullish":
            direction = "long"
            sl = ob.wick_low * (1 - self.sl_buffer_pct)
        else:
            direction = "short"
            sl = ob.wick_high * (1 + self.sl_buffer_pct)
        risk = abs(entry - sl)
        tp1 = entry + risk * 2 if direction == "long" else entry - risk * 2
        tp2 = entry + risk * 3 if direction == "long" else entry - risk * 3
        log.info(f"[LTF] ✅ {self.symbol} {direction.upper()} entry={entry:.5f} SL={sl:.5f} TP2={tp2:.5f}")
        return Signal(
            symbol=self.symbol, direction=direction,
            htf=self.htf, ltf=self.ltf,
            cascade_label=f"{self.htf} OB → {self.ltf} MSS",
            ob=ob, entry_price=entry, sl_price=sl,
            tp1=tp1, tp2=tp2,
            fvg_high=watch.fvg_high, fvg_low=watch.fvg_low,
            mss_level=watch.swing_level,
        )
