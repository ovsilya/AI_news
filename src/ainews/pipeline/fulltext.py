"""Article text for the stories in a brief, so the editor can explain what happened in detail.

Feeds often carry only a headline or a short teaser. For each chosen story whose excerpts are short,
we fetch one article page with the polite fetcher, honour robots.txt, and keep the main text
(extracted by trafilatura), cached in article_text so a redo doesn't fetch again. Paywalled pages
give only a teaser. Google News links can't be opened directly, so those stories keep their headline.
coverage() tells the reader how much of each story the editor actually saw.
"""
from __future__ import annotations

import logging
import re
import sqlite3
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from ..config import USER_AGENT
from ..db import now
from .rank import Story

log = logging.getLogger(__name__)

MIN_CHARS = 800      # stories with less excerpt text than this get their article fetched
MAX_CHARS = 6000     # kept per article
SKIP_HOSTS = {"news.google.com", "news.ycombinator.com", "www.reddit.com", "openrouter.ai"}
BOT_NAME = USER_AGENT.split("/", 1)[0]


class Robots:
    """robots.txt per host, fetched once per run. A missing robots.txt (4xx) allows everything;
    an unreachable one (5xx, network error) allows nothing, as RFC 9309 suggests."""

    def __init__(self, fetcher):
        self.fetcher, self.cache = fetcher, {}

    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        root = f"{parts.scheme}://{parts.netloc}"
        if root not in self.cache:
            rp = RobotFileParser()
            try:
                r = self.fetcher.get(f"{root}/robots.txt", conditional=False)
                rp.parse((r.text or "").splitlines())
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code < 500:
                    rp.parse([])
                else:
                    rp.disallow_all = True
            except httpx.HTTPError:
                rp.disallow_all = True
            self.cache[root] = rp
        return self.cache[root].can_fetch(BOT_NAME, url)


def _extract(html: str, url: str) -> str:
    import trafilatura

    text = trafilatura.extract(html, url=url, include_comments=False, include_tables=False, favor_precision=True)
    return (text or "").strip()[:MAX_CHARS]


def _article(conn: sqlite3.Connection, fetcher, robots: Robots, url: str) -> str:
    row = conn.execute("SELECT text, status FROM article_text WHERE url = ?", (url,)).fetchone()
    if row is not None:
        return row["text"] or ""
    text, status = "", "ok"
    try:
        if not robots.allowed(url):
            status = "robots"
        else:
            r = fetcher.get(url, conditional=False)
            text = _extract(r.text or "", url)
            status = "ok" if text else "empty"
    except Exception as exc:  # one bad page must not stop the brief
        log.info("article %s: %s", url, exc)
        status = "error"
    conn.execute("INSERT OR REPLACE INTO article_text VALUES (?, ?, ?, ?)", (url, text, status, now()))
    conn.commit()
    return text


ARTICLE_CHARS = 600    # this much body text counts as the full article (paywall teasers are shorter)
HEADLINE_CHARS = 60    # less than this beyond the headline: we only had the headline
PAPER_URLS = ("arxiv.org/abs/", "huggingface.co/papers/")
COVERAGE_RANK = {"article": 3, "abstract": 2, "excerpt": 1, "headline": 0}


def _beyond_title(text: str, title: str) -> int:
    """Characters of text that aren't the headline repeated (Google News summaries repeat it)."""
    seen = set(re.findall(r"\w+", title.lower()))
    return sum(len(w) + 1 for w in re.findall(r"\w+", text.lower()) if w not in seen)


def coverage(s: Story) -> str:
    """What the editor had to work from: "article", "abstract" (papers), "excerpt" or "headline"."""
    body, paper = 0, False
    for it in s.items:
        paper = paper or any(p in it["url"] for p in PAPER_URLS)
        body = max(body, len(it.get("full_text") or ""), _beyond_title(it.get("summary") or "", it["title"]))
    if body >= ARTICLE_CHARS:
        return "abstract" if paper else "article"
    return "excerpt" if body >= HEADLINE_CHARS else "headline"


def enrich(conn: sqlite3.Connection, fetcher, stories: list[Story], excerpt_len) -> dict:
    """Attach `full_text` to one item of every thin story. excerpt_len(story) -> current excerpt size."""
    robots, stats = Robots(fetcher), {"thin": 0, "enriched": 0}
    for s in stories:
        if excerpt_len(s) >= MIN_CHARS:
            continue
        stats["thin"] += 1
        for it in s.items:
            if urlsplit(it["url"]).netloc in SKIP_HOSTS:
                continue
            text = _article(conn, fetcher, robots, it["url"])
            if text:
                it["full_text"] = text
                stats["enriched"] += 1
                break
    return stats
