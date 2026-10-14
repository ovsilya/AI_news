"""Podcasts through Apple's public podcast directory (the iTunes lookup API, no key).

Many podcast feeds link an episode only to its audio file (No Priors' does), which leaves the brief nothing
to link to. The directory gives every episode its own page, with the date and the show notes."""
from __future__ import annotations

import json
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from . import RawItem, html_to_text
from .rss import SUMMARY_CHARS


def _page(url: str) -> str:
    """The episode page without Apple's referral tag (uo=4)."""
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query) if k != "uo"]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def parse_lookup(text: str) -> list[RawItem]:
    out = []
    for r in json.loads(text).get("results", []):
        if r.get("wrapperType") != "podcastEpisode" or not r.get("trackViewUrl") or not r.get("trackName"):
            continue   # the first result describes the show itself
        out.append(RawItem(
            url=_page(r["trackViewUrl"]),
            title=html_to_text(r["trackName"], 300),
            summary=html_to_text(r.get("description"), SUMMARY_CHARS),
            published_at=r.get("releaseDate"),
        ))
    return out


def collect(source, fetcher, conn, settings) -> list[RawItem]:
    r = fetcher.get(source.url)
    if r.not_modified:
        return []
    return parse_lookup(r.text)
