"""
Tests for the Round-2 reliability improvements (SPEC.md AC8..AC14).

Run from the repo root:  python -m pytest tests/ -v
"""

from __future__ import annotations

from typing import Optional

from ob_detector import OrderBlock, OrderBlockDetector


def make_bull_ob(ob_id: str = "XAU/USD_1h_bull_T1") -> OrderBlock:
    """Bullish OB: body zone [104, 105], protective wick low 103."""
    return OrderBlock(
        ob_id=ob_id, symbol="XAU/USD", htf="1h", direction="bullish",
        ob_high=105.0, ob_low=104.0, wick_high=105.5, wick_low=103.0,
        candle_time="2026-07-23T10:00:00",
    )


# --------------------------------------------------------------------------- #
# AC8 — range-based tap (catches wicks the price sample misses)
# --------------------------------------------------------------------------- #

def test_ac8_range_tap_when_price_outside_zone():
    det = OrderBlockDetector("XAU/USD", "1h", store=None)
    det._obs = [make_bull_ob()]  # zone [104, 105]
    # Price 106 is OUTSIDE the zone, but the candle wicked down INTO it (low 104.5
    # sits inside the zone) without engulfing it → a genuine tap.
    tapped = det.check_tap(106.0, candle_low=104.5, candle_high=106.0)
    assert [o.ob_id for o in tapped] == ["XAU/USD_1h_bull_T1"]


def test_ac8_no_tap_when_neither_price_nor_range_touches():
    det = OrderBlockDetector("XAU/USD", "1h", store=None)
    det._obs = [make_bull_ob()]  # zone [104, 105]
    # Price above the zone and the candle range entirely above it.
    assert det.check_tap(106.0, candle_low=105.5, candle_high=107.0) == []


def test_ac8_price_only_tap_still_works_without_candle():
    det = OrderBlockDetector("XAU/USD", "1h", store=None)
    det._obs = [make_bull_ob()]
    assert len(det.check_tap(104.5)) == 1  # price inside zone, no candle given


# --------------------------------------------------------------------------- #
# AC9 — OANDA transient retry
# --------------------------------------------------------------------------- #

class _FakeResp:
    def __init__(self, status, payload=None, text="", headers=None):
        self.status_code = status
        self._payload = payload or {}
        self.text = text
        self.headers = headers or {}

    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0
        self.headers = {}

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        return self._responses.pop(0)


def test_ac9_retry_on_5xx_then_success(monkeypatch):
    import data_connector
    monkeypatch.setattr(data_connector.time, "sleep", lambda *a, **k: None)

    conn = data_connector.DataConnector("token")
    ok_payload = {"candles": [
        {"complete": True, "time": "2026-07-01T00:00:00Z",
         "mid": {"o": "100", "h": "101", "l": "99", "c": "100.5"}, "volume": 10},
    ]}
    fake = _FakeSession([_FakeResp(500, text="server error"),
                         _FakeResp(200, ok_payload)])
    monkeypatch.setattr(conn, "_session", fake)

    df = conn.get_candles("XAU/USD", "1h")
    assert df is not None
    assert len(df) == 1
    assert fake.calls == 2  # first failed, retried once


def test_ac9_returns_none_after_exhausting_retries(monkeypatch):
    import data_connector
    monkeypatch.setattr(data_connector.time, "sleep", lambda *a, **k: None)

    conn = data_connector.DataConnector("token")
    # More failures than MAX_RETRIES + 1 attempts.
    fake = _FakeSession([_FakeResp(500) for _ in range(5)])
    monkeypatch.setattr(conn, "_session", fake)

    assert conn.get_candles("XAU/USD", "1h") is None
    assert fake.calls == data_connector.DataConnector.MAX_RETRIES + 1


# --------------------------------------------------------------------------- #
# AC11 / AC12 — Telegram: Markdown-safe errors + real startup interval
# --------------------------------------------------------------------------- #

class _CapturePost:
    def __init__(self):
        self.payload: Optional[dict] = None

    def __call__(self, url, json=None, timeout=None):
        self.payload = json
        return _FakeResp(200, {"ok": True})


def test_ac11_send_error_has_no_parse_mode(monkeypatch):
    import telegram_notifier
    cap = _CapturePost()
    monkeypatch.setattr(telegram_notifier.requests, "post", cap)

    tg = telegram_notifier.TelegramNotifier("token", "chat")
    tg.send_error("scan_once", "boom with _under_ *stars* [brackets]")

    assert cap.payload is not None
    assert "parse_mode" not in cap.payload  # plain text, cannot 400 on metachars


def test_ac12_startup_shows_configured_interval(monkeypatch):
    import telegram_notifier
    cap = _CapturePost()
    monkeypatch.setattr(telegram_notifier.requests, "post", cap)

    tg = telegram_notifier.TelegramNotifier("token", "chat")
    tg.send_startup(["XAU/USD"], ["1h→5min"], interval_minutes=5)

    assert cap.payload is not None
    assert "every 5 minutes" in cap.payload["text"]


# --------------------------------------------------------------------------- #
# AC14 — in-code fallback config matches config.yaml
# --------------------------------------------------------------------------- #

def test_ac14_fallback_config_matches_yaml(monkeypatch):
    import main
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    monkeypatch.setenv("OANDA_API_TOKEN", "y")

    cfg = main.load_config("does_not_exist_config.yaml")
    assert cfg["pairs"] == ["XAU/USD", "SPX", "NDX"]
    assert cfg["scan_interval_seconds"] == 300
    assert {(c["htf"], c["ltf"]) for c in cfg["cascades"]} == {
        ("4h", "15min"), ("2h", "30min"), ("1h", "5min"),
    }
