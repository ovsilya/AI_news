"""Raw CHANGELOG.md diff: new '## <version>' sections become items."""
from __future__ import annotations

import re

from ..db import get_snapshot, set_snapshot
from . import RawItem

HEADING = re.compile(r"^##\s+\[?v?(\d+\.\d+(?:\.\d+)?[^\]\s]*)\]?.*$", re.MULTILINE)


def parse_changelog(text: str) -> list[tuple[str, str]]:
    """[(version, body)] in file order (newest first in most changelogs)."""
    matches = list(HEADING.finditer(text))
    out = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        out.append((m.group(1), text[m.end():end].strip()))
    return out


def collect(source, fetcher, conn, settings) -> list[RawItem]:
    r = fetcher.get(source.url)
    if r.not_modified:
        return []
    entries = parse_changelog(r.text)
    key = f"changelog:{source.id}"
    seen = set(get_snapshot(conn, key) or [])
    first_run = not seen
    new = [(v, body) for v, body in entries if v not in seen]
    if first_run:
        new = new[:1]  # don't flood the first brief with the whole history
    set_snapshot(conn, key, sorted(seen | {v for v, _ in entries}))
    page = source.repo_url or source.url
    return [
        RawItem(url=f"{page}?v={v}", title=f"{source.name}: version {v}", summary=body[:1200])
        for v, body in new
    ]
