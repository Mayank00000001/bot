"""
data_connector.py — OANDA API se real-time Forex/Gold candles fetch karta hai.

OANDA advantages:
- Institutional grade data
- Zero delay — real-time
- Unlimited API calls (free demo account)
- Best Forex/Gold data quality
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
import requests

from logger import get_logger

log = get_logger(__name__)

# OANDA API endpoints
PRACTICE_URL = "https://api-fxtrade.oanda.com/v3"   # Live account
DEMO_URL     = "https://api-fxpractice.oanda.com/v3" # Demo account

# OANDA instrument mapping
SYMBOL_MAP = {
    "XAU/USD": "XAU_USD",
    "EUR/USD": "EUR_USD",
    "GBP/USD": "GBP_USD",
    "USD/JPY": "USD_JPY",
    "AUD/USD": "AUD_USD",
    "USD/CAD": "USD_CAD",
    "GBP/JPY": "GBP_JPY",
    "EUR/JPY": "EUR_JPY",
    "BTC/USD": "BTC_USD",
    "SPX":     "SPX500_USD",
    "NDX":     "NAS100_USD",
    "US30":    "US30_USD",
}

# OANDA granularity mapping
RESOLUTION_MAP = {
    "1min":  "M1",
    "5min":  "M5",
    "15min": "M15",
    "30min": "M30",
    "1h":    "H1",
    "2h":    "H2",
    "4h":    "H4",
    "1day":  "D",
    "1week": "W",
    "1month":"M",
}

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
    OANDA v20 REST API wrapper.

    Usage:
        conn = DataConnector(
            api_token="v2xxx...",
            account_id="12345678",
            demo=True
        )
        df = conn.get_candles("XAU/USD", "4h")
    """

    REQUEST_DELAY = 0.2  # 200ms between requests — OANDA allows 100 req/sec

    def __init__(
        self,
        api_token: str,
        account_id: str,
        demo: bool = True,
    ) -> None:
        self._token      = api_token
        self._account_id = account_id
        self._base_url   = DEMO_URL if demo else PRACTICE_URL
        self._last_req   = 0.0

        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {api_token}",
            "Content-Type":  "application/json",
            "Accept-Datetime-Format": "UNIX",
        })

    def test_connection(self) -> bool:
        """API token valid hai? Account check karo."""
        try:
            res = self._session.get(
                f"{self._base_url}/accounts/{self._account_id}/summary",
                timeout=10,
            ).json()

            if "account" in res:
                acc = res["account"]
                log.info(
                    f"OANDA connected ✅ | "
                    f"Account: {acc.get('id')} | "
                    f"Balance: {acc.get('balance')} {acc.get('currency')}"
                )
                return True

            log.error(f"OANDA test fail: {res}")
            return False

        except Exception as e:
            log.error(f"OANDA connection error: {e}")
            return False

    def get_candles(
        self, symbol: str, timeframe: str, count: int = 0
    ) -> Optional[pd.DataFrame]:
        """
        OANDA se OHLCV candles fetch karo.

        Args:
            symbol: e.g. "XAU/USD", "EUR/USD", "BTC/USD"
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

        granularity = RESOLUTION_MAP.get(timeframe)
        if not granularity:
            log.error(f"Invalid timeframe: {timeframe}")
            return None

        n = count or CANDLE_COUNT.get(timeframe, 200)

        self._wait_rate_limit()

        try:
            res = self._session.get(
                f"{self._base_url}/instruments/{instrument}/candles",
                params={
                    "count":       n,
                    "granularity": granularity,
                    "price":       "M",  # Mid price (ask+bid / 2)
                },
                timeout=15,
            ).json()

            if "candles" not in res:
                log.warning(f"No candles: {symbol}/{timeframe} — {res}")
                return None

            candles = [c for c in res["candles"] if c.get("complete", True)]

            if not candles:
                log.warning(f"No complete candles: {symbol}/{timeframe}")
                return None

            rows = []
            for c in candles:
                mid = c.get("mid", {})
                rows.append({
                    "open_time": pd.to_datetime(float(c["time"]), unit="s"),
                    "open":      float(mid.get("o", 0)),
                    "high":      float(mid.get("h", 0)),
                    "low":       float(mid.get("l", 0)),
                    "close":     float(mid.get("c", 0)),
                    "volume":    int(c.get("volume", 0)),
                })

            df = pd.DataFrame(rows).reset_index(drop=True)
            log.debug(f"Fetched {len(df)} candles: {symbol}/{timeframe}")
            return df

        except requests.exceptions.Timeout:
            log.error(f"Timeout: {symbol}/{timeframe}")
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
                f"{self._base_url}/instruments/{instrument}/candles",
                params={
                    "count":       1,
                    "granularity": "S5",  # 5-second candle — latest price
                    "price":       "M",
                },
                timeout=10,
            ).json()

            candles = res.get("candles", [])
            if candles:
                return float(candles[-1]["mid"]["c"])

        except Exception as e:
            log.error(f"Price fetch error {symbol}: {e}")
        return None

    def _wait_rate_limit(self) -> None:
        elapsed = time.time() - self._last_req
        if elapsed < self.REQUEST_DELAY:
            time.sleep(self.REQUEST_DELAY - elapsed)
        self._last_req = time.time()
