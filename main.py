"""
main.py — Forex Signal Bot
Twelve Data se data → OB detect → MSS confirm → Telegram alert
Free server par 24/7 chalta hai (Railway / Render / Koyeb)
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Dict, List, Tuple

import os
import yaml

from data_connector import DataConnector
from ob_detector import OrderBlockDetector
from ltf_confirmation import LTFConfirmationEngine
from chart_generator import generate_chart
from telegram_notifier import TelegramNotifier
from logger import get_logger

log = get_logger("main")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config(path: str = "config.yaml") -> dict:
    """
    Config load karo — Environment Variables ko priority dete hain.

    Railway par: Variables tab mein set karo
    Local PC par: config.yaml mein set karo

    Priority: ENV VARIABLE > config.yaml
    """
    # Load base config (pairs, cascades, strategy settings)
    p = Path(path)
    if p.exists():
        with p.open() as f:
            cfg = yaml.safe_load(f)
    else:
        # config.yaml nahi hai toh default structure banao
        cfg = {
            "pairs": ["XAU/USD","EUR/USD","GBP/USD","USD/JPY","AUD/USD","USD/CAD","GBP/JPY"],
            "cascades": [
                {"htf": "1week", "ltf": "4h"},
                {"htf": "1day",  "ltf": "1h"},
                {"htf": "4h",    "ltf": "15min"},
                {"htf": "1h",    "ltf": "5min"},
            ],
            "strategy": {
                "displacement_multiplier": 1.5,
                "min_displacement_candles": 2,
                "swing_pivot_bars": 2,
                "signal_timeout_minutes": 15,
                "ob_mitigation": "candle_close",
                "max_obs_per_pair": 5,
            },
            "scan_interval_seconds": 900,
            "chart": {"output_dir": "charts"},
            "telegram": {},
            "twelve_data": {},
        }

    # --- Environment Variables se credentials lo (Railway ke liye) ---
    env_token      = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    env_chat_id    = os.environ.get("TELEGRAM_CHAT_ID", "")
    env_oanda_tok  = os.environ.get("OANDA_API_TOKEN", "")
    env_oanda_acc  = os.environ.get("OANDA_ACCOUNT_ID", "")

    if env_token:
        cfg.setdefault("telegram", {})["bot_token"] = env_token
        log.info("Telegram token: environment variable se load hua")
    if env_chat_id:
        cfg.setdefault("telegram", {})["chat_id"] = env_chat_id
        log.info("Telegram chat_id: environment variable se load hua")
    if env_oanda_tok:
        cfg.setdefault("oanda", {})["api_token"] = env_oanda_tok
        log.info("OANDA token: environment variable se load hua")
    if env_oanda_acc:
        cfg.setdefault("oanda", {})["account_id"] = env_oanda_acc
        log.info("OANDA account_id: environment variable se load hua")

    # --- Validation ---
    tg = cfg.get("telegram", {})
    oa = cfg.get("oanda", {})

    token      = tg.get("bot_token", "")
    chat_id    = str(tg.get("chat_id", ""))
    oanda_tok  = oa.get("api_token", "")
    oanda_acc  = oa.get("account_id", "")

    errors = []
    if not token or "YOUR_" in token:
        errors.append("TELEGRAM_BOT_TOKEN — Railway Variables mein add karo")
    if not chat_id or "YOUR_" in chat_id:
        errors.append("TELEGRAM_CHAT_ID — Railway Variables mein add karo")
    if not oanda_tok or "YOUR_" in oanda_tok:
        errors.append("OANDA_API_TOKEN — Railway Variables mein add karo")
    if not oanda_acc or "YOUR_" in oanda_acc:
        errors.append("OANDA_ACCOUNT_ID — Railway Variables mein add karo")

    if errors:
        print("\n❌ Missing credentials:\n")
        for e in errors:
            print(f"   • {e}")
        print("\nRailway Dashboard → Service → Variables tab mein add karo\n")
        sys.exit(1)

    return cfg


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------

class SignalScanner:

    def __init__(self, cfg: dict, data: DataConnector, tg: TelegramNotifier) -> None:
        self._cfg  = cfg
        self._data = data
        self._tg   = tg
        self._strat = cfg.get("strategy", {})

        self._ob_det: Dict[Tuple[str, str], OrderBlockDetector] = {}
        self._ltf_eng: Dict[Tuple[str, str, str], LTFConfirmationEngine] = {}
        self._setup()

    def _setup(self) -> None:
        pairs     = self._cfg.get("pairs", [])
        cascades  = self._cfg.get("cascades", [])
        max_obs   = self._strat.get("max_obs_per_pair", 5)
        mitmode   = self._strat.get("ob_mitigation", "candle_close")
        timeout   = self._strat.get("signal_timeout_minutes", 15)

        for sym in pairs:
            for c in cascades:
                htf, ltf = c["htf"], c["ltf"]
                key_ob = (sym, htf)
                if key_ob not in self._ob_det:
                    self._ob_det[key_ob] = OrderBlockDetector(
                        symbol=sym, htf=htf,
                        max_obs=max_obs, mitigation_mode=mitmode,
                    )
                self._ltf_eng[(sym, htf, ltf)] = LTFConfirmationEngine(
                    symbol=sym, htf=htf, ltf=ltf,
                    signal_timeout_minutes=timeout,
                )
        log.info(f"Ready: {len(self._ob_det)} OB detectors, {len(self._ltf_eng)} LTF engines")

    def scan_once(self) -> None:
        pairs    = self._cfg.get("pairs", [])
        cascades = self._cfg.get("cascades", [])
        log.info(f"--- Scan started: {len(pairs)} pairs × {len(cascades)} cascades ---")

        for sym in pairs:
            for c in cascades:
                htf, ltf = c["htf"], c["ltf"]
                try:
                    self._scan_cascade(sym, htf, ltf)
                except Exception as e:
                    log.error(f"Error scanning {sym} {htf}→{ltf}: {e}")

        log.info("--- Scan complete ---")

    def _scan_cascade(self, sym: str, htf: str, ltf: str) -> None:
        detector = self._ob_det.get((sym, htf))
        engine   = self._ltf_eng.get((sym, htf, ltf))
        if not detector or not engine:
            return

        # HTF candles
        df_htf = self._data.get_candles(sym, htf)
        if df_htf is None or len(df_htf) < 15:
            log.warning(f"No HTF data: {sym}/{htf}")
            return

        # OB detection
        new_obs = detector.update(df_htf)
        for ob in new_obs:
            self._tg.send_ob_detected(sym, htf, ob.direction, ob.ob_low, ob.ob_high)

        # Price tap check
        current = self._data.get_current_price(sym)
        if current is None:
            return

        tapped = detector.check_tap(current)
        for ob in tapped:
            log.info(f"[TAP] {sym} @ {current:.5f} → {ob.direction.upper()} OB {htf}→{ltf}")
            engine.add_watch(ob)

        # LTF confirmation
        if engine.active_count() == 0:
            return

        df_ltf = self._data.get_candles(sym, ltf)
        if df_ltf is None or len(df_ltf) < 20:
            return

        signals = engine.process(df_ltf)
        for sig in signals:
            chart = generate_chart(
                df_ltf, sig,
                output_dir=self._cfg.get("chart", {}).get("output_dir", "charts"),
            )
            self._tg.send_signal(sig, chart)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    print("\n" + "═"*50)
    print("   Forex Signal Bot — HTF OB + LTF MSS")
    print("   Twelve Data | Telegram Alerts")
    print("═"*50 + "\n")

    # Dirs
    for d in ["state", "logs", "charts"]:
        Path(d).mkdir(exist_ok=True)

    cfg = load_config("config.yaml")

    # Telegram
    tg_cfg = cfg["telegram"]
    tg = TelegramNotifier(tg_cfg["bot_token"], str(tg_cfg["chat_id"]))
    log.info("Testing Telegram...")
    if not tg.test_connection():
        print("\n❌ Telegram connect nahi hua. Token aur chat_id check karo!\n")
        sys.exit(1)

    # OANDA
    oa_cfg = cfg["oanda"]
    data = DataConnector(
        api_token=oa_cfg["api_token"],
        account_id=oa_cfg["account_id"],
        demo=oa_cfg.get("demo", True),
    )
    log.info("Testing OANDA...")
    if not data.test_connection():
        print("\n❌ OANDA connect nahi hua. API token aur Account ID check karo!\n")
        sys.exit(1)

    # Scanner
    scanner = SignalScanner(cfg, data, tg)

    # Startup alert
    pairs    = cfg.get("pairs", [])
    cascades = [f"{c['htf']}→{c['ltf']}" for c in cfg.get("cascades", [])]
    tg.send_startup(pairs, cascades)

    interval = cfg.get("scan_interval_seconds", 900)
    log.info(f"Bot live! Scan interval: {interval}s ({interval//60} min)")

    # Main loop
    try:
        while True:
            try:
                scanner.scan_once()
            except Exception as e:
                log.error(f"Scan error: {e}", exc_info=True)
                tg.send_error("scan_once", str(e))
            log.info(f"Next scan in {interval}s...")
            time.sleep(interval)
    except KeyboardInterrupt:
        log.info("Bot stopped (Ctrl+C)")


if __name__ == "__main__":
    main()
