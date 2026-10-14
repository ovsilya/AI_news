"""RSS / Atom feeds (also used for filtered feeds and Google News search proxies)."""
from __future__ import annotations

from collections import Counter
from urllib.parse import urlencode

import feedparser

from . import RawItem, html_to_text, iso_from_struct

SUMMARY_CHARS = 4000   # enough for the editor to explain the story; dedup only uses the first 200


def parse_feed(text: str) -> list[RawItem]:
    feed = feedparser.parse(text)
    items, guids = [], []
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
        guids.append(e.get("id") or "")
    return _keep_anchors(items, guids)


def _keep_anchors(items: list[RawItem], guids: list[str]) -> list[RawItem]:
    """Docs changelogs link every entry to one page, told apart only by its #anchor, which canonical URLs drop.
    Repeat the anchor in the query so each entry stays its own item (the CHANGELOG collector uses ?v=); entries
    that even share an anchor (two releases on one day) are told apart by their GUID."""
    pages = Counter(it.url.partition("#")[0] for it in items if "#" in it.url)
    links = Counter(it.url for it in items)
    for it, guid in zip(items, guids):
        page, _, anchor = it.url.partition("#")
        if anchor and pages[page] > 1:
            key = guid if links[it.url] > 1 and guid else anchor
            it.url = f"{page}{'&' if '?' in page else '?'}{urlencode({'entry': key})}#{anchor}"
    return items


def collect(source, fetcher, conn, settings) -> list[RawItem]:
    r = fetcher.get(source.url)
    if r.not_modified:
        return []
    return parse_feed(r.text)
