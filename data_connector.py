"""
data_connector.py — OANDA REST API se Forex/Gold/Index candles fetch karta hai.

OANDA Practice (Demo) account API:
  - Free, unlimited practical use (rate limit generous)
  - Real-time data, zero delay
  - Forex, Gold (XAU/USD), Indices (SPX500, NAS100) sab available
"""

from __future__ import annotations

import time
from typing import Optional

import pandas as pd
import requests

from logger import get_logger

log = get_logger(__name__)

# Practice account ka base URL (Live account ke liye alag hota hai)
BASE_URL = "https://api-fxpractice.oanda.com/v3"

# OANDA instrument naming convention
SYMBOL_MAP = {
    "XAU/USD": "XAU_USD",
    "EUR/USD": "EUR_USD",
    "GBP/USD": "GBP_USD",
    "USD/JPY": "USD_JPY",
    "AUD/USD": "AUD_USD",
    "USD/CAD": "USD_CAD",
    "GBP/JPY": "GBP_JPY",
    "BTC/USD": "BTC_USD",
    "SPX":     "SPX500_USD",
    "NDX":     "NAS100_USD",
}

# OANDA candle granularity mapping
GRANULARITY_MAP = {
    "5min":  "M5",
    "15min": "M15",
    "30min": "M30",
    "1h":    "H1",
    "2h":    "H2",
    "4h":    "H4",
    "1day":  "D",
    "1week": "W",
}

# OANDA max count per request = 5000, lekin hum kam rakhte hain
CANDLE_COUNT = {
    "1week": 100,
    "1day":  200,
    "4h":    300,
    "2h":    300,
    "1h":    300,
    "30min": 300,
    "15min": 300,
    "5min":  300,
}


class DataConnector:
    """
    OANDA REST API wrapper (Practice/Demo account).

    Usage:
        conn = DataConnector(api_token="your_token")
        df = conn.get_candles("XAU/USD", "4h")
    """

    REQUEST_DELAY = 0.5  # OANDA rate limit generous hai — 0.5s safe gap

    def __init__(self, api_token: str) -> None:
        self._token = api_token
        self._last_request_time = 0.0
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
        })

    def test_connection(self) -> bool:
        """API token valid hai? Check karo."""
        try:
            res = self._session.get(f"{BASE_URL}/accounts", timeout=10)
            if res.status_code == 200:
                data = res.json()
                accounts = data.get("accounts", [])
                log.info(f"OANDA connected ✅ — {len(accounts)} account(s) found")
                return True
            log.error(f"OANDA test fail: {res.status_code} — {res.text[:200]}")
            return False
        except Exception as e:
            log.error(f"OANDA connection error: {e}")
            return False

    def get_candles(
        self, symbol: str, timeframe: str, count: int = 0
    ) -> Optional[pd.DataFrame]:
        """
        Symbol ka OHLCV data fetch karo.

        Args:
            symbol: e.g. "XAU/USD", "SPX", "NDX"
            timeframe: e.g. "4h", "15min", "1day"
            count: Kitne candles chahiye (0 = default)

        Returns:
            DataFrame: open_time, open, high, low, close, volume
            None on error.
        """
        instrument = SYMBOL_MAP.get(symbol)
        if not instrument:
            log.warning(f"Symbol not mapped: {symbol}")
            return None

        granularity = GRANULARITY_MAP.get(timeframe)
        if not granularity:
            log.error(f"Invalid timeframe: {timeframe}")
            return None

        n = count or CANDLE_COUNT.get(timeframe, 200)
        self._wait_rate_limit()

        try:
            res = self._session.get(
                f"{BASE_URL}/instruments/{instrument}/candles",
                params={
                    "count":       n,
                    "granularity": granularity,
                    "price":       "M",   # Mid prices (bid+ask average)
                },
                timeout=15,
            )

            if res.status_code != 200:
                log.warning(f"OANDA error {symbol}/{timeframe}: {res.status_code} — {res.text[:200]}")
                return None

            data = res.json()
            candles = data.get("candles", [])
            if not candles:
                log.warning(f"No data returned for {symbol}/{timeframe}")
                return None

            rows = []
            for c in candles:
                if not c.get("complete", True):
                    continue  # Skip incomplete/live candle
                mid = c["mid"]
                rows.append({
                    "open_time": pd.to_datetime(c["time"]),
                    "open":  float(mid["o"]),
                    "high":  float(mid["h"]),
                    "low":   float(mid["l"]),
                    "close": float(mid["c"]),
                    "volume": float(c.get("volume", 0)),
                })

            if not rows:
                return None

            df = pd.DataFrame(rows).reset_index(drop=True)
            log.debug(f"Fetched {len(df)} candles: {symbol}/{timeframe}")
            return df

        except requests.exceptions.Timeout:
            log.error(f"Timeout fetching {symbol}/{timeframe}")
            return None
        except Exception as e:
            log.error(f"Error fetching {symbol}/{timeframe}: {e}")
            return None

    def get_current_price(self, symbol: str) -> Optional[float]:
        """Symbol ka latest mid price fetch karo."""
        instrument = SYMBOL_MAP.get(symbol)
        if not instrument:
            return None

        self._wait_rate_limit()
        try:
            res = self._session.get(
                f"{BASE_URL}/instruments/{instrument}/candles",
                params={"count": 1, "granularity": "M1", "price": "M"},
                timeout=10,
            )
            if res.status_code != 200:
                return None
            data = res.json()
            candles = data.get("candles", [])
            if candles:
                return float(candles[-1]["mid"]["c"])
        except Exception as e:
            log.error(f"Price fetch error {symbol}: {e}")
        return None

    def _wait_rate_limit(self) -> None:
        elapsed = time.time() - self._last_request_time
        if elapsed < self.REQUEST_DELAY:
            time.sleep(self.REQUEST_DELAY - elapsed)
        self._last_request_time = time.time()
