"""LLM judge (Claude Haiku): scores candidate stories against the reader profile, in batches."""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from pathlib import Path

from ..db import now
from .rank import Story

log = logging.getLogger(__name__)
BATCH = 25
SECTIONS = ["big_picture", "models", "tools", "research", "systems", "community", "skip"]
SCALE = {"type": "integer", "enum": [0, 1, 2, 3]}
SCHEMA = {
    "type": "object",
    "properties": {
        "scores": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "relevance": SCALE, "novelty": SCALE, "actionability": SCALE, "credibility": SCALE,
                    "section": {"type": "string", "enum": SECTIONS},
                    "why_it_matters": {"type": "string"},
                },
                "required": ["id", "relevance", "novelty", "actionability", "credibility", "section", "why_it_matters"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["scores"],
    "additionalProperties": False,
}


def load_prompt(prompts_dir: Path, name: str, profile: str) -> tuple[str, str]:
    """Returns (prompt text with the profile filled in, version tag from the first line)."""
    text = (prompts_dir / f"{name}.md").read_text(encoding="utf-8")
    m = re.search(r"version:\s*([\w.\-]+)", text.splitlines()[0])
    return text.replace("{profile}", profile.strip()), (m.group(1) if m else name)


def story_payload(s: Story, source_names: dict[str, str], excerpt: int = 400) -> dict:
    summary = next((i["summary"] for i in s.items if i.get("summary")), "") or ""
    eng = {}
    for i in s.items:
        if i.get("engagement"):
            eng.update({k: v for k, v in json.loads(i["engagement"]).items() if v})
    return {
        "id": s.id,
        "title": s.title,
        "sources": [source_names.get(x, x) for x in s.sources],
        "source_layer": s.layer,
        "summary": summary[:excerpt],
        **({"engagement": eng} if eng else {}),
    }


def judge(conn: sqlite3.Connection, llm, stories: list[Story], *, model: str, prompts_dir: Path,
          profile: str, source_names: dict[str, str]) -> int:
    """Score stories that have no score for the current prompt version. Returns the number scored."""
    system, version = load_prompt(prompts_dir, "judge", profile)
    existing = {r["story_id"]: dict(r) for r in conn.execute(
        "SELECT * FROM scores WHERE prompt_version = ?", (version,))}
    todo = []
    for s in stories:
        if s.id in existing:
            s.score = existing[s.id]
        else:
            todo.append(s)

    by_id = {s.id: s for s in todo}
    for i in range(0, len(todo), BATCH):
        batch = todo[i:i + BATCH]
        user = "Score these stories:\n" + json.dumps([story_payload(s, source_names) for s in batch], ensure_ascii=False)
        try:
            result = llm.json(model=model, system=system, user=user, schema=SCHEMA,
                              max_tokens=8000, effort="low", purpose="judge")
        except Exception as exc:  # one failed batch shouldn't sink the brief
            log.warning("judge batch %d failed: %s", i // BATCH, exc)
            continue
        for sc in result.get("scores", []):
            s = by_id.get(sc.get("id"))
            if s is None:
                continue
            s.score = sc
            conn.execute(
                "INSERT OR REPLACE INTO scores VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (s.id, version, model, now(), sc["relevance"], sc["novelty"], sc["actionability"],
                 sc["credibility"], sc["section"], sc["why_it_matters"]),
            )
        conn.commit()
    return sum(1 for s in todo if s.score)
