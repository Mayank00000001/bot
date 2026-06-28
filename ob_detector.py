"""
ob_detector.py — HTF Order Block detection.

Bullish OB: last bearish candle before strong upward displacement
Bearish OB: last bullish candle before strong downward displacement
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Optional

import pandas as pd

from logger import get_logger

log = get_logger(__name__)


@dataclass
class OrderBlock:
    ob_id: str
    symbol: str
    htf: str
    direction: str       # "bullish" | "bearish"
    ob_high: float
    ob_low: float
    wick_high: float
    wick_low: float
    candle_time: str
    is_mitigated: bool = False
    tap_count: int = 0

    def contains_price(self, price: float) -> bool:
        return self.ob_low <= price <= self.ob_high

    def is_mitigated_by(self, candle: pd.Series, mode: str = "candle_close") -> bool:
        if mode == "candle_close":
            return self.ob_low <= candle["close"] <= self.ob_high
        return candle["low"] <= self.ob_high and candle["high"] >= self.ob_low


class OrderBlockDetector:

    DISPLACEMENT_MULTIPLIER = 1.5
    MIN_DISPLACEMENT_CANDLES = 2

    def __init__(
        self,
        symbol: str,
        htf: str,
        max_obs: int = 5,
        mitigation_mode: str = "candle_close",
        state_file: str = "state/ob_state.json",
    ) -> None:
        self.symbol = symbol
        self.htf = htf
        self.max_obs = max_obs
        self.mitigation_mode = mitigation_mode
        self._state_file = Path(state_file)
        self._obs: List[OrderBlock] = []
        self._load_state()

    def update(self, df: pd.DataFrame) -> List[OrderBlock]:
        if len(df) < 15:
            return []
        self._check_mitigation(df.iloc[-1])
        new_obs = self._scan(df)
        active = self.get_active_obs()
        if len(active) + len(new_obs) > self.max_obs:
            excess = len(active) + len(new_obs) - self.max_obs
            remove_ids = {ob.ob_id for ob in active[:excess]}
            self._obs = [ob for ob in self._obs if ob.ob_id not in remove_ids]
        self._obs.extend(new_obs)
        if new_obs:
            self._save_state()
        return new_obs

    def check_tap(self, price: float) -> List[OrderBlock]:
        tapped = [ob for ob in self._obs if not ob.is_mitigated and ob.contains_price(price)]
        for ob in tapped:
            ob.tap_count += 1
        if tapped:
            self._save_state()
        return tapped

    def get_active_obs(self) -> List[OrderBlock]:
        return [ob for ob in self._obs if not ob.is_mitigated]

    def _is_displacement(self, df: pd.DataFrame, start_idx: int, direction: str) -> bool:
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
                break
        return False

    def _scan(self, df: pd.DataFrame) -> List[OrderBlock]:
        existing = {ob.ob_id for ob in self._obs}
        new_obs = []
        scan_end = len(df) - 1
        scan_start = max(10, scan_end - 30)

        for i in range(scan_start, scan_end - self.MIN_DISPLACEMENT_CANDLES):
            c = df.iloc[i]

            if c["close"] < c["open"]:  # Bearish candle → Bullish OB candidate
                if self._is_displacement(df, i + 1, "bullish"):
                    ob_id = f"{self.symbol}_{self.htf}_bull_{i}"
                    if ob_id not in existing:
                        ob = OrderBlock(
                            ob_id=ob_id, symbol=self.symbol, htf=self.htf,
                            direction="bullish",
                            ob_high=c["open"], ob_low=c["close"],
                            wick_high=c["high"], wick_low=c["low"],
                            candle_time=str(c.get("open_time", i)),
                        )
                        if not self._already_mitigated(df, i, ob):
                            new_obs.append(ob)
                            log.info(f"[OB] 🟢 Bullish — {self.symbol}/{self.htf} [{ob.ob_low:.5f}–{ob.ob_high:.5f}]")

            elif c["close"] > c["open"]:  # Bullish candle → Bearish OB candidate
                if self._is_displacement(df, i + 1, "bearish"):
                    ob_id = f"{self.symbol}_{self.htf}_bear_{i}"
                    if ob_id not in existing:
                        ob = OrderBlock(
                            ob_id=ob_id, symbol=self.symbol, htf=self.htf,
                            direction="bearish",
                            ob_high=c["close"], ob_low=c["open"],
                            wick_high=c["high"], wick_low=c["low"],
                            candle_time=str(c.get("open_time", i)),
                        )
                        if not self._already_mitigated(df, i, ob):
                            new_obs.append(ob)
                            log.info(f"[OB] 🔴 Bearish — {self.symbol}/{self.htf} [{ob.ob_low:.5f}–{ob.ob_high:.5f}]")
        return new_obs

    def _already_mitigated(self, df: pd.DataFrame, ob_idx: int, ob: OrderBlock) -> bool:
        for i in range(ob_idx + self.MIN_DISPLACEMENT_CANDLES + 1, len(df)):
            if ob.is_mitigated_by(df.iloc[i], self.mitigation_mode):
                return True
        return False

    def _check_mitigation(self, candle: pd.Series) -> None:
        for ob in self._obs:
            if not ob.is_mitigated and ob.is_mitigated_by(candle, self.mitigation_mode):
                ob.is_mitigated = True
                log.info(f"[OB] ❌ Mitigated: {ob.ob_id}")

    def _save_state(self) -> None:
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            key = f"{self.symbol}_{self.htf}"
            existing = {}
            if self._state_file.exists():
                with self._state_file.open("r") as f:
                    existing = json.load(f)
            existing[key] = [asdict(ob) for ob in self._obs]
            with self._state_file.open("w") as f:
                json.dump(existing, f, indent=2)
        except Exception as e:
            log.error(f"State save fail: {e}")

    def _load_state(self) -> None:
        try:
            if not self._state_file.exists():
                return
            with self._state_file.open("r") as f:
                data = json.load(f)
            key = f"{self.symbol}_{self.htf}"
            self._obs = [OrderBlock(**ob) for ob in data.get(key, [])]
            log.info(f"[OB] Loaded {key}: {len(self.get_active_obs())} active")
        except Exception as e:
            log.warning(f"State load fail (fresh): {e}")
            self._obs = []
