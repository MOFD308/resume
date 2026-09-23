import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id            TEXT PRIMARY KEY,      -- "<kind>:<external id>"
    kind          TEXT NOT NULL,         -- youtube | website | x | discord
    author        TEXT NOT NULL,
    category      TEXT NOT NULL,
    title         TEXT,
    url           TEXT,
    content       TEXT,
    published_at  TEXT NOT NULL,
    fetched_at    TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending',  -- pending | done | error | skipped
    error         TEXT,
    is_macro      INTEGER DEFAULT 0,
    summary       TEXT,
    macro_summary TEXT,
    risk_level    TEXT,
    macro_themes  TEXT                  -- JSON list
);
CREATE INDEX IF NOT EXISTS items_published ON items(published_at);

CREATE TABLE IF NOT EXISTS levels (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id      TEXT NOT NULL REFERENCES items(id),
    kind         TEXT NOT NULL,
    author       TEXT NOT NULL,
    ticker       TEXT NOT NULL,
    level_type   TEXT NOT NULL,
    price        REAL NOT NULL,
    price_high   REAL,
    direction    TEXT,
    timeframe    TEXT,
    conviction   TEXT,
    note         TEXT,
    quote        TEXT,
    published_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS levels_ticker ON levels(ticker, published_at);

CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DB:
    def __init__(self, path: Path | str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)

    def query(self, sql: str, params=()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def execute(self, sql: str, params=()) -> None:
        with self._lock, self.conn:
            self.conn.execute(sql, params)

    # --- items -----------------------------------------------------------
    def has_item(self, item_id: str) -> bool:
        return bool(self.query("SELECT 1 FROM items WHERE id=?", (item_id,)))

    def add_item(self, *, id, kind, author, category, title, url, content, published_at) -> bool:
        """Insert a new item; returns False if it was already stored."""
        with self._lock, self.conn:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO items (id, kind, author, category, title, url, content,"
                " published_at, fetched_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (id, kind, author, category, title, url, content, published_at, utcnow()),
            )
            return cur.rowcount == 1

    def pending_items(self) -> list[dict]:
        return self.query("SELECT * FROM items WHERE status='pending' ORDER BY published_at")

    def mark_item(self, item_id: str, status: str, error: str | None = None) -> None:
        self.execute("UPDATE items SET status=?, error=? WHERE id=?", (status, error, item_id))

    def save_extraction(self, item: dict, extraction) -> None:
        with self._lock, self.conn:
            self.conn.execute("DELETE FROM levels WHERE item_id=?", (item["id"],))
            for lv in extraction.levels:
                self.conn.execute(
                    "INSERT INTO levels (item_id, kind, author, ticker, level_type, price, price_high,"
                    " direction, timeframe, conviction, note, quote, published_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (item["id"], item["kind"], item["author"], lv.ticker.upper().lstrip("$"),
                     lv.level_type, lv.price, lv.price_high, lv.direction, lv.timeframe,
                     lv.conviction, lv.note, lv.quote, item["published_at"]),
                )
            self.conn.execute(
                "UPDATE items SET status='done', error=NULL, is_macro=?, summary=?, macro_summary=?,"
                " risk_level=?, macro_themes=? WHERE id=?",
                (int(extraction.is_macro), extraction.summary, extraction.macro_summary,
                 extraction.risk_level,
                 json.dumps([t.model_dump() for t in extraction.macro_themes], ensure_ascii=False),
                 item["id"]),
            )

    # --- key/value state (cursors, sent markers) --------------------------
    def get_kv(self, key: str, default: str | None = None) -> str | None:
        rows = self.query("SELECT value FROM kv WHERE key=?", (key,))
        return rows[0]["value"] if rows else default

    def set_kv(self, key: str, value: str) -> None:
        self.execute("INSERT INTO kv (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (key, value))
