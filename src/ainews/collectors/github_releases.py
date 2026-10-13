"""GitHub releases.atom for a list of repos (no auth, no API rate limit)."""
from __future__ import annotations

import logging
import re

from .rss import parse_feed

log = logging.getLogger(__name__)
# Version suffixes like 0.12.0rc1, 4.0.0-beta.2, 1.2.0.dev0, plus standalone "nightly"/"preview".
PRERELEASE = re.compile(
    r"\d[.\-_]?(rc|alpha|beta|dev|nightly|preview|pre)[.\-_]?\d*\b|\b(nightly|preview)\b", re.IGNORECASE
)


def collect(source, fetcher, conn, settings):
    out, failures = [], []
    for repo in source.repos:
        try:
            r = fetcher.get(f"https://github.com/{repo}/releases.atom")
        except Exception as exc:  # one broken repo shouldn't hide the others
            failures.append(f"{repo}: {exc}")
            continue
        if r.not_modified:
            continue
        for item in parse_feed(r.text):
            if PRERELEASE.search(item.title):
                continue
            name = repo.split("/", 1)[1]
            if name.lower() not in item.title.lower():
                item.title = f"{name} {item.title}"
            out.append(item)
    if failures and len(failures) == len(source.repos):
        raise RuntimeError("; ".join(failures))
    for f in failures:
        log.warning("%s: %s", source.id, f)
    return out
