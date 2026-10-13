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
MAX_AGE_DAYS = 7   # ignore feed entries older than this (first runs would otherwise import whole archives)


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


def store_items(conn: sqlite3.Connection, source, items) -> int:
    """Insert new items; refresh engagement on ones already seen. Returns the number of new items."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)
    fetched, new = now(), 0
    for it in items:
        if not it.title or not it.url or _too_old(it.published_at, cutoff):
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
    stats = {"sources": 0, "errors": 0, "new_items": 0, "per_source": {}}
    for src in sources:
        stats["sources"] += 1
        try:
            items = collectors[src.kind](src, fetcher, conn, settings)
            new = store_items(conn, src, items)
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
