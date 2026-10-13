"""Checks on the editor's output before anything is published.

- every item must cite stories that were actually given to the editor;
- every number in a title or summary must appear in the cited stories' source text,
  otherwise the sentence is dropped (the title falls back to the source headline).
Sentence-level citations via the Citations API come in Phase 2.
"""
from __future__ import annotations

import re

from .editor import SECTION_ORDER, source_text
from .rank import Story

NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
SENTENCE = re.compile(r"(?<=[.!?])\s+")


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "") for n in NUMBER.findall(text)}


def _unsupported(text: str, source: str) -> set[str]:
    return _numbers(text) - _numbers(source)


def verify(brief: dict, stories: list[Story]) -> tuple[dict, list[str]]:
    by_id = {s.id: s for s in stories}
    warnings: list[str] = []
    seen: set[int] = set()
    sections = []
    for sec in brief.get("sections", []):
        if sec.get("key") not in SECTION_ORDER:
            warnings.append(f"unknown section {sec.get('key')!r} dropped")
            continue
        items = []
        for it in sec.get("items", []):
            ids = [i for i in it.get("story_ids", []) if i in by_id]
            if not ids:
                warnings.append(f"item {it.get('title')!r} cites no known story; dropped")
                continue
            if set(ids) & seen:
                warnings.append(f"item {it.get('title')!r} repeats a story already shown; dropped")
                continue
            seen.update(ids)
            src = "\n".join(source_text(by_id[i], limit=4000) for i in ids)
            title = it.get("title", "")
            if _unsupported(title, src):
                warnings.append(f"title {title!r} has unsupported numbers; using source headline")
                title = by_id[ids[0]].title
            kept = []
            for sentence in SENTENCE.split(it.get("summary", "").strip()):
                bad = _unsupported(sentence, src)
                if bad:
                    warnings.append(f"dropped sentence with unsupported {sorted(bad)}: {sentence[:80]!r}")
                else:
                    kept.append(sentence)
            items.append({**it, "story_ids": ids, "title": title, "summary": " ".join(kept)})
        if items:
            sections.append({"key": sec["key"], "items": items})
    sections.sort(key=lambda s: SECTION_ORDER.index(s["key"]))
    return {**brief, "sections": sections}, warnings
