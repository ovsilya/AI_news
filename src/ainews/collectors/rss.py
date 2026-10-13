"""RSS / Atom feeds (also used for filtered feeds and Google News search proxies)."""
from __future__ import annotations

import feedparser

from . import RawItem, html_to_text, iso_from_struct


def parse_feed(text: str) -> list[RawItem]:
    feed = feedparser.parse(text)
    items = []
    for e in feed.entries:
        link = e.get("link") or ""
        title = html_to_text(e.get("title"), 300)
        if not link or not title:
            continue
        summary = e.get("summary") or ""
        if not summary and e.get("content"):
            summary = e["content"][0].get("value", "")
        items.append(RawItem(
            url=link,
            title=title,
            summary=html_to_text(summary),
            published_at=iso_from_struct(e.get("published_parsed") or e.get("updated_parsed")),
        ))
    return items


def collect(source, fetcher, conn, settings) -> list[RawItem]:
    r = fetcher.get(source.url)
    if r.not_modified:
        return []
    return parse_feed(r.text)
