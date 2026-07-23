"""
state_store.py — SQLite-backed persistence for order blocks and pending LTF watches.

Replaces the previous hand-rolled JSON file (order blocks only) and the RAM-only
watch dict. Both order blocks AND in-flight LTF watches now live in one
transactional SQLite database, so the tap -> watch -> MSS pipeline has a single
source of truth.

Durability note:
    The DB path defaults to ``state/bot.db`` and can be overridden with the
    ``STATE_DB_PATH`` environment variable. On an ephemeral filesystem (e.g. a
    Railway container without a mounted volume) the file is wiped on restart —
    this is *in-run* persistence. Pointing ``STATE_DB_PATH`` at a mounted volume
    makes the exact same code durable across restarts with no code change.

Dependencies:
    Uses only the Python standard library ``sqlite3`` — there is no pip package
    to install (adding ``sqlite3`` to requirements.txt would break ``pip``).

Concurrency:
    The bot runs a single scan loop (one thread), so one shared connection is
    used. This is not safe for multi-threaded access.

Invariants:
    - One row per ``ob_id`` in ``order_blocks`` and at most one watch per
      ``ob_id`` in ``watches``.
    - Boolean columns are stored as INTEGER 0/1 and returned as Python ``bool``.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import List, Optional

from logger import get_logger

log = get_logger(__name__)

DEFAULT_DB_PATH = "state/bot.db"

# Order-block columns in declaration order; also the OrderBlock dataclass fields.
_OB_COLUMNS = (
    "ob_id", "symbol", "htf", "direction",
    "ob_high", "ob_low", "wick_high", "wick_low",
    "candle_time", "invalidated", "tapped", "tap_count", "notified",
)
_OB_BOOL_FIELDS = ("invalidated", "tapped", "notified")

# Watch columns in declaration order.
_WATCH_COLUMNS = (
    "ob_id", "symbol", "htf", "ltf", "ob_json",
    "tap_time", "timeout_seconds",
    "displacement_confirmed", "fvg_confirmed", "displacement_candle_idx",
    "fvg_high", "fvg_low", "swing_level",
)
_WATCH_BOOL_FIELDS = ("displacement_confirmed", "fvg_confirmed")


class StateStore:
    """SQLite store for order blocks and pending LTF watches."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        path = db_path or os.environ.get("STATE_DB_PATH", DEFAULT_DB_PATH)
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # isolation_level=None would auto-commit; we use explicit `with self._conn`
        # transactions for the multi-statement writes instead.
        self._conn = sqlite3.connect(str(self._path))
        self._conn.row_factory = sqlite3.Row
        self._init_schema()
        log.info(f"[STATE] SQLite store ready: {self._path}")

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS order_blocks (
                ob_id        TEXT PRIMARY KEY,
                symbol       TEXT NOT NULL,
                htf          TEXT NOT NULL,
                direction    TEXT NOT NULL,
                ob_high      REAL NOT NULL,
                ob_low       REAL NOT NULL,
                wick_high    REAL NOT NULL,
                wick_low     REAL NOT NULL,
                candle_time  TEXT NOT NULL,
                invalidated  INTEGER NOT NULL DEFAULT 0,
                tapped       INTEGER NOT NULL DEFAULT 0,
                tap_count    INTEGER NOT NULL DEFAULT 0,
                notified     INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_ob_symbol_htf
                ON order_blocks(symbol, htf);

            CREATE TABLE IF NOT EXISTS watches (
                ob_id                   TEXT PRIMARY KEY,
                symbol                  TEXT NOT NULL,
                htf                     TEXT NOT NULL,
                ltf                     TEXT NOT NULL,
                ob_json                 TEXT NOT NULL,
                tap_time                REAL NOT NULL,
                timeout_seconds         INTEGER NOT NULL,
                displacement_confirmed  INTEGER NOT NULL DEFAULT 0,
                fvg_confirmed           INTEGER NOT NULL DEFAULT 0,
                displacement_candle_idx INTEGER NOT NULL DEFAULT -1,
                fvg_high                REAL NOT NULL DEFAULT 0,
                fvg_low                 REAL NOT NULL DEFAULT 0,
                swing_level             REAL NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_watch_cascade
                ON watches(symbol, htf, ltf);
            """
        )
        self._conn.commit()

    # ------------------------------------------------------------------ #
    # Order blocks
    # ------------------------------------------------------------------ #

    def load_obs(self, symbol: str, htf: str) -> List[dict]:
        """Return all order blocks for one (symbol, htf) as OrderBlock-shaped dicts."""
        rows = self._conn.execute(
            "SELECT * FROM order_blocks WHERE symbol=? AND htf=?",
            (symbol, htf),
        ).fetchall()
        return [self._row_to_ob_dict(r) for r in rows]

    def save_obs(self, symbol: str, htf: str, obs: List[dict]) -> None:
        """Atomically replace the full order-block set for one (symbol, htf) key.

        ``obs`` is a list of OrderBlock-shaped dicts (e.g. ``asdict(ob)``).
        """
        params = [self._ob_dict_to_params(o) for o in obs]
        with self._conn:  # BEGIN/COMMIT, rolls back on exception
            self._conn.execute(
                "DELETE FROM order_blocks WHERE symbol=? AND htf=?",
                (symbol, htf),
            )
            if params:
                placeholders = ", ".join(f":{c}" for c in _OB_COLUMNS)
                self._conn.executemany(
                    f"INSERT INTO order_blocks ({', '.join(_OB_COLUMNS)}) "
                    f"VALUES ({placeholders})",
                    params,
                )

    # ------------------------------------------------------------------ #
    # Watches
    # ------------------------------------------------------------------ #

    def load_watches(self, symbol: str, htf: str, ltf: str) -> List[dict]:
        """Return all pending watches for one cascade as watch-shaped dicts."""
        rows = self._conn.execute(
            "SELECT * FROM watches WHERE symbol=? AND htf=? AND ltf=?",
            (symbol, htf, ltf),
        ).fetchall()
        return [self._row_to_watch_dict(r) for r in rows]

    def upsert_watch(self, watch: dict) -> None:
        """Insert or replace a single watch row (keyed by ob_id)."""
        params = self._watch_dict_to_params(watch)
        placeholders = ", ".join(f":{c}" for c in _WATCH_COLUMNS)
        with self._conn:
            self._conn.execute(
                f"INSERT OR REPLACE INTO watches ({', '.join(_WATCH_COLUMNS)}) "
                f"VALUES ({placeholders})",
                params,
            )

    def delete_watch(self, ob_id: str) -> None:
        """Remove a watch (signal fired or expired)."""
        with self._conn:
            self._conn.execute("DELETE FROM watches WHERE ob_id=?", (ob_id,))

    # ------------------------------------------------------------------ #
    # Row <-> dict conversion (SQLite stores bools as INTEGER)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _row_to_ob_dict(row: sqlite3.Row) -> dict:
        d = {c: row[c] for c in _OB_COLUMNS}
        for f in _OB_BOOL_FIELDS:
            d[f] = bool(d[f])
        return d

    @staticmethod
    def _ob_dict_to_params(ob: dict) -> dict:
        p = {c: ob[c] for c in _OB_COLUMNS}
        for f in _OB_BOOL_FIELDS:
            p[f] = int(bool(p[f]))
        return p

    @staticmethod
    def _row_to_watch_dict(row: sqlite3.Row) -> dict:
        d = {c: row[c] for c in _WATCH_COLUMNS}
        for f in _WATCH_BOOL_FIELDS:
            d[f] = bool(d[f])
        return d

    @staticmethod
    def _watch_dict_to_params(watch: dict) -> dict:
        p = {c: watch[c] for c in _WATCH_COLUMNS}
        for f in _WATCH_BOOL_FIELDS:
            p[f] = int(bool(p[f]))
        return p

    def close(self) -> None:
        self._conn.close()
