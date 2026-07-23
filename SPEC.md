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

---

# Round 2 — Reliability improvements (PR #2, stacked on PR #1)

Findings from the full-codebase review. Focus: make the core tap detection and
data path more reliable. No new dependencies.

## Acceptance Criteria (binary testable)

- [x] **AC8 — range-based tap:** A tap fires when the latest candle's [low, high]
  range overlaps the OB zone, even if the sampled price is outside the zone
  (catches intra-scan wicks the 5-min sample misses). Test: OB zone [104,105],
  price 106 (outside), candle low=103/high=106 → `check_tap` returns the OB.
- [x] **AC9 — OANDA transient retry:** `DataConnector` retries GET requests on
  timeout / connection error / 429 / 5xx with backoff, then returns None. Test:
  a stub session failing once (500) then 200 yields data on the retry.
- [x] **AC10 — cascade errors log traceback:** `scan_once`'s per-cascade handler
  logs with `exc_info=True`. Verified by code (a swallowed `AttributeError` now
  shows its stack — the class of bug that hid the original defect).
- [x] **AC11 — error alerts are Markdown-safe:** `send_error` sends without
  `parse_mode`, so exception text containing `_`/`*`/`[` cannot 400 the alert.
  Test: captured payload for `send_error` has no `parse_mode`.
- [x] **AC12 — startup shows the real interval:** `send_startup` reports the
  configured scan interval, not a hardcoded "15 minutes". Test: captured payload
  contains the passed interval.
- [x] **AC13 — dead sort removed / detection unchanged:** the no-op `.sort()`
  before `max()` in `_scan` is removed; detection output is unchanged (AC6 test).
- [x] **AC14 — fallback config matches config.yaml:** the in-code default config
  in `load_config` matches the shipped `config.yaml` (pairs, cascades, interval).

## Out of Scope (Round 2)

- Pruning invalidated order blocks (bounded by the monthly migration; pure
  housekeeping, not a reliability gain — deliberately skipped).
- Switching tap detection to LTF candles (would need an extra fetch per scan).

---

# Round 3 — Confirmation = displacement + MSS (PR #3)

Owner request: HTF zone on H4/H2/H1, confirmation on M15/M30/M5 "with
displacement and MSS only". The Fair Value Gap was a mandatory phase between
displacement and MSS and was suppressing valid signals.

## Acceptance Criteria (binary testable)

- [ ] **AC15 — FVG is no longer required:** a watch that gets displacement and an
  MSS but no FVG produces a signal. Test: LTF frame with displacement + MSS but
  no FVG → `process` returns a Signal (before: returned nothing).
- [ ] **AC16 — displacement still required:** no displacement → no signal. Test:
  flat LTF frame → `process` returns no signal.
- [ ] **AC17 — MSS still required:** displacement present but no MSS break → no
  signal. Test: displacement without the swing break → `process` returns nothing.
- [ ] **AC18 — FVG recorded as confluence when present:** if an FVG does exist at
  displacement, it is still captured on the signal (fvg_high/fvg_low set) and the
  Telegram signal shows the FVG line only when present.

## Out of Scope (Round 3)

- BOS-anchored HTF order-block selection and most-recent-vs-strongest (offered as
  a later upgrade; owner did not request it in this round).
