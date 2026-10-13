"""J2: the model reads each new or changed posting into structured fields (prompts/job_extract.md).

Skills are mapped to canonical names (config/skills.yaml) so they can be counted. A salary the board
publishes wins over one read from the text."""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from pathlib import Path

import yaml

from ..db import now
from ..llm import BudgetExceeded
from ..pipeline.score import load_prompt
from .filters import FAMILIES

log = logging.getLogger(__name__)

BATCH = 5
TEXT_CHARS = 7000    # of each posting's text; requirements sit in the first few thousand characters
STRINGS = {"type": "array", "items": {"type": "string"}}
ITEM = {
    "type": "object",
    "properties": {
        "id": {"type": "integer"},
        "role_family": {"type": "string", "enum": list(FAMILIES)},
        "seniority": {"type": "string", "enum": ["intern", "junior", "mid", "senior", "staff", "principal", "lead",
                                                  "manager", "unknown"]},
        "years_min": {"type": "integer"},
        "skills_required": STRINGS,
        "skills_preferred": STRINGS,
        "customer_facing": {"type": "boolean"},
        "travel": {"type": "string", "enum": ["none", "some", "frequent", "unknown"]},
        "remote_policy": {"type": "string", "enum": ["onsite", "hybrid", "remote", "unknown"]},
        "visa_sponsorship": {"type": "string", "enum": ["yes", "no", "unknown"]},
        "salary_min": {"type": "number"},
        "salary_max": {"type": "number"},
        "salary_currency": {"type": "string"},
        "salary_period": {"type": "string", "enum": ["year", "month", "hour", "unknown"]},
        "summary": {"type": "string"},
    },
    "additionalProperties": False,
}
ITEM["required"] = list(ITEM["properties"])
SCHEMA = {"type": "object", "properties": {"postings": {"type": "array", "items": ITEM}}, "required": ["postings"],
          "additionalProperties": False}


class Skills:
    """Canonical skill names and their aliases, from config/skills.yaml."""

    def __init__(self, path: Path):
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        self.category, self.alias = {}, {}
        for category, names in raw.items():
            for name, aliases in (names or {}).items():
                self.category[name] = category
                for a in [name] + list(aliases or []):
                    self.alias[self._key(a)] = name

    @staticmethod
    def _key(s: str) -> str:
        return re.sub(r"[\s_]+", " ", s.strip().lower())

    @property
    def names(self) -> list[str]:
        return list(self.category)

    def canonical(self, s: str) -> str:
        s = re.sub(r"\s+", " ", s.strip()).strip(".;,")
        return self.alias.get(self._key(s), s)

    def normalize(self, items: list[str], exclude: set[str] = frozenset()) -> list[str]:
        out = []
        for s in items or []:
            name = self.canonical(s)
            if name and name not in out and name not in exclude:
                out.append(name)
        return out


def _payload(row) -> dict:
    return {"id": row["id"], "company": row["company"], "title": row["title"],
            "locations": json.loads(row["locations"] or "[]"), "department": row["department"] or "",
            "text": (row["description"] or "")[:TEXT_CHARS]}


def _clean(item: dict, row, skills: Skills) -> dict:
    required = skills.normalize(item.get("skills_required"))
    data = {k: item.get(k) for k in ITEM["properties"] if not k.startswith("salary") and k != "id"}
    data["skills_required"] = required
    data["skills_preferred"] = skills.normalize(item.get("skills_preferred"), exclude=set(required))
    board = json.loads(row["salary"]) if row["salary"] else None
    if board:
        data["salary"] = {**board, "source": "board"}
    elif item.get("salary_min") and item.get("salary_max") and item.get("salary_currency"):
        data["salary"] = {"min": item["salary_min"], "max": item["salary_max"], "currency": item["salary_currency"],
                          "period": item.get("salary_period") or "unknown", "source": "text"}
    else:
        data["salary"] = None
    return data


def extract_postings(conn: sqlite3.Connection, llm, *, model: str, prompts_dir: Path, skills: Skills,
                     limit: int = 200) -> dict:
    """Extracts open postings that have no extraction for this prompt version or text, newest first."""
    system, version = load_prompt(prompts_dir, "job_extract", "")
    system = system.replace("{skills}", ", ".join(skills.names))
    rows = conn.execute(
        "SELECT p.* FROM job_postings p LEFT JOIN job_extractions e ON e.posting_id = p.id"
        " WHERE p.closed_at IS NULL AND (e.posting_id IS NULL OR e.text_hash != p.text_hash"
        " OR e.prompt_version != ?) ORDER BY COALESCE(p.posted_at, p.first_seen) DESC LIMIT ?",
        (version, limit)).fetchall()
    stats = {"todo": len(rows), "extracted": 0, "failed": 0}
    by_id = {r["id"]: r for r in rows}
    for i in range(0, len(rows), BATCH):
        batch = rows[i:i + BATCH]
        user = "Postings:\n" + json.dumps([_payload(r) for r in batch], ensure_ascii=False)
        try:
            out = llm.json(model=model, system=system, user=user, schema=SCHEMA, max_tokens=6000,
                           effort="low", purpose="jobs")
        except BudgetExceeded as exc:
            log.warning("job extraction stopped: %s", exc)
            stats["budget_stop"] = str(exc)
            break
        except Exception as exc:   # one bad batch shouldn't stop the rest
            log.warning("job extraction batch failed: %s", exc)
            stats["failed"] += len(batch)
            continue
        for item in out.get("postings", []):
            row = by_id.get(item.get("id"))
            if row is None:
                continue
            conn.execute("INSERT OR REPLACE INTO job_extractions VALUES (?, ?, ?, ?, ?)",
                         (row["id"], version, row["text_hash"], json.dumps(_clean(item, row, skills)), now()))
            stats["extracted"] += 1
        conn.commit()
    return stats
