"""Collectors: one function per source kind, each returning RawItems."""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable


@dataclass
class RawItem:
    url: str
    title: str
    summary: str = ""
    published_at: str | None = None   # ISO 8601, UTC
    discussion_url: str | None = None
    engagement: dict = field(default_factory=dict)


_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def html_to_text(s: str | None, limit: int = 1200) -> str:
    """Strip tags and collapse whitespace; good enough for feed summaries."""
    if not s:
        return ""
    text = _WS.sub(" ", html.unescape(_TAG.sub(" ", s))).strip()
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + "…"


def iso_from_struct(t) -> str | None:
    """feedparser time.struct_time (UTC) -> ISO string."""
    if not t:
        return None
    return datetime(*t[:6], tzinfo=timezone.utc).isoformat(timespec="seconds")


def iso_from_epoch(ts: float | int | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds")


def registry() -> dict[str, Callable]:
    from . import changelog, github_releases, hf_papers, hn, openrouter, rss

    return {
        "rss": rss.collect,
        "github_releases": github_releases.collect,
        "changelog": changelog.collect,
        "hf_papers": hf_papers.collect,
        "hn": hn.collect,
        "openrouter": openrouter.collect,
    }
