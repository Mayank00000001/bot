"""
ob_detector.py — HTF Order Block detection.

Order-block lifecycle:
    detected -> (price enters body zone) TAPPED  [alert + arm LTF watch]
             -> (LTF MSS confirmed in time) signal, then retired
             -> (HTF candle CLOSES beyond the protective wick) INVALIDATED, retired

Key fix (see SPEC.md):
    "Tap" (price returns into the zone) and "invalidation" (the OB fails) are now
    separate events. Previously an OB was marked mitigated as soon as a candle
    closed *inside* the zone — but that is the tap we want to act on, so the OB
    was killed before it could ever tap. Now a close inside the zone arms the
    watch; only a close *beyond the protective wick* invalidates the OB.

Invariants:
    - An OB is tapped at most once (``tapped`` latches True on first tap).
    - ``check_tap`` never returns an invalidated or already-tapped OB.
    - Detection still surfaces only *fresh* OBs — ones price has not already
      closed back into after they formed (``_already_touched``).
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import List, Optional, Set

import pandas as pd

from logger import get_logger
from state_store import StateStore

log = get_logger(__name__)


@dataclass
class OrderBlock:
    ob_id: str
    symbol: str
    htf: str
    direction: str
    ob_high: float
    ob_low: float
    wick_high: float
    wick_low: float
    candle_time: str
    invalidated: bool = False   # price closed beyond the protective wick -> retired
    tapped: bool = False        # price returned into the zone -> watch armed (once)
    tap_count: int = 0
    notified: bool = False      # "New OB" Telegram alert already sent

    def contains_price(self, price: float) -> bool:
        """True if price is inside the OB body zone (the tap test)."""
        # bool() so a numpy scalar input does not leak a numpy.bool_ out.
        return bool(self.ob_low <= price <= self.ob_high)

    def closed_inside(self, candle: pd.Series) -> bool:
        """True if the candle CLOSED inside the body zone.

        Used only as the detection freshness filter (has price already returned
        to this OB since it formed?) — not as an invalidation test.
        """
        return bool(self.ob_low <= candle["close"] <= self.ob_high)

    def is_invalidated_by(self, candle: pd.Series) -> bool:
        """True if the candle CLOSED beyond the protective wick — the OB failed.

        Bullish OB: invalidated by a close below its wick low.
        Bearish OB: invalidated by a close above its wick high.
        """
        if self.direction == "bullish":
            return bool(candle["close"] < self.wick_low)
        return bool(candle["close"] > self.wick_high)


class OrderBlockDetector:

    DISPLACEMENT_MULTIPLIER = 1.5
    MIN_DISPLACEMENT_CANDLES = 2

    def __init__(
        self,
        symbol: str,
        htf: str,
        max_obs: int = 5,
        store: Optional[StateStore] = None,
    ) -> None:
        self.symbol = symbol
        self.htf = htf
        self.max_obs = max_obs
        self._store = store
        self._obs: List[OrderBlock] = []
        self._load_state()

    def update(self, df: pd.DataFrame) -> List[OrderBlock]:
        """Retire invalidated OBs, detect new ones, return only the NEW OBs."""
        if len(df) < 15:
            return []
        self._check_invalidation(df.iloc[-1])
        new_obs = self._scan(df)

        # Cap active OBs
        active = self.get_active_obs()
        if len(active) + len(new_obs) > self.max_obs:
            excess = len(active) + len(new_obs) - self.max_obs
            remove_ids = {ob.ob_id for ob in active[:excess]}
            self._obs = [ob for ob in self._obs if ob.ob_id not in remove_ids]

        # Mark new OBs as notified before adding
        for ob in new_obs:
            ob.notified = True

        self._obs.extend(new_obs)
        # Persist invalidation flips + cap removals + new OBs from this scan.
        self._save_state()
        return new_obs

    def check_tap(self, price: float) -> List[OrderBlock]:
        """Return OBs newly tapped by ``price`` (in-zone, not invalidated, not
        yet tapped). Latches ``tapped`` so each OB taps at most once."""
        tapped = [
            ob for ob in self._obs
            if not ob.invalidated and not ob.tapped and ob.contains_price(price)
        ]
        for ob in tapped:
            ob.tapped = True
            ob.tap_count += 1
        if tapped:
            self._save_state()
        return tapped

    def get_active_obs(self) -> List[OrderBlock]:
        """OBs that have not been invalidated (still in play)."""
        return [ob for ob in self._obs if not ob.invalidated]

    def _is_displacement(self, df: pd.DataFrame, start_idx: int, direction: str) -> bool:
        """
        Check displacement — body must be significantly larger than recent
        average AND move price meaningfully (not just one noisy candle).
        """
        if start_idx < 10:
            return False
        prior_bodies = (df["close"].iloc[start_idx-10:start_idx] - df["open"].iloc[start_idx-10:start_idx]).abs()
        avg_body = prior_bodies.mean()
        if avg_body == 0:
            return False
        threshold = avg_body * self.DISPLACEMENT_MULTIPLIER
        count = 0
        for i in range(start_idx, min(start_idx + 6, len(df))):
            c = df.iloc[i]
            body = abs(c["close"] - c["open"])
            is_dir = (c["close"] > c["open"]) if direction == "bullish" else (c["close"] < c["open"])
            if body >= threshold and is_dir:
                count += 1
                if count >= self.MIN_DISPLACEMENT_CANDLES:
                    return True
            else:
                count = 0  # reset on non-qualifying candle — must be truly consecutive
        return False

    def _displacement_strength(self, df: pd.DataFrame, start_idx: int, direction: str) -> float:
        """
        Calculate total displacement move size (used to rank OBs by strength).
        Returns total price movement across the displacement candles.
        """
        total_move = 0.0
        for i in range(start_idx, min(start_idx + 6, len(df))):
            c = df.iloc[i]
            is_dir = (c["close"] > c["open"]) if direction == "bullish" else (c["close"] < c["open"])
            if is_dir:
                total_move += abs(c["close"] - c["open"])
            else:
                break
        return total_move

    def _scan(self, df: pd.DataFrame) -> List[OrderBlock]:
        """
        Scan for OBs. Instead of registering every candle that technically
        passes displacement, collect all candidates and keep only the STRONGEST
        one per direction in the recent window — this matches how real OBs are
        identified (the origin of the most significant displacement, not every
        minor wiggle).
        """
        existing_keys: Set[str] = set()
        for ob in self._obs:
            existing_keys.add(ob.ob_id)

        scan_end = len(df) - 1
        scan_start = max(10, scan_end - 50)  # wider lookback window

        bullish_candidates = []  # (strength, idx, ob)
        bearish_candidates = []

        for i in range(scan_start, scan_end - self.MIN_DISPLACEMENT_CANDLES):
            c = df.iloc[i]
            candle_time = str(c.get("open_time", i))
            clean_time = candle_time.replace(" ", "T").replace(":", "").replace("-", "")[:15]

            if c["close"] < c["open"]:
                if self._is_displacement(df, i + 1, "bullish"):
                    ob_id = f"{self.symbol}_{self.htf}_bull_{clean_time}"
                    if ob_id not in existing_keys:
                        ob = OrderBlock(
                            ob_id=ob_id, symbol=self.symbol, htf=self.htf,
                            direction="bullish",
                            ob_high=c["open"], ob_low=c["close"],
                            wick_high=c["high"], wick_low=c["low"],
                            candle_time=candle_time,
                        )
                        if not self._already_touched(df, i, ob):
                            strength = self._displacement_strength(df, i + 1, "bullish")
                            bullish_candidates.append((strength, i, ob))

            elif c["close"] > c["open"]:
                if self._is_displacement(df, i + 1, "bearish"):
                    ob_id = f"{self.symbol}_{self.htf}_bear_{clean_time}"
                    if ob_id not in existing_keys:
                        ob = OrderBlock(
                            ob_id=ob_id, symbol=self.symbol, htf=self.htf,
                            direction="bearish",
                            ob_high=c["close"], ob_low=c["open"],
                            wick_high=c["high"], wick_low=c["low"],
                            candle_time=candle_time,
                        )
                        if not self._already_touched(df, i, ob):
                            strength = self._displacement_strength(df, i + 1, "bearish")
                            bearish_candidates.append((strength, i, ob))

        new_obs = []
        # Keep only the strongest bullish + strongest bearish candidate
        # (the most recent, most significant displacement — matches real OB logic)
        if bullish_candidates:
            bullish_candidates.sort(key=lambda x: (x[1], x[0]))  # prioritize recency, then strength
            strongest = max(bullish_candidates, key=lambda x: x[0])
            new_obs.append(strongest[2])
            log.info(f"[OB] 🟢 Bullish — {self.symbol}/{self.htf} [{strongest[2].ob_low:.5f}–{strongest[2].ob_high:.5f}] @ {strongest[2].candle_time} (strength={strongest[0]:.3f})")

        if bearish_candidates:
            bearish_candidates.sort(key=lambda x: (x[1], x[0]))
            strongest = max(bearish_candidates, key=lambda x: x[0])
            new_obs.append(strongest[2])
            log.info(f"[OB] 🔴 Bearish — {self.symbol}/{self.htf} [{strongest[2].ob_low:.5f}–{strongest[2].ob_high:.5f}] @ {strongest[2].candle_time} (strength={strongest[0]:.3f})")

        return new_obs

    def _already_touched(self, df: pd.DataFrame, ob_idx: int, ob: OrderBlock) -> bool:
        """True if price already CLOSED back into the zone after the OB formed.

        Detection freshness filter — such an OB is not a fresh setup, so it is
        not surfaced. (Preserves the original ``candle_close`` behaviour.)
        """
        for i in range(ob_idx + self.MIN_DISPLACEMENT_CANDLES + 1, len(df)):
            if ob.closed_inside(df.iloc[i]):
                return True
        return False

    def _check_invalidation(self, candle: pd.Series) -> None:
        """Retire OBs whose protective wick was closed through by ``candle``."""
        for ob in self._obs:
            if not ob.invalidated and ob.is_invalidated_by(candle):
                ob.invalidated = True
                log.info(f"[OB] ❌ Invalidated (closed beyond wick): {ob.ob_id}")

    # ------------------------------------------------------------------ #
    # Persistence (SQLite via StateStore; in-memory only when store is None)
    # ------------------------------------------------------------------ #

    def _save_state(self) -> None:
        if self._store is None:
            return
        try:
            self._store.save_obs(self.symbol, self.htf, [asdict(ob) for ob in self._obs])
        except Exception as e:
            log.error(f"State save fail: {e}")

    def _load_state(self) -> None:
        if self._store is None:
            return
        try:
            rows = self._store.load_obs(self.symbol, self.htf)
            self._obs = [OrderBlock(**row) for row in rows]
            log.info(f"[OB] Loaded {self.symbol}_{self.htf}: {len(self.get_active_obs())} active")
        except Exception as e:
            log.warning(f"State load fail (fresh): {e}")
            self._obs = []
