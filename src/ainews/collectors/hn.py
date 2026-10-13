"""Hacker News via the Algolia search API: stories above a points threshold."""
from __future__ import annotations

import json
import time

from . import RawItem, html_to_text, iso_from_epoch


def parse_hits(text: str) -> list[RawItem]:
    out = []
    for h in json.loads(text).get("hits", []):
        if not h.get("title"):
            continue
        discussion = f"https://news.ycombinator.com/item?id={h['objectID']}"
        out.append(RawItem(
            url=h.get("url") or discussion,
            title=h["title"],
            summary=html_to_text(h.get("story_text"), 600),
            published_at=iso_from_epoch(h.get("created_at_i")),
            discussion_url=discussion,
            engagement={"points": h.get("points", 0), "comments": h.get("num_comments", 0)},
        ))
    return out


def collect(source, fetcher, conn, settings, lookback_hours: int = 36) -> list[RawItem]:
    since = int(time.time()) - lookback_hours * 3600
    params = {
        "tags": "story",
        "numericFilters": f"points>={source.min_points},created_at_i>{since}",
        "hitsPerPage": 200,
    }
    r = fetcher.get(source.url, conditional=False, params=params)
    return parse_hits(r.text)
