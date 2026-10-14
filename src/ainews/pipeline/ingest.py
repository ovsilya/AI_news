"""Run every collector, filter, canonicalise and store new items; record source health."""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone

from ..collectors import registry
from ..db import now
from .normalize import canonical_url

log = logging.getLogger(__name__)
MAX_AGE_DAYS = 7   # don't report feed entries older than this (first runs would otherwise import whole archives)


def _too_old(published_at: str | None, cutoff: datetime) -> bool:
    if not published_at:
        return False
    try:
        ts = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts < cutoff


def needs_baseline(conn: sqlite3.Connection, source_id: str) -> bool:
    """True for a source that has never been fetched successfully and has nothing stored or remembered."""
    for table in ("items", "feed_seen"):
        if conn.execute(f"SELECT 1 FROM {table} WHERE source_id = ? LIMIT 1", (source_id,)).fetchone():
            return False
    row = conn.execute("SELECT last_status FROM source_health WHERE source_id = ?", (source_id,)).fetchone()
    return row is None or row["last_status"].startswith("error")


def store_items(conn: sqlite3.Connection, source, items, *, baseline: bool = False) -> int:
    """Insert new items; refresh engagement on ones already seen. Returns the number of new items.

    Every link a feed lists is remembered, so a post comes in once at most: entries too old to report go
    to feed_seen, which keeps old posts that a feed re-dates when they are edited (the LangChain blog does)
    from coming back as new. With baseline (a source's first fetch) everything listed is remembered and
    nothing is reported, since a new feed's dates can't be trusted yet."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)
    fetched, new = now(), 0
    for it in items:
        if not it.title or not it.url:
            continue
        if not source.matches(f"{it.title} {it.summary}"):
            continue
        canon = canonical_url(it.url)
        engagement = json.dumps(it.engagement) if it.engagement else None
        row = conn.execute(
            "SELECT id FROM items WHERE source_id = ? AND canonical_url = ?", (source.id, canon)
        ).fetchone()
        if row:
            if engagement:
                conn.execute("UPDATE items SET engagement = ? WHERE id = ?", (engagement, row["id"]))
            continue
        if baseline or _too_old(it.published_at, cutoff):
            conn.execute("INSERT OR IGNORE INTO feed_seen VALUES (?, ?, ?)", (source.id, canon, fetched))
            continue
        if conn.execute("SELECT 1 FROM feed_seen WHERE source_id = ? AND canonical_url = ?",
                        (source.id, canon)).fetchone():
            continue
        conn.execute(
            "INSERT INTO items (source_id, url, canonical_url, title, summary, published_at, fetched_at,"
            " discussion_url, engagement) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (source.id, it.url, canon, it.title, it.summary, it.published_at, fetched, it.discussion_url, engagement),
        )
        new += 1
    return new


def _health(conn, source_id: str, status: str, new: int) -> None:
    ok = not status.startswith("error")
    conn.execute(
        """INSERT INTO source_health (source_id, first_run_at, last_run_at, last_status, last_new_items, last_new_at,
                                      consecutive_errors)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(source_id) DO UPDATE SET
             last_run_at = excluded.last_run_at,
             last_status = excluded.last_status,
             last_new_items = excluded.last_new_items,
             last_new_at = COALESCE(excluded.last_new_at, source_health.last_new_at),
             consecutive_errors = CASE WHEN ? THEN 0 ELSE source_health.consecutive_errors + 1 END""",
        (source_id, now(), now(), status, new, now() if new else None, 0 if ok else 1, ok),
    )


def collect_all(conn: sqlite3.Connection, sources, fetcher, settings) -> dict:
    collectors = registry()
    stats = {"sources": 0, "errors": 0, "new_items": 0, "per_source": {}, "baseline": []}
    # A fresh install reports the last MAX_AGE_DAYS from every source; a source added later starts with a baseline.
    established = conn.execute("SELECT 1 FROM source_health LIMIT 1").fetchone() is not None
    for src in sources:
        stats["sources"] += 1
        try:
            items = collectors[src.kind](src, fetcher, conn, settings)
            baseline = established and needs_baseline(conn, src.id)
            new = store_items(conn, src, items, baseline=baseline)
            if baseline:
                stats["baseline"].append(src.id)
            _health(conn, src.id, "ok", new)
            conn.commit()
        except Exception as exc:
            conn.rollback()  # also drops the ETag we may have cached, so the next run refetches
            stats["errors"] += 1
            msg = f"error: {type(exc).__name__}: {exc}"[:300]
            log.warning("%s: %s", src.id, msg)
            _health(conn, src.id, msg, 0)
            conn.commit()
            stats["per_source"][src.id] = msg
            continue
        stats["new_items"] += new
        stats["per_source"][src.id] = new
        log.info("%-18s %3d new", src.id, new)
    return stats
