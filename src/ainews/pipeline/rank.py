"""Heat score, candidate selection for the judge, and final selection for the brief."""
from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

HALF_LIFE_H = 24
SECTION_QUOTA = {"big_picture": 4, "models": 4, "tools": 5, "research": 4, "systems": 3, "community": 3}
TOP_N = 5
EXPLORE_N = 2      # high-heat stories the judge rated low, so the brief doesn't become a bubble
MIN_SCORE = 0.35


@dataclass
class Story:
    id: int
    title: str
    layer: str | None
    heat: float
    first_seen: str
    items: list[dict] = field(default_factory=list)
    score: dict | None = None
    section: str | None = None

    @property
    def sources(self) -> list[str]:
        return list(dict.fromkeys(i["source_id"] for i in self.items))


def _age_hours(ts: str, now: datetime) -> float:
    t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return max(0.0, (now - t).total_seconds() / 3600)


def compute_heat(items: list[dict], weights: dict[str, float], now: datetime) -> float:
    """Each independent source counts once (weighted, decaying), plus normalised engagement."""
    per_source: dict[str, float] = {}
    points = upvotes = 0
    for it in items:
        # Age from publication when the source gives a date (a first run fetches week-old posts too).
        age = _age_hours(it["fetched_at"], now)
        if it.get("published_at"):
            try:
                age = max(age, _age_hours(it["published_at"], now))
            except ValueError:
                pass
        decay = 0.5 ** (age / HALF_LIFE_H)
        w = weights.get(it["source_id"], 1.0) * decay
        per_source[it["source_id"]] = max(per_source.get(it["source_id"], 0.0), w)
        eng = json.loads(it["engagement"]) if it.get("engagement") else {}
        points = max(points, eng.get("points", 0) or 0)
        upvotes = max(upvotes, eng.get("upvotes", 0) or 0)
    # Engagement is a tiebreaker, worth at most about half a source: it shouldn't let one busy
    # platform (HF upvotes, HN points) crowd out stories reported by several outlets.
    engagement = 0.5 * min(1.0, math.log1p(points) / math.log1p(1000)) + 0.5 * min(1.0, math.log1p(upvotes) / math.log1p(200))
    return round(sum(per_source.values()) + engagement, 4)


def load_candidates(conn: sqlite3.Connection, weights: dict[str, float], since: str, limit: int = 200,
                    now: datetime | None = None) -> list[Story]:
    """Stories with new items since `since`, not already shown in a brief in the last 7 days, hottest first."""
    now = now or datetime.now(timezone.utc)
    shown = set()
    week_ago = (now - timedelta(days=7)).isoformat(timespec="seconds")
    for row in conn.execute("SELECT story_ids FROM briefs WHERE created_at >= ?", (week_ago,)):
        shown.update(json.loads(row["story_ids"]))

    stories = []
    for row in conn.execute("SELECT * FROM stories WHERE last_seen >= ?", (since,)):
        if row["id"] in shown:
            continue
        items = [dict(r) for r in conn.execute(
            "SELECT source_id, url, title, summary, published_at, fetched_at, discussion_url, engagement"
            " FROM items WHERE story_id = ? ORDER BY id", (row["id"],))]
        heat = compute_heat(items, weights, now)
        conn.execute("UPDATE stories SET heat = ? WHERE id = ?", (heat, row["id"]))
        stories.append(Story(row["id"], row["title"], row["layer"], heat, row["first_seen"], items))
    conn.commit()
    stories.sort(key=lambda s: s.heat, reverse=True)
    return _diverse(stories, limit)


def _diverse(stories: list[Story], limit: int) -> list[Story]:
    """Hottest first, but every layer gets a fair share before the rest is filled by heat."""
    per_layer = max(1, limit // max(1, len(SECTION_QUOTA)))
    taken, counts = [], {}
    for s in stories:
        if counts.get(s.layer, 0) < per_layer:
            taken.append(s)
            counts[s.layer] = counts.get(s.layer, 0) + 1
    ids = {s.id for s in taken}
    taken += [s for s in stories if s.id not in ids][: max(0, limit - len(taken))]
    taken.sort(key=lambda s: s.heat, reverse=True)
    return taken[:limit]


def final_score(story: Story, max_heat: float) -> float:
    s = story.score or {}
    judge = (2 * s.get("relevance", 0) + s.get("novelty", 0) + s.get("actionability", 0) + s.get("credibility", 0)) / 15
    return judge * (0.6 + 0.4 * (story.heat / max_heat if max_heat else 0))


def select(stories: list[Story]) -> list[Story]:
    """Top N overall, then per-section quotas, then a few exploration picks. Sets story.section."""
    scored = [s for s in stories if s.score and s.score.get("section") != "skip"]
    if not scored:
        return []
    max_heat = max(s.heat for s in stories) or 1.0
    ranked = sorted(scored, key=lambda s: final_score(s, max_heat), reverse=True)
    chosen: list[Story] = []
    for s in ranked:
        if len(chosen) == TOP_N:
            break
        if final_score(s, max_heat) >= MIN_SCORE:
            s.section = "top"
            chosen.append(s)
    used = {s.id for s in chosen}
    for section, quota in SECTION_QUOTA.items():
        picks = [s for s in ranked if s.id not in used and s.score["section"] == section
                 and final_score(s, max_heat) >= MIN_SCORE][:quota]
        for s in picks:
            s.section = section
            used.add(s.id)
        chosen.extend(picks)
    explore = [s for s in sorted(scored, key=lambda s: s.heat, reverse=True)
               if s.id not in used and s.score.get("relevance", 0) <= 1][:EXPLORE_N]
    for s in explore:
        s.section = s.score["section"]
    return chosen + explore


def select_by_heat(stories: list[Story]) -> list[Story]:
    """No-LLM mode: the hottest stories overall, then each layer's quota by heat."""
    ranked = sorted(stories, key=lambda s: s.heat, reverse=True)
    chosen = ranked[:TOP_N]
    for s in chosen:
        s.section = "top"
    used = {s.id for s in chosen}
    for section, quota in SECTION_QUOTA.items():
        picks = [s for s in ranked if s.id not in used and (s.layer or "community") == section][:quota]
        for s in picks:
            s.section = section
            used.add(s.id)
        chosen.extend(picks)
    return chosen
