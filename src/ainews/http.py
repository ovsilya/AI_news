"""Polite HTTP fetching: identifies the bot, conditional GET, per-host spacing, backoff on 429/5xx."""
from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from .config import USER_AGENT
from .db import now

log = logging.getLogger(__name__)

# Minimum seconds between two requests to the same host.
HOST_SPACING = {"export.arxiv.org": 3.0, "rss.arxiv.org": 3.0, "www.reddit.com": 30.0, "github.com": 0.5}
# Strict rate limiters: (seconds to wait after a 429 without Retry-After, max retries). Quick retries
# only extend a Reddit block, so wait a full minute and try once more.
SLOW_429 = {"www.reddit.com": (60.0, 1)}
# Hosts that return intermittent 404s for valid URLs (YouTube channel feeds): retry those too.
RETRY_404_HOSTS = {"www.youtube.com"}
DEFAULT_SPACING = 1.0


@dataclass
class Response:
    status: int
    text: str | None  # None when the server replied 304 Not Modified
    url: str

    @property
    def not_modified(self) -> bool:
        return self.status == 304


class Fetcher:
    def __init__(self, conn: sqlite3.Connection | None = None, timeout: float = 20.0, retries: int = 3,
                 client: httpx.Client | None = None, sleep=time.sleep):
        self.conn = conn
        self.retries = retries
        self.sleep = sleep
        self.client = client or httpx.Client(
            timeout=timeout, follow_redirects=True,
            headers={"Accept-Encoding": "gzip, deflate"},
        )
        self._last_hit: dict[str, float] = {}

    def _space(self, host: str) -> None:
        wait = HOST_SPACING.get(host, DEFAULT_SPACING) - (time.monotonic() - self._last_hit.get(host, 0.0))
        if wait > 0:
            self.sleep(wait)
        self._last_hit[host] = time.monotonic()

    def get(self, url: str, *, conditional: bool = True, headers: dict | None = None,
            params: dict | None = None) -> Response:
        hdrs = {"User-Agent": USER_AGENT, **(headers or {})}
        cache_key = url if not params else f"{url}?{httpx.QueryParams(params)}"
        if conditional and self.conn is not None:
            row = self.conn.execute("SELECT etag, last_modified FROM http_cache WHERE url = ?", (cache_key,)).fetchone()
            if row and row["etag"]:
                hdrs["If-None-Match"] = row["etag"]
            if row and row["last_modified"]:
                hdrs["If-Modified-Since"] = row["last_modified"]

        host = urlsplit(url).netloc
        delay, retries = SLOW_429.get(host, (2.0, self.retries))
        for attempt in range(retries + 1):
            self._space(host)
            try:
                r = self.client.get(url, headers=hdrs, params=params)
            except httpx.TransportError as exc:
                if attempt == retries:
                    raise
                log.info("retrying %s after %s", url, exc)
                self.sleep(delay)
                delay *= 2
                continue
            if r.status_code == 429 or r.status_code >= 500 or (r.status_code == 404 and host in RETRY_404_HOSTS):
                if attempt == retries:
                    r.raise_for_status()
                retry_after = r.headers.get("Retry-After", "")
                self.sleep(float(retry_after) if retry_after.isdigit() else delay)
                delay *= 2
                continue
            break

        if r.status_code == 304:
            return Response(304, None, str(r.url))
        r.raise_for_status()
        if conditional and self.conn is not None:
            self.conn.execute(
                "INSERT OR REPLACE INTO http_cache VALUES (?, ?, ?, ?)",
                (cache_key, r.headers.get("ETag"), r.headers.get("Last-Modified"), now()),
            )
        return Response(r.status_code, r.text, str(r.url))

    def close(self) -> None:
        self.client.close()
