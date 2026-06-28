"""
data_connector.py — Twelve Data API se Forex/Gold candles fetch karta hai.

Free tier: 800 API calls/day, 8 calls/minute.
Har request ke baad rate limit respect karta hai.
"""

from __future__ import annotations

import time
from typing import Optional

import pandas as pd
import requests

from logger import get_logger

log = get_logger(__name__)

BASE_URL = "https://api.twelvedata.com"

# Twelve Data timeframe mapping
TF_MAP = {
    "5min":  "5min",
    "15min": "15min",
    "30min": "30min",
    "1h":    "1h",
    "4h":    "4h",
    "1day":  "1day",
    "1week": "1week",
}

# Kitne candles chahiye har timeframe ke liye
CANDLE_COUNT = {
    "1week": 100,
    "1day":  200,
    "4h":    300,
    "1h":    300,
    "15min": 300,
    "5min":  300,
}


class DataConnector:
    """
    Twelve Data REST API wrapper.

    Usage:
        conn = DataConnector(api_key="your_key")
        df = conn.get_candles("XAU/USD", "4h")
    """

    # Free tier: 8 requests/minute → 1 request har 8 seconds
    REQUEST_DELAY = 8.0

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._last_request_time = 0.0
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "ForexSignalBot/1.0"})

    def test_connection(self) -> bool:
        """API key valid hai? Check karo."""
        try:
            res = self._session.get(
                f"{BASE_URL}/api_usage",
                params={"apikey": self._api_key},
                timeout=10,
            ).json()
            if "current_usage" in res:
                used  = res["current_usage"]
                limit = res["plan_limit"]
                log.info(f"Twelve Data connected — API usage: {used}/{limit} today")
                return True
            log.error(f"Twelve Data test fail: {res}")
            return False
        except Exception as e:
            log.error(f"Twelve Data connection error: {e}")
            return False

    def get_candles(
        self, symbol: str, timeframe: str, count: int = 0
    ) -> Optional[pd.DataFrame]:
        """
        Symbol ka OHLCV data fetch karo.

        Args:
            symbol: e.g. "XAU/USD", "EUR/USD"
            timeframe: e.g. "4h", "15min", "1day"
            count: Kitne candles chahiye (0 = default)

        Returns:
            DataFrame: open_time, open, high, low, close, volume
            None on error.
        """
        tf = TF_MAP.get(timeframe)
        if not tf:
            log.error(f"Invalid timeframe: {timeframe}. Valid: {list(TF_MAP.keys())}")
            return None

        n = count or CANDLE_COUNT.get(timeframe, 200)

        # Rate limit respect karo
        self._wait_rate_limit()

        try:
            res = self._session.get(
                f"{BASE_URL}/time_series",
                params={
                    "symbol":     symbol,
                    "interval":   tf,
                    "outputsize": n,
                    "apikey":     self._api_key,
                    "order":      "ASC",   # Purane pehle, naye baad
                },
                timeout=15,
            ).json()

            if res.get("status") == "error":
                log.warning(f"API error for {symbol}/{timeframe}: {res.get('message')}")
                return None

            values = res.get("values", [])
            if not values:
                log.warning(f"No data returned for {symbol}/{timeframe}")
                return None

            df = pd.DataFrame(values)
            df["open_time"] = pd.to_datetime(df["datetime"])
            for col in ["open", "high", "low", "close"]:
                df[col] = df[col].astype(float)

            # Volume — Gold/Forex mein always 0 hota hai Twelve Data par
            df["volume"] = df.get("volume", pd.Series([0.0] * len(df))).astype(float)

            df = df[["open_time", "open", "high", "low", "close", "volume"]]
            df = df.reset_index(drop=True)

            log.debug(f"Fetched {len(df)} candles: {symbol}/{timeframe}")
            return df

        except requests.exceptions.Timeout:
            log.error(f"Timeout fetching {symbol}/{timeframe}")
            return None
        except Exception as e:
            log.error(f"Error fetching {symbol}/{timeframe}: {e}")
            return None

    def get_current_price(self, symbol: str) -> Optional[float]:
        """Symbol ka latest price fetch karo."""
        self._wait_rate_limit()
        try:
            res = self._session.get(
                f"{BASE_URL}/price",
                params={"symbol": symbol, "apikey": self._api_key},
                timeout=10,
            ).json()
            price = res.get("price")
            if price:
                return float(price)
        except Exception as e:
            log.error(f"Price fetch error {symbol}: {e}")
        return None

    def _wait_rate_limit(self) -> None:
        """Free tier rate limit: 8 seconds between requests."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self.REQUEST_DELAY:
            wait = self.REQUEST_DELAY - elapsed
            log.debug(f"Rate limit wait: {wait:.1f}s")
            time.sleep(wait)
        self._last_request_time = time.time()
