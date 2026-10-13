"""Editor: writes the brief as structured JSON, in a teaching style. Links are added by us from story_ids,
so the model never produces a URL."""
from __future__ import annotations

import json
from pathlib import Path

from .rank import Story
from .score import load_prompt

SECTION_ORDER = ["top", "big_picture", "trends", "models", "tools", "research", "systems", "community"]
SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "enum": SECTION_ORDER},
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "story_ids": {"type": "array", "items": {"type": "integer"}},
                                "title": {"type": "string"},
                                "summary": {"type": "string"},      # what happened
                                "explainer": {"type": "string"},    # "What is it?": the idea, in plain words
                                "pro_take": {"type": "string"},     # "Comment from a pro": why it matters to the reader
                            },
                            "required": ["story_ids", "title", "summary", "explainer", "pro_take"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["key", "items"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["headline", "sections"],
    "additionalProperties": False,
}


def source_text(s: Story, limit: int = 900) -> str:
    """All the text the editor may draw facts from for one story (also used by verify)."""
    parts = [s.title]
    for it in s.items:
        parts.append(it["title"])
        if it.get("summary"):
            parts.append(it["summary"])
    text = "\n".join(dict.fromkeys(parts))
    return text[:limit]


def editor_payload(stories: list[Story], source_names: dict[str, str]) -> list[dict]:
    return [{
        "id": s.id,
        "section": s.section,
        "title": s.title,
        "sources": [source_names.get(x, x) for x in s.sources],
        "excerpts": source_text(s),
        "judge_note": (s.score or {}).get("why_it_matters", ""),
    } for s in stories]


def write_brief(llm, stories: list[Story], *, model: str, prompts_dir: Path, profile: str,
                source_names: dict[str, str], date_label: str) -> dict:
    system, version = load_prompt(prompts_dir, "editor", profile)
    user = (f"Today is {date_label}. Selected stories:\n"
            + json.dumps(editor_payload(stories, source_names), ensure_ascii=False))
    brief = llm.json(model=model, system=system, user=user, schema=SCHEMA, max_tokens=24000,
                     effort="medium", purpose="editor", stream=True)
    brief["prompt_version"] = version
    return brief


def brief_without_llm(stories: list[Story]) -> dict:
    """Fallback for --no-llm runs: headlines and source summaries, grouped by section."""
    sections: dict[str, list] = {}
    for s in stories:
        summary = next((i["summary"] for i in s.items if i.get("summary")), "") or ""
        sections.setdefault(s.section or "community", []).append({
            "story_ids": [s.id], "title": s.title,
            "summary": (summary[:280] + "…") if len(summary) > 280 else summary,
            "explainer": "", "pro_take": "",
        })
    return {
        "headline": "Unranked digest (no LLM): the hottest stories by source count and engagement.",
        "sections": [{"key": k, "items": sections[k]} for k in SECTION_ORDER if k in sections],
        "prompt_version": "no-llm",
    }
