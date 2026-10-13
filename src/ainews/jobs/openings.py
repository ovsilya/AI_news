"""J3: openings for you. Each day the new postings that fit you best go to the channel, ranked in code
(no model call), and each posting is sent once.

- Kept: open postings the model has read, published (else first seen) in the last week, in your role
  families and markets (private/openings.yaml), not at a level you skip, not asking for far more years
  than you have, and never sent before.
- Fit, 0-100: 70 points for skills and 30 for level. The posting's required skills count in full and its
  preferred ones at half; a skill you have (private/skills.yaml) earns all of its weight, one you're
  learning half.
- The same role posted in several cities is shown once, with all its places, and no employer takes more
  than two of the day's places."""
from __future__ import annotations

import html
import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from ..db import finish_run, now, start_run
from ..deliver.render import DIVIDER, _day, _pack
from .boards import load_companies
from .extract import Skills
from .filters import FAMILIES, MARKETS
from .stats import CURRENCY, TARGET, _renormalize, _when, load_fit_profile

FRESH = timedelta(days=7)
SKILL_POINTS, LEVEL_POINTS = 70, 30
LEVEL_SHARE = {"unknown": 0.6, "mid": 0.4, "manager": 0.3}   # of the level points; your levels get them all
OTHER_LEVEL_SHARE = 0.3
EXTRA_YEARS = 3          # a posting may ask for this many years more than you have
DEFAULTS = {"families": list(TARGET), "markets": list(MARKETS), "levels": ["senior", "staff", "principal", "lead"],
            "skip_levels": ["intern", "junior"], "years": None, "top": 8, "min_fit": 50}
SHOW_SKILLS = 8
FOOTER = ("Fit = your skills against the posting's (required in full, preferred at half), plus seniority."
          " Each posting is sent once.")
NO_PROFILE = " Add private/skills.yaml to rank by your skills."
PER_EMPLOYER = 2


def load_prefs(path: Path) -> dict:
    """private/openings.yaml, over the defaults. Unknown families or markets are an error, not a silent miss."""
    raw = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}) if path.exists() else {}
    prefs = {**DEFAULTS, **{k: v for k, v in raw.items() if v is not None}}
    for key, known in (("families", FAMILIES), ("markets", MARKETS)):
        unknown = [v for v in prefs[key] if v not in known]
        if unknown:
            raise ValueError(f"{path.name}: unknown {key} {unknown}; use {list(known)}")
    return prefs


def fit_score(data: dict, profile: dict | None, prefs: dict) -> int:
    required, preferred = data.get("skills_required") or [], data.get("skills_preferred") or []
    weight = len(required) + 0.5 * len(preferred)
    if profile is None or not weight:
        skills = 0.5    # nothing to compare: neither a match nor a miss
    else:
        have, learning = set(profile["have"]), set(profile["learning"])

        def w(s: str) -> float:
            return 1.0 if s in have else 0.5 if s in learning else 0.0

        skills = (sum(map(w, required)) + 0.5 * sum(map(w, preferred))) / weight
    level = data.get("seniority") or "unknown"
    share = 1.0 if level in prefs["levels"] else LEVEL_SHARE.get(level, OTHER_LEVEL_SHARE)
    return round(SKILL_POINTS * skills + LEVEL_POINTS * share)


def _wanted(row, data: dict, prefs: dict, since: datetime) -> bool:
    if _when(row) < since or data.get("role_family") not in prefs["families"]:
        return False
    if not set(json.loads(row["markets"])) & set(prefs["markets"]):
        return False
    if (data.get("seniority") or "unknown") in prefs["skip_levels"]:
        return False
    years = prefs.get("years")
    return not (years is not None and (data.get("years_min") or 0) > years + EXTRA_YEARS)


def pick_openings(conn: sqlite3.Connection, skills: Skills, profile: dict | None, prefs: dict, names: dict[str, str],
                  *, now_dt: datetime | None = None) -> dict:
    """{"picked": best groups first, "considered": postings that passed the filters, "unread": new postings
    in your role families the model hasn't read yet}."""
    now_dt = now_dt or datetime.now(timezone.utc)
    since = now_dt - FRESH
    rows = conn.execute(
        "SELECT p.*, e.data FROM job_postings p JOIN job_extractions e ON e.posting_id = p.id"
        " LEFT JOIN job_alerts a ON a.posting_id = p.id WHERE p.closed_at IS NULL AND a.posting_id IS NULL").fetchall()
    scored = []
    for r in rows:
        data = _renormalize(json.loads(r["data"]), skills)
        if _wanted(r, data, prefs, since):
            scored.append((fit_score(data, profile, prefs), _when(r), r, data))
    scored.sort(key=lambda x: (-x[0], -x[1].timestamp(), x[2]["id"]))

    groups: dict[tuple[str, str], dict] = {}
    for fit, when, r, data in scored:
        key = (r["company"], " ".join(r["title"].casefold().split()))
        places = json.loads(r["locations"] or "[]") or [MARKETS[m] for m in json.loads(r["markets"])]
        if key in groups:   # the same role in another city: one entry, all its places
            g = groups[key]
            g["ids"].append(r["id"])
            g["places"] += [p for p in places if p not in g["places"]]
            continue
        groups[key] = {"ids": [r["id"]], "fit": fit, "posted": when.isoformat(), "company": names.get(r["company"], r["company"]),
                       "title": " ".join(r["title"].split()), "url": r["url"], "places": places, "family": data["role_family"],
                       **_skills_for(data, profile),
                       **{k: data.get(k) for k in ("seniority", "remote_policy", "visa_sponsorship", "salary", "summary")}}
    picked, per_employer = [], {}
    for g in groups.values():
        if g["fit"] < prefs["min_fit"] or len(picked) == prefs["top"]:
            break
        if per_employer.get(g["company"], 0) < PER_EMPLOYER:
            per_employer[g["company"]] = per_employer.get(g["company"], 0) + 1
            picked.append(g)

    unread = conn.execute(
        "SELECT p.title_family, p.markets, p.posted_at, p.first_seen FROM job_postings p"
        " LEFT JOIN job_extractions e ON e.posting_id = p.id LEFT JOIN job_alerts a ON a.posting_id = p.id"
        " WHERE p.closed_at IS NULL AND e.posting_id IS NULL AND a.posting_id IS NULL").fetchall()
    waiting = sum(1 for r in unread if r["title_family"] in prefs["families"] and _when(r) >= since
                  and set(json.loads(r["markets"])) & set(prefs["markets"]))
    return {"picked": picked, "considered": len(scored), "unread": waiting}


def _skills_for(data: dict, profile: dict | None) -> dict:
    """Which of the posting's skills you have, are learning, or would be new (required ones only)."""
    if profile is None:
        return {"have": [], "learning": [], "missing": [], "required": 0, "required_have": 0}
    have, learning = set(profile["have"]), set(profile["learning"])
    required = data.get("skills_required") or []
    both = required + (data.get("skills_preferred") or [])
    return {"have": [s for s in both if s in have], "learning": [s for s in both if s in learning],
            "missing": [s for s in required if s not in have and s not in learning],
            "required": len(required), "required_have": sum(1 for s in required if s in have)}


def _pay(sal: dict | None) -> str:
    if not sal or not sal.get("min") or not sal.get("max"):
        return ""
    cur = CURRENCY.get(sal.get("currency") or "", f"{sal.get('currency') or ''} ")
    lo, hi, period = sal["min"], sal["max"], sal.get("period") or "unknown"
    if period == "year" or (period == "unknown" and lo >= 10000):
        return f"{cur}{round(lo / 1000)}k–{cur}{round(hi / 1000)}k a year"
    if period in ("month", "hour"):
        return f"{cur}{lo:,.0f}–{cur}{hi:,.0f} a{'n' * (period == 'hour')} {period}"
    return f"{cur}{lo:,.0f}–{cur}{hi:,.0f}"


def _places(places: list[str]) -> str:
    """'San Francisco, CA | New York City, NY' and 'San Francisco, California, United States' -> two cities."""
    seen, out = set(), []
    for place in (p.strip() for loc in places for p in re.split(r"[|;]", loc)):
        city = place.split(",")[0].strip().casefold()
        if place and city not in seen:
            seen.add(city)
            out.append(", ".join(x.strip() for x in place.split(",")[:2]))
    return " / ".join(out[:2]) + (f" +{len(out) - 2} more" if len(out) > 2 else "")


def _facts(g: dict) -> list[str]:
    parts = [_places(g["places"])]
    if g.get("remote_policy") in ("remote", "hybrid", "onsite"):
        parts.append(g["remote_policy"])
    if g.get("seniority") not in (None, "unknown"):
        parts.append(g["seniority"])
    if g.get("visa_sponsorship") == "yes":
        parts.append("visa sponsorship")
    return parts


def _skill_lines(g: dict) -> list[tuple[str, str]]:
    out = []
    if g["have"]:
        count = f" ({g['required_have']} of {g['required']} required)" if g["required"] else ""
        out.append(("✅ Yours", ", ".join(g["have"][:SHOW_SKILLS]) + count))
    if g["learning"]:
        out.append(("📚 Learning", ", ".join(g["learning"][:SHOW_SKILLS])))
    if g["missing"]:
        out.append(("➕ New to you", ", ".join(g["missing"][:SHOW_SKILLS])))
    return out


def title_for(day: datetime) -> str:
    return f"Jobs for you · {day:%a} {day.day} {day:%b}"


def render(picked: list[dict], considered: int, unread: int, title: str, footer: str) -> tuple[str, list[str]]:
    e = html.escape
    lead = (f"{len(picked)} best match{'es' * (len(picked) != 1)} from {considered} new posting{'s' * (considered != 1)}"
            " in your roles and markets this week, best fit first.")
    wait = f"{unread} more new postings wait for the model to read them." if unread else ""
    md = [f"# {title}", "", lead] + ([wait] if wait else []) + [""]
    units = [f"💼 <b>{e(title)}</b>\n{e(lead)}" + (f"\n<i>{e(wait)}</i>" if wait else "")]
    for i, g in enumerate(picked, 1):
        posted = _day(datetime.fromisoformat(g["posted"]))
        facts, pay = " · ".join(_facts(g)), _pay(g.get("salary"))
        tail = f"{FAMILIES[g['family']]} · posted {posted} · fit {g['fit']}"
        md += [f"## {i}. {g['title']} · {g['company']}", "", f"📍 {facts}"] + ([f"💰 {pay}"] if pay else [])
        md += [f"_{g['summary']}_"] if g.get("summary") else []
        md += [f"{label}: {text}" for label, text in _skill_lines(g)]
        md += [f"🔗 [Open the posting]({g['url']}) · {tail}", ""]
        unit = [f"{DIVIDER}", f"<b>{i}. {e(g['title'])}</b> · {e(g['company'])}", f"📍 {e(facts)}"]
        unit += [f"💰 {e(pay)}"] if pay else []
        unit += [f"<i>{e(g['summary'])}</i>"] if g.get("summary") else []
        unit += [f"{label}: {e(text)}" for label, text in _skill_lines(g)]
        unit.append(f'🔗 <a href="{e(g["url"], quote=True)}">Open the posting</a> · {e(tail)}')
        units.append("\n".join(unit))
    md += ["---", footer]
    units.append(f"{DIVIDER}\n<i>{e(footer)}</i>")
    return "\n".join(md), _pack(units)


def build_openings(conn: sqlite3.Connection, settings, *, top: int | None = None,
                   now_dt: datetime | None = None) -> dict:
    """Ranks this week's unsent postings and stores the message (kind "jobs_openings"); nothing is sent here."""
    now_dt = now_dt or datetime.now(timezone.utc)
    run_id = start_run(conn, "openings")
    try:
        skills = Skills(settings.skills_path)
        prefs = load_prefs(settings.openings_path)
        if top:
            prefs["top"] = top
        profile = load_fit_profile(settings.fit_path, skills)
        names = {c.id: c.name for c in load_companies(settings.companies_path)}
        found = pick_openings(conn, skills, profile, prefs, names, now_dt=now_dt)
        result = {"report_id": None, **found, "markdown": "", "telegram": [], "has_profile": profile is not None}
        if found["picked"]:
            local = now_dt.astimezone()
            title = title_for(local)
            footer = FOOTER + ("" if profile else NO_PROFILE)
            result["markdown"], result["telegram"] = render(found["picked"], found["considered"], found["unread"],
                                                            title, footer)
            stats = {"posting_ids": [i for g in found["picked"] for i in g["ids"]],
                     "fits": {str(i): g["fit"] for g in found["picked"] for i in g["ids"]},
                     "considered": found["considered"], "unread": found["unread"]}
            result["report_id"] = conn.execute(
                "INSERT INTO reports (created_at, kind, stats, markdown, telegram_html) VALUES (?, 'jobs_openings', ?, ?, ?)",
                (now(), json.dumps(stats), result["markdown"], json.dumps(result["telegram"]))).lastrowid
            conn.commit()
        finish_run(conn, run_id, "ok", {"picked": len(found["picked"]), "considered": found["considered"],
                                        "unread": found["unread"]})
        return result
    except Exception as exc:
        finish_run(conn, run_id, f"error: {exc}"[:300], {})
        raise


def mark_sent(conn: sqlite3.Connection, report_id: int, message_ids: list[int]) -> int:
    """Records the message ids and the postings sent, so they aren't sent again. Returns how many postings."""
    stats = json.loads(conn.execute("SELECT stats FROM reports WHERE id = ?", (report_id,)).fetchone()["stats"])
    conn.execute("UPDATE reports SET telegram_message_ids = ? WHERE id = ?", (json.dumps(message_ids), report_id))
    sent = now()
    conn.executemany("INSERT OR IGNORE INTO job_alerts VALUES (?, ?, ?, ?)",
                     [(pid, report_id, stats["fits"].get(str(pid)), sent) for pid in stats["posting_ids"]])
    conn.commit()
    return len(stats["posting_ids"])
