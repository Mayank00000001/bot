# SPEC — LTF Tap Alert Fix + State Persistence

## Intent

The deployed forex signal bot (HTF Order Block → LTF MSS) stopped reporting the
LTF tap step: order blocks stay stuck on "Watching for LTF tap..." forever and
the "OB TAPPED" alert never fires. Fix the tap→watch→MSS pipeline so tap alerts
and downstream signals work again, and consolidate the OB + watch state into a
single transactional SQLite store (in-run persistence; durability-ready via a
configurable DB path).

## Root cause (confirmed by code reading)

1. **Missing method silently swallows the tap.** `main.py` calls
   `engine.is_watching(ob.ob_id)` in the tap loop, but `LTFConfirmationEngine`
   defines no `is_watching`. The moment `check_tap` returns any OB, this raises
   `AttributeError`, which the broad `except Exception` in `scan_once` swallows —
   so `send_tap_alert` and `add_watch` never run. Deterministic tap killer.
2. **Mitigation pre-empts tap.** `OrderBlockDetector._check_mitigation` marks an
   OB `is_mitigated=True` as soon as an HTF candle *closes inside the zone*
   (`ob_mitigation="candle_close"`). `check_tap` then excludes mitigated OBs. But
   a close inside the zone *is* the tap event — so the OB is retired before it can
   tap. This also drives the endless "New OB" spam (retired OBs free a slot, the
   scanner promotes the next candidate every scan).
3. **Watches are RAM-only.** `LTFConfirmationEngine._watches` is never persisted.
4. **State file is fragile.** OBs live in a hand-rolled JSON file; watches live
   nowhere. No single source of truth.

## Acceptance Criteria (binary testable)

- [x] **AC1 — tap not gated by mitigation:** When price is inside an active OB's
  zone, `check_tap(price)` returns that OB **even if an HTF candle has closed
  inside the zone**. Test: unit test — OB zone contains price AND last HTF close
  inside zone → `check_tap` returns the OB (before fix: empty list).
- [x] **AC2 — mitigation redefined to invalidation:** An OB is retired only when
  an HTF candle **closes beyond its protective wick** (bullish: `close < wick_low`;
  bearish: `close > wick_high`). A close *inside* the zone does **not** retire it.
  Test: unit test — close inside zone → still active; close below `wick_low` (bull)
  / above `wick_high` (bear) → invalidated.
- [x] **AC3 — tap fires once:** A tapped OB is not re-alerted on later scans.
  Test: two consecutive `check_tap` calls with price in zone → OB flagged tapped,
  tap surfaced once.
- [x] **AC4 — state persisted in SQLite:** OBs (with tapped/invalidated status)
  and pending watches are written to a SQLite DB and reloaded on init. Test: seed
  state, construct a fresh detector + engine from the same DB path → same OBs and
  watches recovered.
- [x] **AC5 — sqlite3 is stdlib, no new pip dep:** DB import + connect + schema
  init succeed; `requirements.txt` gains no installable `sqlite3` line (would break
  `pip install`). Test: `pip install -r requirements.txt` stays valid; import works.
- [x] **AC6 — behavior preserved:** OB detection, displacement/FVG/MSS evaluation,
  and all Telegram message texts are unchanged. Test: detection unit tests pass;
  message-format strings unchanged.
- [x] **AC7 — is_watching exists:** `LTFConfirmationEngine.is_watching(ob_id)`
  returns whether an OB is currently watched, so the `main.py` tap loop no longer
  raises `AttributeError`. Test: unit test — False before `add_watch`, True after;
  the tap loop runs without raising.

## Out of Scope

- Cross-restart durability on Railway's ephemeral filesystem. Chosen: **in-run
  persistence only**. DB path is configurable (`STATE_DB_PATH`, default
  `state/bot.db`) so a later volume mount makes it durable with no code change.
- Strategy / signal-logic changes (displacement thresholds, FVG rules, R:R, pairs).
- The separate single-file `forex-signal` repo (not deployed).
- A second review pass may open a follow-up PR for further bugs found.

## Why Build (Phase 0b)

No library replaces the domain fix. Persistence reuses the Python stdlib
`sqlite3` (no third-party dependency). Build limited to the bot's own logic.
