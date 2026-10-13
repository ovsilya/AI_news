"""SQLite schema and small helpers. One file holds all pipeline state."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

-- Conditional GET state per URL (ETag / Last-Modified).
CREATE TABLE IF NOT EXISTS http_cache (
    url TEXT PRIMARY KEY, etag TEXT, last_modified TEXT, fetched_at TEXT
);

-- One row per source: what happened on the last run (for the health check).
CREATE TABLE IF NOT EXISTS source_health (
    source_id TEXT PRIMARY KEY, first_run_at TEXT, last_run_at TEXT, last_status TEXT,
    last_new_items INTEGER DEFAULT 0, last_new_at TEXT, consecutive_errors INTEGER DEFAULT 0
);

-- Every collected item. The same URL from two sources is two rows in one story.
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY,
    source_id TEXT NOT NULL,
    url TEXT NOT NULL,
    canonical_url TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT,
    published_at TEXT,
    fetched_at TEXT NOT NULL,
    discussion_url TEXT,
    engagement TEXT,          -- JSON: {"points": 120, "comments": 40, "upvotes": 30}
    minhash BLOB,
    story_id INTEGER REFERENCES stories(id),
    UNIQUE (source_id, canonical_url)
);
CREATE INDEX IF NOT EXISTS items_canonical ON items(canonical_url);
CREATE INDEX IF NOT EXISTS items_fetched ON items(fetched_at);
CREATE INDEX IF NOT EXISTS items_story ON items(story_id);

-- A story groups items about the same thing.
CREATE TABLE IF NOT EXISTS stories (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    layer TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    heat REAL DEFAULT 0
);

-- LLM judge output per story and prompt version.
CREATE TABLE IF NOT EXISTS scores (
    story_id INTEGER NOT NULL REFERENCES stories(id),
    prompt_version TEXT NOT NULL,
    model TEXT NOT NULL,
    scored_at TEXT NOT NULL,
    relevance INTEGER, novelty INTEGER, actionability INTEGER, credibility INTEGER,
    section TEXT,
    why_it_matters TEXT,
    PRIMARY KEY (story_id, prompt_version)
);

CREATE TABLE IF NOT EXISTS briefs (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    edition TEXT NOT NULL,
    story_ids TEXT NOT NULL,      -- JSON list of stories shown
    markdown TEXT NOT NULL,
    telegram_html TEXT NOT NULL,
    telegram_message_ids TEXT,    -- JSON list, empty until delivered
    cost_usd REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY, lane TEXT NOT NULL, started_at TEXT NOT NULL,
    finished_at TEXT, status TEXT, stats TEXT
);

CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY, run_id INTEGER, at TEXT NOT NULL, purpose TEXT, model TEXT,
    input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER, cache_write_tokens INTEGER,
    cost_usd REAL
);

-- Small key/value snapshots for diff-based collectors (e.g. the OpenRouter model list).
CREATE TABLE IF NOT EXISTS snapshots (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);
-- Main text of article pages fetched for stories in a brief (status: ok | robots | empty | error).
CREATE TABLE IF NOT EXISTS article_text (url TEXT PRIMARY KEY, text TEXT, status TEXT NOT NULL, fetched_at TEXT NOT NULL);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Path | str) -> sqlite3.Connection:
    on_disk = str(path) != ":memory:"
    if on_disk:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if on_disk:
        conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
    conn.commit()
    return conn


def get_snapshot(conn: sqlite3.Connection, key: str):
    row = conn.execute("SELECT value FROM snapshots WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value"]) if row else None


def set_snapshot(conn: sqlite3.Connection, key: str, value) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO snapshots VALUES (?, ?, ?)", (key, json.dumps(value, sort_keys=True), now())
    )


def start_run(conn: sqlite3.Connection, lane: str) -> int:
    cur = conn.execute("INSERT INTO runs (lane, started_at, status) VALUES (?, ?, 'running')", (lane, now()))
    conn.commit()
    return cur.lastrowid


def finish_run(conn: sqlite3.Connection, run_id: int, status: str, stats: dict) -> None:
    conn.execute(
        "UPDATE runs SET finished_at = ?, status = ?, stats = ? WHERE id = ?",
        (now(), status, json.dumps(stats), run_id),
    )
    conn.commit()
