"""
ltf_confirmation.py — MSS + FVG + Displacement sequential confirmation.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Tuple

import pandas as pd

from ob_detector import OrderBlock
from logger import get_logger
from state_store import StateStore

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
        store: Optional[StateStore] = None,
    ) -> None:
        self.symbol = symbol
        self.htf = htf
        self.ltf = ltf
        self.sl_buffer_pct = sl_buffer_pct
        self.timeout_seconds = signal_timeout_minutes * 60
        self._store = store
        self._watches: Dict[str, PendingWatch] = {}
        self._load_watches()

    def is_watching(self, ob_id: str) -> bool:
        """True if an armed watch exists for this OB (used by the tap loop)."""
        return ob_id in self._watches

    def add_watch(self, ob: OrderBlock) -> None:
        if ob.ob_id in self._watches:
            return
        watch = PendingWatch(
            ob=ob, tap_time=time.time(),
            timeout_seconds=self.timeout_seconds,
        )
        self._watches[ob.ob_id] = watch
        self._persist_watch(watch)
        log.info(f"[LTF] 👀 Watch — {self.symbol} {ob.direction.upper()} {self.htf}→{self.ltf}")

    def process(self, df: pd.DataFrame) -> List[Signal]:
        if len(df) < MIN_CANDLES:
            return []
        # Clean expired
        for oid in [k for k, w in self._watches.items() if w.is_expired()]:
            log.info(f"[LTF] ⏰ Expired: {oid}")
            del self._watches[oid]
            self._drop_watch(oid)

        signals = []
        for ob_id, watch in list(self._watches.items()):
            sig = self._evaluate(watch, df)
            if sig:
                signals.append(sig)
                del self._watches[ob_id]
                self._drop_watch(ob_id)
            else:
                # Persist confirmation progress (displacement/FVG flags) so a
                # restart resumes mid-sequence instead of from scratch.
                self._persist_watch(watch)
        return signals

    def active_count(self) -> int:
        return len(self._watches)

    def _evaluate(self, watch: PendingWatch, df: pd.DataFrame) -> Optional[Signal]:
        # Confirmation = displacement + MSS. FVG is optional confluence only
        # (recorded for the signal/chart when present, never a gate).

        # Phase 1: Displacement (required)
        if not watch.displacement_confirmed:
            idx = self._find_displacement(df, watch.ob.direction)
            if idx == -1:
                return None
            watch.displacement_confirmed = True
            watch.displacement_candle_idx = idx
            # Swing level to break for the MSS check — captured once, when
            # displacement first confirms (was previously set in the FVG block).
            watch.swing_level = self._get_swing_level(df, watch.ob.direction)
            # Optional FVG confluence: record it if it is there, but do not block.
            fvg = self._find_fvg(df, watch.displacement_candle_idx, watch.ob.direction)
            if fvg is not None:
                watch.fvg_confirmed = True
                watch.fvg_high, watch.fvg_low = fvg
                log.debug(f"[LTF] FVG confluence [{watch.fvg_low:.5f}–{watch.fvg_high:.5f}]")

        # Phase 2: MSS (required)
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

    # ------------------------------------------------------------------ #
    # Persistence (SQLite via StateStore; in-memory only when store is None)
    # ------------------------------------------------------------------ #

    def _load_watches(self) -> None:
        if self._store is None:
            return
        try:
            for row in self._store.load_watches(self.symbol, self.htf, self.ltf):
                watch = self._row_to_watch(row)
                self._watches[watch.ob.ob_id] = watch
            if self._watches:
                log.info(
                    f"[LTF] Loaded {len(self._watches)} watch(es) — "
                    f"{self.symbol} {self.htf}→{self.ltf}"
                )
        except Exception as e:
            log.warning(f"[LTF] Watch load fail (fresh): {e}")
            self._watches = {}

    def _persist_watch(self, watch: PendingWatch) -> None:
        if self._store is None:
            return
        try:
            self._store.upsert_watch(self._watch_to_row(watch))
        except Exception as e:
            log.error(f"[LTF] Watch save fail: {e}")

    def _drop_watch(self, ob_id: str) -> None:
        if self._store is None:
            return
        try:
            self._store.delete_watch(ob_id)
        except Exception as e:
            log.error(f"[LTF] Watch delete fail: {e}")

    def _watch_to_row(self, watch: PendingWatch) -> dict:
        return {
            "ob_id": watch.ob.ob_id,
            "symbol": self.symbol, "htf": self.htf, "ltf": self.ltf,
            "ob_json": json.dumps(asdict(watch.ob)),
            "tap_time": watch.tap_time,
            "timeout_seconds": watch.timeout_seconds,
            "displacement_confirmed": watch.displacement_confirmed,
            "fvg_confirmed": watch.fvg_confirmed,
            "displacement_candle_idx": watch.displacement_candle_idx,
            "fvg_high": watch.fvg_high,
            "fvg_low": watch.fvg_low,
            "swing_level": watch.swing_level,
        }

    @staticmethod
    def _row_to_watch(row: dict) -> PendingWatch:
        ob = OrderBlock(**json.loads(row["ob_json"]))
        return PendingWatch(
            ob=ob,
            tap_time=row["tap_time"],
            timeout_seconds=row["timeout_seconds"],
            displacement_confirmed=row["displacement_confirmed"],
            fvg_confirmed=row["fvg_confirmed"],
            displacement_candle_idx=row["displacement_candle_idx"],
            fvg_high=row["fvg_high"],
            fvg_low=row["fvg_low"],
            swing_level=row["swing_level"],
        )
