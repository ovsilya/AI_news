"""J1: poll every employer's board and diff it against what we stored (new / changed / closed)."""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx

from ..db import get_snapshot, now, set_snapshot
from ..pipeline.fulltext import Robots
from . import workday
from .boards import Company, Posting, fetch_postings
from .filters import markets, role_family

log = logging.getLogger(__name__)

CLOSE_AFTER = timedelta(hours=36)   # a posting missing for this long, from successful polls, has closed
WORKDAY_WORKERS = 4                 # Workday hosts searched at the same time


def _hash(p: Posting) -> str:
    return hashlib.sha1(f"{p.title}\n{p.description}".encode()).hexdigest()


def store_board(conn: sqlite3.Connection, c: Company, postings: list[Posting], at: str | None = None, *,
                touched=(), still_open=None, listed: int | None = None) -> dict:
    """Saves the matching postings of one board and closes the ones that have gone.

    For a board without a full list (Workday): `touched` are keys of stored postings the board still
    shows but we didn't read again, and `still_open(row)` asks a missing posting's own page before it
    is closed: True keeps it open, False closes it, None leaves it for the next run."""
    at = at or now()
    stats = {"listed": len(postings) if listed is None else listed, "matching": len(touched),
             "new": 0, "changed": 0, "closed": 0}
    seen = set(touched)
    for key in touched:
        conn.execute("UPDATE job_postings SET last_seen = ?, closed_at = NULL WHERE key = ?", (at, key))
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
    cutoff = _cutoff(at)
    for row in conn.execute("SELECT id, key, url, last_seen FROM job_postings WHERE company = ? AND closed_at IS NULL"
                            " ORDER BY last_seen", (c.id,)).fetchall():
        if row["key"] in seen or row["last_seen"] >= cutoff:
            continue
        verdict = still_open(row) if still_open else False
        if verdict:
            conn.execute("UPDATE job_postings SET last_seen = ? WHERE id = ?", (at, row["id"]))
        elif verdict is False:
            conn.execute("UPDATE job_postings SET closed_at = ? WHERE id = ?", (row["last_seen"], row["id"]))
            stats["closed"] += 1
    conn.execute("INSERT OR REPLACE INTO job_boards VALUES (?, ?, 'ok', ?, ?)",
                 (c.id, at, stats["listed"], stats["matching"]))
    conn.commit()
    return stats


@dataclass
class WorkdayResult:
    """What one Workday poll found, before it is stored."""
    found: int                       # search results in our role families
    fresh: list[Posting]             # new postings, read and in our markets
    touched: list[str]               # keys of stored postings still in the results
    open_now: dict[str, bool]        # key -> still open?, from the own pages of missing postings
    skipped: list[str]               # job ids read before and outside our roles or markets
    waiting: int                     # new postings left for the next run


def _workday_state(conn: sqlite3.Connection, c: Company) -> tuple[dict, set]:
    known = {r["key"]: dict(r) for r in conn.execute(
        "SELECT key, url, last_seen, closed_at FROM job_postings WHERE company = ?", (c.id,))}
    return known, set(get_snapshot(conn, f"workday:{c.id}:skipped") or [])


def fetch_workday(c: Company, fetcher, robots, known: dict, skipped: set, cutoff: str) -> WorkdayResult:
    """The network half of a Workday poll (no database, so boards can be fetched in parallel):
    search, read each new posting once (up to MAX_DETAILS), and ask the own page of each stored
    posting that has been missing past the grace period (up to MAX_CHECKS)."""
    site = workday.Site.parse(c.board)
    if not robots.allowed(f"https://{site.host}/{site.site}/"):
        raise PermissionError(f"robots.txt disallows {c.board}")
    found = workday.search(c, fetcher)
    skipped = set(skipped)
    fresh, touched, waiting, read = [], [], 0, 0
    for jid, item in found.items():
        key = f"{c.id}:{jid}"
        if key in known:
            touched.append(key)
            continue
        if jid in skipped:
            continue
        if read >= workday.MAX_DETAILS:
            waiting += 1
            continue
        read += 1
        try:
            info = workday.detail(fetcher, site, item.get("externalPath") or "")
        except httpx.HTTPError as exc:
            log.warning("jobs %s: %s: %s", c.id, jid, exc)
            continue
        p = workday.to_posting(item, info, site) if info else None
        if p is None or not role_family(p.title, c.ai_native) or not markets(p.locations, p.remote, p.extra):
            skipped.add(jid)
            continue
        fresh.append(p)
    open_now: dict[str, bool] = {}
    missing = sorted((r for k, r in known.items() if k not in set(touched) and r["closed_at"] is None
                      and r["last_seen"] < cutoff), key=lambda r: r["last_seen"])
    for r in missing[:workday.MAX_CHECKS]:
        path = workday.path_of(r["url"] or "")
        if path:
            try:
                open_now[r["key"]] = workday.detail(fetcher, site, path) is not None
            except httpx.HTTPError:
                pass                      # unknown is not closed
    return WorkdayResult(len(found), fresh, touched, open_now, sorted(skipped & set(found)), waiting)


def store_workday(conn: sqlite3.Connection, c: Company, res: WorkdayResult, at: str) -> dict:
    set_snapshot(conn, f"workday:{c.id}:skipped", res.skipped)
    stats = store_board(conn, c, res.fresh, at, touched=res.touched, listed=res.found,
                        still_open=lambda row: res.open_now.get(row["key"]))
    stats["waiting"] = res.waiting
    return stats


def _cutoff(at: str) -> str:
    return (datetime.fromisoformat(at) - CLOSE_AFTER).isoformat(timespec="seconds")


def poll_workday(conn: sqlite3.Connection, c: Company, fetcher, robots, at: str | None = None) -> dict:
    """One Workday career site, start to finish. Postings already stored aren't read again."""
    at = at or now()
    known, skipped = _workday_state(conn, c)
    return store_workday(conn, c, fetch_workday(c, fetcher, robots, known, skipped, _cutoff(at)), at)


def _record_error(conn: sqlite3.Connection, c: Company, exc: Exception) -> None:
    log.warning("jobs %s: %s", c.id, exc)
    conn.execute("INSERT INTO job_boards (company, last_polled_at, last_status) VALUES (?, ?, ?)"
                 " ON CONFLICT(company) DO UPDATE SET last_polled_at = excluded.last_polled_at,"
                 " last_status = excluded.last_status",
                 (c.id, now(), f"error: {type(exc).__name__}: {exc}"[:200]))
    conn.commit()


def poll_all(conn: sqlite3.Connection, companies: list[Company], fetcher) -> dict:
    """Polls every board. One failing board is recorded and skipped; it closes nothing.
    Workday sites are slow to search (one request a second per host), so hosts run in parallel."""
    total = {"boards": 0, "errors": 0, "listed": 0, "matching": 0, "new": 0, "changed": 0, "closed": 0, "waiting": 0}

    def add(stats: dict) -> None:
        for k, v in stats.items():
            total[k] += v

    robots = Robots(fetcher)
    for c in companies:
        if c.ats == "workday":
            continue
        total["boards"] += 1
        try:
            postings = fetch_postings(c, fetcher)
        except Exception as exc:
            total["errors"] += 1
            _record_error(conn, c, exc)
            continue
        add(store_board(conn, c, postings))

    by_host: dict[str, list[Company]] = {}
    for c in companies:
        if c.ats == "workday":
            by_host.setdefault(workday.Site.parse(c.board).host, []).append(c)
    if not by_host:
        return total
    at = now()
    state = {c.id: _workday_state(conn, c) for cs in by_host.values() for c in cs}

    def run_host(cs: list[Company]) -> list[tuple[Company, WorkdayResult | Exception]]:
        out = []
        for c in cs:     # one host's boards in turn, so its spacing holds
            try:
                out.append((c, fetch_workday(c, fetcher, robots, *state[c.id], _cutoff(at))))
            except Exception as exc:
                out.append((c, exc))
        return out

    with ThreadPoolExecutor(max_workers=WORKDAY_WORKERS) as pool:
        for fut in as_completed([pool.submit(run_host, cs) for cs in by_host.values()]):
            for c, res in fut.result():
                total["boards"] += 1
                if isinstance(res, Exception):
                    total["errors"] += 1
                    _record_error(conn, c, res)
                else:
                    add(store_workday(conn, c, res, at))
    return total
