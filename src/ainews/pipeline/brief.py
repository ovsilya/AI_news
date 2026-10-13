"""Lane ① brief: dedup → heat → judge → select → write → verify → render → (send)."""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone

from ..db import finish_run, now, start_run
from ..deliver.render import to_markdown, to_telegram
from .dedup import assign_stories
from .editor import brief_without_llm, write_brief
from .rank import load_candidates, select, select_by_heat
from .score import judge
from .verify import verify

log = logging.getLogger(__name__)


def _since(conn: sqlite3.Connection, now_dt: datetime) -> str:
    """Stories updated since the last brief (pm edition = changes only), at most 26 h back."""
    floor = (now_dt - timedelta(hours=26)).isoformat(timespec="seconds")
    row = conn.execute("SELECT MAX(created_at) AS t FROM briefs").fetchone()
    return max(row["t"], floor) if row and row["t"] else floor


def build_brief(conn: sqlite3.Connection, settings, sources, *, llm=None, edition: str = "am",
                now_dt: datetime | None = None) -> dict:
    """Builds and stores a brief. Returns {"brief_id", "markdown", "telegram", "warnings", "stats"}."""
    now_dt = now_dt or datetime.now(timezone.utc)
    run_id = start_run(conn, "brief")
    layers = {s.id: s.layer for s in sources}
    weights = {s.id: s.weight for s in sources}
    names = {s.id: s.name for s in sources}
    stats: dict = {"edition": edition, "llm": llm is not None}
    try:
        stats["dedup"] = assign_stories(conn, layers, now=now_dt)
        candidates = load_candidates(conn, weights, _since(conn, now_dt), now=now_dt)
        stats["candidates"] = len(candidates)
        profile = settings.profile_text()
        if llm is not None and candidates:
            stats["judged"] = judge(conn, llm, candidates, model=settings.model_judge,
                                    prompts_dir=settings.prompts_dir, profile=profile, source_names=names)
            chosen = select(candidates)
        else:
            chosen = select_by_heat(candidates)
        stats["selected"] = len(chosen)

        date_label = now_dt.astimezone().strftime("%a %d %b %Y")  # reader's local date (set TZ on Cloud Run)
        if not chosen:
            draft = {"headline": "Nothing new since the last brief.", "sections": [], "prompt_version": "none"}
        elif llm is not None:
            draft = write_brief(llm, chosen, model=settings.model_editor, prompts_dir=settings.prompts_dir,
                                profile=profile, source_names=names, date_label=date_label)
        else:
            draft = brief_without_llm(chosen)
        brief, warnings = verify(draft, chosen)
        stats["warnings"] = len(warnings)
        for w in warnings:
            log.info("verify: %s", w)

        shown = sorted({i for sec in brief["sections"] for it in sec["items"] for i in it["story_ids"]})
        cost = llm.total_cost if llm is not None else 0.0
        title = f"AI Brief · {date_label}" + (" · evening update" if edition == "pm" else "")
        footer = (f"{len(shown)} stories from {stats['candidates']} candidates · "
                  f"{sum(1 for _ in sources)} sources · run cost ${cost:.2f}")
        by_id = {s.id: s for s in chosen}
        markdown = to_markdown(brief, by_id, names, title, footer)
        telegram = to_telegram(brief, by_id, names, title, footer)
        brief_id = conn.execute(
            "INSERT INTO briefs (created_at, edition, story_ids, markdown, telegram_html, cost_usd)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (now(), edition, json.dumps(shown), markdown, json.dumps(telegram), cost),
        ).lastrowid
        conn.commit()
        stats["cost_usd"] = round(cost, 4)
        finish_run(conn, run_id, "ok", stats)
        return {"brief_id": brief_id, "markdown": markdown, "telegram": telegram, "warnings": warnings,
                "stats": stats}
    except Exception as exc:
        finish_run(conn, run_id, f"error: {exc}"[:300], stats)
        raise


def mark_sent(conn: sqlite3.Connection, brief_id: int, message_ids: list[int]) -> None:
    conn.execute("UPDATE briefs SET telegram_message_ids = ? WHERE id = ?", (json.dumps(message_ids), brief_id))
    conn.commit()
