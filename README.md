# Forex Signal Bot 🚨

HTF Order Block → LTF confirmation (Displacement → FVG → MSS) signal bot.
Fetches live market data from **OANDA** and sends alerts to **Telegram**.
Runs 24/7 on a free host (Railway / Render / Koyeb).

> ⚠️ Signals only — always verify on the chart before trading.

---

## What it does

1. Detects HTF **Order Blocks** (bullish/bearish) on each configured pair.
2. Watches for price to **tap** the OB zone → posts an *OB TAPPED* alert and
   arms a lower-timeframe (LTF) watch.
3. On the LTF, confirms **Displacement → FVG → MSS** within a timeout window.
4. On confirmation, sends a **signal** (entry / SL / TP1 / TP2) with a chart.

Alert sequence per setup: `New OB` → `OB TAPPED` → `MSS SIGNAL`.

## Data source & credentials

Data comes from the **OANDA practice API** (free, real-time). Set three
environment variables (Railway → Service → **Variables**), or fill `config.yaml`
locally:

| Variable | Where to get it |
|----------|-----------------|
| `OANDA_API_TOKEN` | OANDA practice account → Manage API Access |
| `TELEGRAM_BOT_TOKEN` | @BotFather |
| `TELEGRAM_CHAT_ID` | @userinfobot (or your channel ID) |

Environment variables take priority over `config.yaml`.

## Configuration (`config.yaml`)

- `pairs` — instruments to scan (e.g. `XAU/USD`, `SPX`, `NDX`).
- `cascades` — HTF→LTF pairs (e.g. `4h→15min`, `1h→5min`).
- `scan_interval_seconds` — how often to scan (default 300 = 5 min).
- `strategy.signal_timeout_minutes` — how long a tapped OB waits for MSS.
- `strategy.max_obs_per_pair` — active OBs kept per pair/timeframe.
- `strategy.ob_mitigation` — **ignored** (kept for reference). Order blocks are
  retired only when price *closes beyond the protective wick* (see below).

## How order blocks are retired (important)

- A candle closing **inside** the OB zone is a **tap** — it arms the watch, it
  does **not** kill the OB.
- An OB is **invalidated** (retired) only when a candle **closes beyond its
  protective wick** (bullish: below the wick low; bearish: above the wick high).

## State persistence

Order blocks and pending LTF watches are stored in a single SQLite database
(`state/bot.db`, standard-library `sqlite3` — no extra dependency). The path is
configurable via the `STATE_DB_PATH` environment variable.

**Durability:** on an ephemeral host filesystem (e.g. a Railway container
*without* a mounted volume) this DB is wiped on every restart — it is *in-run*
persistence. That is fine for a process that runs continuously. To make state
survive restarts, mount a volume and point `STATE_DB_PATH` at a file on it — **no
code change required**.

## Deploy on Railway (free)

1. Push this repo to GitHub.
2. Railway → *New Project* → *Deploy from GitHub repo*.
3. Add the three environment variables above.
4. The bot deploys and starts (`Procfile`: `worker: python main.py`).
5. Watch the logs for `Bot live!` and a Telegram startup message.

## Run / test locally

```bash
pip install -r requirements.txt
python -m pytest tests/ -v      # 15 tests (state + tap/invalidation logic)
python main.py                  # needs the three env vars / config.yaml
```

## Handover notes

- **This deploy runs one process continuously** (~1 month on Railway free tier,
  then migrated to a fresh account). It is **not** restarted otherwise.
- Each migration/redeploy resets the in-run state: order blocks re-detect
  themselves on the next scan; only *in-flight* LTF watches are lost (rare, once
  per migration). If that ever matters, add a Railway volume (see *Durability*).
- `ob_mitigation` in `config.yaml` no longer has any effect.

## Common errors

| Error | Fix |
|-------|-----|
| `Telegram connect nahi hua` | Check `TELEGRAM_BOT_TOKEN`; send your bot a message first |
| `OANDA connect nahi hua` | Check `OANDA_API_TOKEN` (practice account) |
| `No HTF data` | Check the symbol mapping in `data_connector.py` (`XAU/USD` → `XAU_USD`) |
| Railway crash loop | Check the logs → are the three variables set correctly? |
