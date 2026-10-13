"""RSS / Atom feeds (also used for filtered feeds and Google News search proxies)."""
from __future__ import annotations

import feedparser

from . import RawItem, html_to_text, iso_from_struct

SUMMARY_CHARS = 4000   # enough for the editor to explain the story; dedup only uses the first 200


def parse_feed(text: str) -> list[RawItem]:
    feed = feedparser.parse(text)
    items = []
    for e in feed.entries:
        link = e.get("link") or ""
        title = html_to_text(e.get("title"), 300)
        if not link or not title:
            continue
        # Many blogs and Substacks put a teaser in <summary> and the whole post in <content>: keep the longer.
        texts = [e.get("summary") or ""] + [c.get("value", "") for c in e.get("content") or []]
        summary = max((html_to_text(t, SUMMARY_CHARS) for t in texts), key=len)
        items.append(RawItem(
            url=link,
            title=title,
            summary=summary,
            published_at=iso_from_struct(e.get("published_parsed") or e.get("updated_parsed")),
        ))
    return items


def collect(source, fetcher, conn, settings) -> list[RawItem]:
    r = fetcher.get(source.url)
    if r.not_modified:
        return []
    return parse_feed(r.text)
