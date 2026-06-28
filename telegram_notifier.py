"""telegram_notifier.py — Telegram par signal alerts bhejta hai."""

from __future__ import annotations
import os
from pathlib import Path
from typing import Optional
import requests
from ltf_confirmation import Signal
from logger import get_logger
log = get_logger(__name__)


class TelegramNotifier:
    BASE = "https://api.telegram.org/bot"

    def __init__(self, bot_token: str, chat_id: str) -> None:
        self._token = bot_token
        self._chat_id = chat_id
        self._api = f"{self.BASE}{bot_token}"

    def test_connection(self) -> bool:
        try:
            r = requests.get(f"{self._api}/getMe", timeout=10).json()
            if r.get("ok"):
                log.info(f"Telegram OK: @{r['result']['username']}")
                return True
        except Exception as e:
            log.error(f"Telegram test fail: {e}")
        return False

    def send_startup(self, pairs: list, cascades: list) -> None:
        pairs_str = "\n".join(f"   • `{p}`" for p in pairs)
        casc_str  = "\n".join(f"   • `{c}`" for c in cascades)
        self._text(
            f"🤖 *Forex Signal Bot Started!*\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"📌 *Pairs:*\n{pairs_str}\n\n"
            f"🕯 *Cascades:*\n{casc_str}\n\n"
            f"🟢 Scanning every 15 minutes..."
        )

    def send_ob_detected(self, symbol: str, htf: str, direction: str, ob_low: float, ob_high: float) -> None:
        emoji = "🟢" if direction == "bullish" else "🔴"
        self._text(
            f"{emoji} *New {direction.upper()} OB*\n"
            f"📊 `{symbol}` | `{htf}`\n"
            f"📦 Zone: `{ob_low:.5f} – {ob_high:.5f}`\n"
            f"👀 Watching for LTF tap..."
        )

    def send_signal(self, sig: Signal, chart_path: Optional[str] = None) -> None:
        arrow  = "▲" if sig.direction == "long" else "▼"
        dlabel = "LONG  🟢" if sig.direction == "long" else "SHORT 🔴"
        pip    = 0.01 if "XAU" in sig.symbol or "JPY" in sig.symbol else 0.0001
        risk_p = abs(sig.entry_price - sig.sl_price) / pip
        rwd_p  = abs(sig.tp2 - sig.entry_price) / pip

        msg = (
            f"🚨 *MSS SIGNAL* {arrow}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 *Pair:*      `{sig.symbol}`\n"
            f"📈 *Direction:* `{dlabel}`\n"
            f"🕯 *Cascade:*   `{sig.cascade_label}`\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 *Entry:*     `{sig.entry_price:.5f}`\n"
            f"🛑 *SL:*        `{sig.sl_price:.5f}`\n"
            f"🎯 *TP1 (1:2):* `{sig.tp1:.5f}`\n"
            f"🎯 *TP2 (1:3):* `{sig.tp2:.5f}`\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📐 *Risk:*    `{risk_p:.0f} pips`\n"
            f"💵 *Reward:*  `{rwd_p:.0f} pips`\n"
            f"📦 *FVG:*     `{sig.fvg_low:.5f} – {sig.fvg_high:.5f}`\n"
            f"🔰 *MSS:*     `{sig.mss_level:.5f}`\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⚠️ _Signal only — verify before trading_"
        )
        if chart_path and Path(chart_path).exists():
            self._photo(msg, chart_path)
            try: os.remove(chart_path)
            except: pass
        else:
            self._text(msg)

    def send_error(self, ctx: str, err: str) -> None:
        self._text(f"🆘 *Error*\n`{ctx}`\n`{err[:200]}`")

    def _text(self, text: str) -> None:
        try:
            r = requests.post(f"{self._api}/sendMessage", json={
                "chat_id": self._chat_id, "text": text,
                "parse_mode": "Markdown", "disable_web_page_preview": True,
            }, timeout=15)
            if not r.json().get("ok"):
                log.error(f"Telegram fail: {r.text}")
        except Exception as e:
            log.error(f"Telegram error: {e}")

    def _photo(self, caption: str, path: str) -> None:
        try:
            with open(path, "rb") as f:
                r = requests.post(f"{self._api}/sendPhoto", data={
                    "chat_id": self._chat_id, "caption": caption, "parse_mode": "Markdown",
                }, files={"photo": f}, timeout=30)
            if not r.json().get("ok"):
                self._text(caption + "\n\n⚠️ _Chart unavailable_")
        except Exception as e:
            log.error(f"Photo error: {e}")
            self._text(caption)
