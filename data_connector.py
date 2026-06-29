"""
data_connector.py — Finnhub API se Forex/Gold/Crypto candles fetch karta hai.

Free tier: 60 calls/minute — practically unlimited.
Real-time data, zero delay.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
import requests

from logger import get_logger

log = get_logger(__name__)

BASE_URL = "https://finnhub.io/api/v1"

# Finnhub symbol mapping
SYMBOL_MAP = {
    "XAU/USD": "OANDA:XAU_USD",
    "EUR/USD": "OANDA:EUR_USD",
    "GBP/USD": "OANDA:GBP_USD",
    "USD/JPY": "OANDA:USD_JPY",
    "AUD/USD": "OANDA:AUD_USD",
    "USD/CAD": "OANDA:USD_CAD",
    "GBP/JPY": "OANDA:GBP_JPY",
    "BTC/USD": "BINANCE:BTCUSDT",
    "SPX":     "OANDA:SPX500_USD",
    "NDX":     "OANDA:NAS100_USD",
}

# Finnhub resolution mapping
RESOLUTION_MAP = {
    "5min":  "5",
    "15min": "15",
    "30min": "30",
    "1h":    "60",
    "2h":    "120",
    "4h":    "240",
    "1day":  "D",
    "1week": "W",
}

# Candle count per timeframe
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

# Seconds per timeframe (for timestamp calculation)
TF_SECONDS = {
    "5min":  300,
    "15min": 900,
    "30min": 1800,
    "1h":    3600,
    "2h":    7200,
    "4h":    14400,
    "1day":  86400,
    "1week": 604800,
}


class DataConnector:
    """
    Finnhub REST API wrapper.

    Usage:
        conn = DataConnector(api_key="your_key")
        df = conn.get_candles("XAU/USD", "4h")
    """

    REQUEST_DELAY = 1.0  # 1 second between requests (safe for 60/min limit)

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._last_request_time = 0.0
        self._session = requests.Session()
        self._session.headers.update({
            "X-Finnhub-Token": api_key,
            "User-Agent": "ForexSignalBot/1.0",
        })

    def test_connection(self) -> bool:
        """API key valid hai? Check karo."""
        try:
            res = self._session.get(
                f"{BASE_URL}/forex/exchange",
                timeout=10,
            ).json()
            if isinstance(res, list) and len(res) > 0:
                log.info("Finnhub connected ✅")
                return True
            log.error(f"Finnhub test fail: {res}")
            return False
        except Exception as e:
            log.error(f"Finnhub connection error: {e}")
            return False

    def get_candles(
        self, symbol: str, timeframe: str, count: int = 0
    ) -> Optional[pd.DataFrame]:
        """
        Symbol ka OHLCV data fetch karo.

        Args:
            symbol: e.g. "XAU/USD", "BTC/USD", "SPX"
            timeframe: e.g. "4h", "15min", "1day"
            count: Kitne candles chahiye (0 = default)

        Returns:
            DataFrame: open_time, open, high, low, close, volume
            None on error.
        """
        finnhub_symbol = SYMBOL_MAP.get(symbol)
        if not finnhub_symbol:
            log.warning(f"Symbol not mapped: {symbol}")
            return None

        resolution = RESOLUTION_MAP.get(timeframe)
        if not resolution:
            log.error(f"Invalid timeframe: {timeframe}")
            return None

        n = count or CANDLE_COUNT.get(timeframe, 200)
        tf_secs = TF_SECONDS.get(timeframe, 3600)

        # Timestamp range
        t_to   = int(datetime.utcnow().timestamp())
        t_from = t_to - (n * tf_secs)

        self._wait_rate_limit()

        try:
            res = self._session.get(
                f"{BASE_URL}/forex/candle",
                params={
                    "symbol": finnhub_symbol,
                    "resolution": resolution,
                    "from": t_from,
                    "to": t_to,
                },
                timeout=15,
            ).json()

            # Crypto uses different endpoint
            if res.get("s") == "no_data" or not res.get("t"):
                # Try stock candle endpoint for indices/crypto
                self._wait_rate_limit()
                res = self._session.get(
                    f"{BASE_URL}/stock/candle",
                    params={
                        "symbol": finnhub_symbol,
                        "resolution": resolution,
                        "from": t_from,
                        "to": t_to,
                    },
                    timeout=15,
                ).json()

            if res.get("s") == "no_data" or not res.get("t"):
                log.warning(f"No data: {symbol}/{timeframe}")
                return None

            df = pd.DataFrame({
                "open_time": pd.to_datetime(res["t"], unit="s"),
                "open":      res["o"],
                "high":      res["h"],
                "low":       res["l"],
                "close":     res["c"],
                "volume":    res.get("v", [0] * len(res["t"])),
            })

            df = df.reset_index(drop=True)
            log.debug(f"Fetched {len(df)} candles: {symbol}/{timeframe}")
            return df

        except requests.exceptions.Timeout:
            log.error(f"Timeout: {symbol}/{timeframe}")
            return None
        except Exception as e:
            log.error(f"Error fetching {symbol}/{timeframe}: {e}")
            return None

    def get_current_price(self, symbol: str) -> Optional[float]:
        """Symbol ka latest price fetch karo."""
        finnhub_symbol = SYMBOL_MAP.get(symbol)
        if not finnhub_symbol:
            return None

        self._wait_rate_limit()
        try:
            res = self._session.get(
                f"{BASE_URL}/quote",
                params={"symbol": finnhub_symbol},
                timeout=10,
            ).json()
            price = res.get("c")  # current price
            if price and float(price) > 0:
                return float(price)
        except Exception as e:
            log.error(f"Price fetch error {symbol}: {e}")
        return None

    def _wait_rate_limit(self) -> None:
        """Rate limit respect karo."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self.REQUEST_DELAY:
            time.sleep(self.REQUEST_DELAY - elapsed)
        self._last_request_time = time.time()
