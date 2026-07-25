# Changelog

All notable changes to this project are documented here.
Format based on [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased] — Watch window scales with the lower timeframe

### Changed

- **A tapped order block now gets 3 *lower-timeframe candles* to confirm**, not a
  flat 15 minutes. The window therefore differs per cascade: `5min` → 15 min,
  `15min` → 45 min, `30min` → 90 min. The old flat window gave the 15min cascade
  a single candle and the 30min cascade less than one, so those cascades could
  effectively never confirm before the watch expired.
- **Config key renamed:** `strategy.signal_timeout_minutes` →
  `strategy.signal_timeout_candles` (default `3`). The resolved windows are
  logged at startup (`Watch timeout = 3 LTF candles → 5min: 15min, …`).

### Migration

- Replace `signal_timeout_minutes: 15` with `signal_timeout_candles: 3` in
  `config.yaml`. No environment variables change.

## [Unreleased] — LTF tap fix + SQLite state persistence

### Fixed

- **LTF tap alerts never fired; order blocks stayed stuck on "Watching for LTF
  tap..." forever.** Three independent root causes:
  1. **Missing method swallowed every tap.** `main.py` called
     `engine.is_watching(...)`, which `LTFConfirmationEngine` did not define.
     The resulting `AttributeError` was caught by `scan_once`'s broad
     `except Exception`, so `send_tap_alert` and `add_watch` never ran. Added
     `LTFConfirmationEngine.is_watching()`.
  2. **Mitigation pre-empted the tap.** An order block was marked mitigated as
     soon as a candle *closed inside* its zone — but that close *is* the tap.
     `check_tap` then excluded it, so the OB was retired before it could tap
     (and the freed slot produced the endless "New OB" spam). Retirement is now
     **invalidation**: an OB is retired only when price **closes beyond its
     protective wick**; a close inside the zone arms the LTF watch instead.
  3. **State was fragile.** Order blocks lived in a hand-rolled JSON file and
     LTF watches lived only in memory (lost on any restart).

### Changed

- **State is now stored in SQLite** (`state_store.py`, standard-library
  `sqlite3` — no new dependency). Order blocks *and* pending LTF watches live in
  one transactional database (`state/bot.db`, path overridable via
  `STATE_DB_PATH`). Replaces the previous `state/ob_state.json`.
- **Tap latching is decoupled from the alert.** The LTF watch is armed and the
  OB latched *before* the (best-effort) Telegram alert, so a transient send
  failure can no longer strand an OB tapped-but-unwatched.
- `config.yaml` `strategy.ob_mitigation` is **ignored** (kept for reference).
  Retirement is now always "close beyond the protective wick".
- Added `.gitignore` (excludes `__pycache__/`, `logs/`, `state/`, `*.db`, …).

### Added

- `SPEC.md` — acceptance criteria (AC1–AC7) for this fix.
- `tests/test_bot.py` — 15 tests covering the tap/invalidation state machine and
  SQLite persistence (run with `python -m pytest tests/`).

### Migration notes

- The old `state/ob_state.json` is no longer read; the SQLite store starts fresh
  on first run. Order blocks self-heal by re-detection on the next scan.
- No dependency or environment changes required. Optionally set `STATE_DB_PATH`
  to a mounted volume to make state survive restarts.

## [Unreleased] — Wick-to-wick order-block zones

### Changed

- **Order-block zone is now the candle's full range (wick to wick)**, not just
  its body. The previous body-only zone was a thin sliver (e.g. ~5 points on an
  NDX 1h block whose full range was ~90 points), so it rarely matched where the
  block actually sits. "New OB" messages and charts now show the full zone.
- **A candle that blows clean through a zone no longer counts as a tap.** The
  range-based tap (added earlier) fired whenever a candle's range overlapped the
  zone — including a large candle that crashed straight through it. Such a candle
  breaks the level rather than tapping it, so it is now excluded; a wick that
  enters the zone without engulfing it still taps.
