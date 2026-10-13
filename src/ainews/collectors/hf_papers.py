"""Hugging Face Daily Papers API: community-upvoted papers."""
from __future__ import annotations

import json

from . import RawItem, html_to_text


def parse_papers(text: str, min_upvotes: int) -> list[RawItem]:
    out = []
    for row in json.loads(text):
        paper = row.get("paper") or {}
        pid, upvotes = paper.get("id"), paper.get("upvotes", 0) or 0
        if not pid or upvotes < min_upvotes:
            continue
        out.append(RawItem(
            url=f"https://huggingface.co/papers/{pid}",
            title=html_to_text(paper.get("title") or row.get("title"), 300),
            summary=html_to_text(paper.get("summary") or row.get("summary")),
            published_at=row.get("publishedAt") or paper.get("publishedAt"),
            engagement={"upvotes": upvotes, "comments": row.get("numComments", 0)},
        ))
    return out


def collect(source, fetcher, conn, settings) -> list[RawItem]:
    headers = {"Authorization": f"Bearer {settings.hf_token}"} if settings.hf_token else None
    # Upvotes change during the day, so always fetch fresh (no conditional GET).
    r = fetcher.get(source.url, conditional=False, headers=headers, params={"limit": 100})
    return parse_papers(r.text, source.min_upvotes)
