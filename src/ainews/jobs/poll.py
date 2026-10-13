"""J1: poll every employer's board and diff it against what we stored (new / changed / closed)."""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from datetime import datetime, timedelta

from ..db import now
from .boards import Company, Posting, fetch_postings
from .filters import markets, role_family

log = logging.getLogger(__name__)

CLOSE_AFTER = timedelta(hours=36)   # a posting missing for this long, from successful polls, has closed


def _hash(p: Posting) -> str:
    return hashlib.sha1(f"{p.title}\n{p.description}".encode()).hexdigest()


def store_board(conn: sqlite3.Connection, c: Company, postings: list[Posting], at: str | None = None) -> dict:
    """Saves the matching postings of one board and closes the ones that have gone."""
    at = at or now()
    stats = {"listed": len(postings), "matching": 0, "new": 0, "changed": 0, "closed": 0}
    seen = set()
    for p in postings:
        family = role_family(p.title, c.ai_native)
        where = markets(p.locations, p.remote, p.extra)
        if not family or not where:
            continue
        stats["matching"] += 1
        key = f"{c.id}:{p.job_id}"
        seen.add(key)
        h = _hash(p)
        row = conn.execute("SELECT id, text_hash FROM job_postings WHERE key = ?", (key,)).fetchone()
        values = (p.title, p.url, json.dumps(p.locations), json.dumps(where), family, p.department, p.description,
                  h, json.dumps(p.salary) if p.salary else None, p.posted_at)
        if row is None:
            conn.execute(
                "INSERT INTO job_postings (title, url, locations, markets, title_family, department, description,"
                " text_hash, salary, posted_at, key, company, first_seen, last_seen)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", values + (key, c.id, at, at))
            stats["new"] += 1
        else:
            stats["changed"] += row["text_hash"] != h
            conn.execute(
                "UPDATE job_postings SET title = ?, url = ?, locations = ?, markets = ?, title_family = ?,"
                " department = ?, description = ?, text_hash = ?, salary = ?, posted_at = ?, last_seen = ?,"
                " closed_at = NULL WHERE id = ?", values + (at, row["id"]))
    cutoff = (datetime.fromisoformat(at) - CLOSE_AFTER).isoformat(timespec="seconds")
    for row in conn.execute("SELECT id, key, last_seen FROM job_postings WHERE company = ? AND closed_at IS NULL",
                            (c.id,)).fetchall():
        if row["key"] not in seen and row["last_seen"] < cutoff:
            conn.execute("UPDATE job_postings SET closed_at = ? WHERE id = ?", (row["last_seen"], row["id"]))
            stats["closed"] += 1
    conn.execute("INSERT OR REPLACE INTO job_boards VALUES (?, ?, 'ok', ?, ?)",
                 (c.id, at, stats["listed"], stats["matching"]))
    conn.commit()
    return stats


def poll_all(conn: sqlite3.Connection, companies: list[Company], fetcher) -> dict:
    """Polls every board. One failing board is recorded and skipped; it closes nothing."""
    total = {"boards": 0, "errors": 0, "listed": 0, "matching": 0, "new": 0, "changed": 0, "closed": 0}
    for c in companies:
        total["boards"] += 1
        try:
            postings = fetch_postings(c, fetcher)
        except Exception as exc:
            log.warning("jobs %s: %s", c.id, exc)
            total["errors"] += 1
            conn.execute("INSERT INTO job_boards (company, last_polled_at, last_status) VALUES (?, ?, ?)"
                         " ON CONFLICT(company) DO UPDATE SET last_polled_at = excluded.last_polled_at,"
                         " last_status = excluded.last_status",
                         (c.id, now(), f"error: {type(exc).__name__}: {exc}"[:200]))
            conn.commit()
            continue
        for k, v in store_board(conn, c, postings).items():
            total[k] += v
    return total
