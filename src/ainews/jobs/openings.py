"""J3: openings for you. Each day the new postings that fit you best go to the channel, ranked in code
(no model call), and each posting is sent once.

- Kept: open postings the model has read, published (else first seen) in the last week, in your role
  families and markets (private/openings.yaml), not at a level you skip, not asking for far more years
  than you have, and never sent before.
- Fit, 0-100: 70 points for skills and 30 for level. The posting's required skills count in full and its
  preferred ones at half; a skill you have (private/skills.yaml) earns all of its weight, one you're
  learning half.
- Each opening splits its skills into the ones you have, the required and nice-to-have ones that would be
  new to you, and the ones you're learning, with how much of them you cover (the gap). At the end, "Where
  you stand" does the same for all of the week's roles for you: how big your gap usually is, which of your
  skills they ask for most, which missing ones they ask for most, and what learning the top few would change.
- The same role posted in several cities is shown once, with all its places, and no employer takes more
  than two of the day's places.
- The day's places are shared between three employer groups (each employer's tier in companies.yaml):
  established companies, startups and top tier, 3/3/2 by default. Places a group can't fill go to the
  others, but top tier never gets more than its own, so the list isn't all frontier labs and big names."""
from __future__ import annotations

import html
import json
import re
import sqlite3
import statistics
from collections import Counter
from itertools import groupby
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from ..db import finish_run, now, start_run
from ..deliver.render import DIVIDER, PACK_LIMIT, _day, _pack, visible_len
from .boards import TIERS, Company, load_companies
from .extract import Skills
from .filters import _STATES, FAMILIES, MARKETS
from .stats import CURRENCY, TARGET, _renormalize, _when, load_fit_profile

FRESH = timedelta(days=7)
SKILL_POINTS, LEVEL_POINTS = 70, 30
LEVEL_SHARE = {"unknown": 0.6, "mid": 0.4, "manager": 0.3}   # of the level points; your levels get them all
OTHER_LEVEL_SHARE = 0.3
EXTRA_YEARS = 3          # a posting may ask for this many years more than you have
DEFAULTS = {"families": list(TARGET), "markets": list(MARKETS), "levels": ["senior", "staff", "principal", "lead"],
            "skip_levels": ["intern", "junior"], "years": None,
            "per_tier": {"established": 3, "startup": 3, "top": 2}, "min_fit": 50}
TIER_LABELS = {"established": "🏢 Established", "startup": "🌱 Startups", "top": "🏆 Top tier"}
NO_SPILL = "top"         # the one group that never takes the places another group leaves empty
SHOW_SKILLS = 8
GAP_SIZES = ((80, "small gap", "🟢"), (60, "medium gap", "🟡"), (0, "big gap", "🔴"))   # by the skills you cover
TOP_GAPS = 3        # "learning your top 3 gaps would give N roles a small gap"
MIN_STANDING = 5    # roles needed before "Where you stand" says anything
FOOTER = ("Fit 0–100: 70 for skills (required in full, preferred at half), 30 for level. Skills %: how much of a"
          " posting's skills you cover, a skill you're learning at half; the gap is small from 80%, medium from 60%."
          " Tap a title to open the posting. Each posting is sent once.")
FLAGS = {"US": "🇺🇸", "CA": "🇨🇦", "UK": "🇬🇧", "CH": "🇨🇭", "AE": "🇦🇪"}
# Location parts that name a country, state or province rather than a city: "USA, GA, Atlanta",
# "California - San Francisco", "United States of America, Eagan, Minnesota".
_REGIONS = {x.casefold() for x in _STATES + [
    "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado", "Connecticut", "Delaware", "Florida",
    "Georgia", "Hawaii", "Idaho", "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky", "Louisiana", "Maine",
    "Maryland", "Massachusetts", "Michigan", "Minnesota", "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada",
    "New Hampshire", "New Jersey", "New Mexico", "New York", "North Carolina", "North Dakota", "Ohio", "Oklahoma",
    "Oregon", "Pennsylvania", "Rhode Island", "South Carolina", "South Dakota", "Tennessee", "Texas", "Utah",
    "Vermont", "Virginia", "Washington", "West Virginia", "Wisconsin", "Wyoming", "District of Columbia",
    "ON", "BC", "QC", "AB", "Ontario", "British Columbia", "Quebec", "Québec", "Alberta",
    "US", "USA", "U.S.", "United States", "United States of America", "America", "UK", "United Kingdom",
    "Great Britain", "England", "Scotland", "Canada", "Switzerland", "United Arab Emirates", "UAE"]}
_NOISE = re.compile(r"\s*\([^)]*\)|\s+(office|metro|metro area|area|hq)\b(?=\s*(\(|$))", re.I)   # "San Jose Office (First St)"
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
    prefs["per_tier"] = {**DEFAULTS["per_tier"], **(raw.get("per_tier") or {})}
    bad = {k: v for k, v in prefs["per_tier"].items() if k not in TIERS or not isinstance(v, int) or v < 0}
    if bad:
        raise ValueError(f"{path.name}: per_tier wants a whole number of places for {', '.join(TIERS)}, not {bad}")
    return prefs


def skill_coverage(data: dict, profile: dict | None) -> float | None:
    """How much of a posting's skills you cover, 0-1: its required skills count in full and its preferred ones at
    half; a skill you have earns all of its weight, one you're learning half. None with nothing to compare."""
    required, preferred = data.get("skills_required") or [], data.get("skills_preferred") or []
    weight = len(required) + 0.5 * len(preferred)
    if profile is None or not weight:
        return None
    have, learning = set(profile["have"]), set(profile["learning"])

    def w(s: str) -> float:
        return 1.0 if s in have else 0.5 if s in learning else 0.0

    return (sum(map(w, required)) + 0.5 * sum(map(w, preferred))) / weight


def fit_score(data: dict, profile: dict | None, prefs: dict) -> int:
    cover = skill_coverage(data, profile)
    skills = 0.5 if cover is None else cover   # nothing to compare: neither a match nor a miss
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


def pick_openings(conn: sqlite3.Connection, skills: Skills, profile: dict | None, prefs: dict,
                  employers: dict[str, Company], *, now_dt: datetime | None = None) -> dict:
    """{"picked": by employer group, best fit first in each, "considered": unsent postings that passed the
    filters, "unread": new postings in your role families the model hasn't read yet, "standing": where you
    stand among all of the week's roles for you, sent or not (where_you_stand)}."""
    now_dt = now_dt or datetime.now(timezone.utc)
    since = now_dt - FRESH
    rows = conn.execute(
        "SELECT p.*, e.data, EXISTS (SELECT 1 FROM job_alerts a WHERE a.posting_id = p.id) AS sent"
        " FROM job_postings p JOIN job_extractions e ON e.posting_id = p.id WHERE p.closed_at IS NULL").fetchall()
    scored, roles = [], {}
    for r in rows:
        if r["company"] not in employers:   # taken off the list: no longer polled, so it may have closed
            continue
        data = _renormalize(json.loads(r["data"]), skills)
        if not _wanted(r, data, prefs, since):
            continue
        roles.setdefault((r["company"], " ".join(r["title"].casefold().split())), data)   # each role once
        if not r["sent"]:
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
            g["markets"] += [m for m in json.loads(r["markets"]) if m not in g["markets"]]
            continue
        employer = employers[r["company"]]
        groups[key] = {"ids": [r["id"]], "fit": fit, "posted": when.isoformat(), "company": employer.name,
                       "tier": employer.tier, "title": " ".join(r["title"].split()), "url": r["url"], "places": places,
                       "markets": json.loads(r["markets"]),
                       "family": data["role_family"],
                       **_skills_for(data, profile),
                       **{k: data.get(k) for k in ("seniority", "remote_policy", "visa_sponsorship", "salary", "summary")}}
    unread = conn.execute(
        "SELECT p.title_family, p.markets, p.posted_at, p.first_seen FROM job_postings p"
        " LEFT JOIN job_extractions e ON e.posting_id = p.id LEFT JOIN job_alerts a ON a.posting_id = p.id"
        " WHERE p.closed_at IS NULL AND e.posting_id IS NULL AND a.posting_id IS NULL").fetchall()
    waiting = sum(1 for r in unread if r["title_family"] in prefs["families"] and _when(r) >= since
                  and set(json.loads(r["markets"])) & set(prefs["markets"]))
    return {"picked": _share_out(list(groups.values()), prefs), "considered": len(scored), "unread": waiting,
            "standing": where_you_stand(list(roles.values()), profile)}


def _size(share: float) -> tuple[str, str]:
    """('small gap', '🟢') for a coverage of 0.8 or more; 'no gap' when you cover all of it."""
    pct = round(100 * share)
    if pct >= 100:
        return "no gap", "🟢"
    return next((name, dot) for floor, name, dot in GAP_SIZES if pct >= floor)


def where_you_stand(roles: list[dict], profile: dict | None) -> dict | None:
    """Where you stand among the week's roles for you (each role once): how much of their skills you
    typically cover, how many fall in each gap size, which of your skills they ask for most, which skills
    they ask for that you don't have, and how many would have a small gap if you learned the TOP_GAPS skills
    that would add the most to your coverage."""
    roles = [d for d in roles if skill_coverage(d, profile) is not None]
    if profile is None or len(roles) < MIN_STANDING:
        return None
    have, learning = set(profile["have"]), set(profile["learning"])
    covered = [skill_coverage(d, profile) for d in roles]
    asked, weight = Counter(), Counter()   # roles that list a skill; its share of their coverage, summed
    for d in roles:
        required, preferred = d.get("skills_required") or [], d.get("skills_preferred") or []
        total = len(required) + 0.5 * len(preferred)
        asked.update(set(required + preferred))
        weight.update({s: 1 / total for s in required})
        weight.update({s: 0.5 / total for s in preferred})
    # What learning a skill would add to your coverage (a skill you're learning has half of it to go) picks the
    # ones to learn first; the lists themselves go by how many roles ask for a skill, the number they show.
    gain = {s: w * (0.5 if s in learning else 1.0) for s, w in weight.items() if s not in have}
    first = sorted(gain, key=lambda s: (-gain[s], s))[:TOP_GAPS]
    gaps = sorted(gain, key=lambda s: (-asked[s], -gain[s], s))
    ranked = sorted((s for s in weight if s in have), key=lambda s: (-asked[s], -weight[s], s))
    learned = {"have": list(have | set(first)), "learning": list(learning - set(first))}
    small = GAP_SIZES[0][0] / 100

    def pct(skill: str) -> int:
        return round(100 * asked[skill] / len(roles))

    sizes, ceiling = [], 101
    for floor, name, dot in GAP_SIZES:
        span = f"{floor}%+" if ceiling > 100 else f"{floor}–{ceiling - 1}%" if floor else f"under {ceiling}%"
        sizes.append({"name": name, "dot": dot, "span": span,
                      "roles": sum(1 for c in covered if floor <= round(100 * c) < ceiling)})
        ceiling = floor
    return {"roles": len(roles), "median": statistics.median(covered), "sizes": sizes,
            "strengths": [(s, pct(s)) for s in ranked[:5]],
            "gaps": [(s, pct(s), s in learning) for s in gaps[:6]],
            "top_gaps": first,
            "small_now": sum(1 for c in covered if c >= small),
            "small_after": sum(1 for d in roles if skill_coverage(d, learned) >= small)}


def _share_out(groups: list[dict], prefs: dict) -> list[dict]:
    """Each employer group takes its places (per_tier) with its best fits. Places a group leaves empty go
    to the best of the rest, except to top tier and to a group set to 0. Returned group by group."""
    places = prefs["per_tier"]
    chosen: dict[str, list[dict]] = {t: [] for t in places}
    per_employer: dict[str, int] = {}
    waiting = []

    def take(g: dict) -> None:
        chosen[g["tier"]].append(g)
        per_employer[g["company"]] = per_employer.get(g["company"], 0) + 1

    for g in groups:   # best fit first
        if g["fit"] < prefs["min_fit"]:
            break
        if per_employer.get(g["company"], 0) == PER_EMPLOYER:
            continue
        if len(chosen[g["tier"]]) < places[g["tier"]]:
            take(g)
        elif g["tier"] != NO_SPILL and places[g["tier"]]:
            waiting.append(g)
    spare = sum(places.values()) - sum(map(len, chosen.values()))
    for g in waiting:
        if spare and per_employer.get(g["company"], 0) < PER_EMPLOYER:
            take(g)
            spare -= 1
    return [g for t in places for g in chosen[t]]


def _skills_for(data: dict, profile: dict | None) -> dict:
    """The posting's skills split into the ones you have, are learning, or would be new to you (required and
    preferred apart), and how much of them you cover."""
    required, preferred = data.get("skills_required") or [], data.get("skills_preferred") or []
    if profile is None:
        return {"have": [], "learning": [], "missing": [], "missing_preferred": [], "required": len(required),
                "required_have": 0, "coverage": None}
    have, learning = set(profile["have"]), set(profile["learning"])

    def new(names: list[str]) -> list[str]:
        return [s for s in names if s not in have and s not in learning]

    return {"have": [s for s in required + preferred if s in have],
            "learning": [s for s in required + preferred if s in learning],
            "missing": new(required), "missing_preferred": new(preferred), "required": len(required),
            "required_have": sum(1 for s in required if s in have), "coverage": skill_coverage(data, profile)}


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


def _city(place: str) -> tuple[str, bool]:
    """('Atlanta', False) from 'USA, GA, Atlanta'; ('', True) from 'Remote'. Cities come first after a comma
    ('Austin, TX') and last after a dash ('California - San Francisco'), so that's the fallback when every
    part is also a region's name ('New York - New York'), unless the place is remote ('Remote - Illinois')."""
    place = re.sub(r"\bWashington,?\s*(D\.?C\.?|District of Columbia)\b|\bDistrict of Columbia\s*-\s*Washington\b",
                   "Washington DC", place)
    dashed = " - " in place
    parts = [_NOISE.sub("", p.strip()).strip() for p in place.split(" - " if dashed else ",")]
    remote = any("remote" in p.casefold() for p in parts)
    named = [p for p in parts if p and "remote" not in p.casefold()]
    cities = [p for p in named if p.casefold() not in _REGIONS]
    if cities or remote:   # 'US, CA, Remote' is remote work, not a city
        return (cities[0] if cities else ""), remote
    return (named[-1 if dashed else 0] if named else ""), False


def _where(g: dict) -> str:
    """'San Francisco / New York City 🇺🇸', with '+2 more' past two cities; 'Remote 🇺🇸' when no city is named."""
    cities, remote = [], False
    for place in (p.strip() for loc in g["places"] for p in re.split(r"[|;]", loc)):
        city, rem = _city(place)
        remote |= rem
        if city and city.casefold() not in {c.casefold() for c in cities}:
            cities.append(city)
    flags = "".join(FLAGS[m] for m in g.get("markets") or [] if m in FLAGS)
    where = " / ".join(cities[:2]) + (f" +{len(cities) - 2} more" if len(cities) > 2 else "") or ("Remote" if remote else "")
    return f"{where} {flags}".strip()


def _facts(g: dict) -> list[str]:
    parts = [g["company"], _where(g)]
    if g.get("remote_policy") in ("remote", "hybrid", "onsite"):
        parts.append(g["remote_policy"])
    if g.get("seniority") not in (None, "unknown"):
        parts.append(g["seniority"])
    if g.get("visa_sponsorship") == "yes":
        parts.append("visa sponsorship")
    return [p for p in parts if p]


def _clip(names: list[str]) -> str:
    return ", ".join(names[:SHOW_SKILLS]) + (f" +{len(names) - SHOW_SKILLS}" if len(names) > SHOW_SKILLS else "")


def _bar(share: float) -> str:
    full = round(10 * share)
    return "▰" * full + "▱" * (10 - full)


def _skill_lines(g: dict, posted: str) -> list[str]:
    """Fit, then the posting's skills: how much of them you cover and how big the gap is, the ones you have,
    the ones that would be new to you (required, then nice to have), and the ones you're learning."""
    lines = [f"🎯 Fit {g['fit']} · posted {posted}"]
    if g["coverage"] is None:
        return lines
    name, dot = _size(g["coverage"])
    todo = len(g["missing"])
    lines.append(f"🧩 Skills {_bar(g['coverage'])} {round(100 * g['coverage'])}% · {dot} {name}"
                 + (f", {todo} required skill{'s' * (todo != 1)} new to you" if todo else ""))
    if g["have"]:
        count = f" ({g['required_have']} of {g['required']} required)" if g["required"] else ""
        lines.append(f"✅ You have{count}: {_clip(g['have'])}")
    if g["missing"]:
        lines.append(f"❌ New to you, required: {_clip(g['missing'])}")
    if g["missing_preferred"]:
        lines.append(f"➖ New to you, nice to have: {_clip(g['missing_preferred'])}")
    if g["learning"]:
        lines.append(f"📚 Learning: {_clip(g['learning'])}")
    return lines


def _standing_lines(s: dict) -> list[str]:
    """'Where you stand' among the week's roles for you."""
    lines = [f"🧩 You cover {_bar(s['median'])} {round(100 * s['median'])}% of a typical role's skills",
             " · ".join(f"{b['dot']} {b['roles']} {b['name']} ({b['span']})" for b in s["sizes"])]
    if s["strengths"]:
        lines.append("💪 Most asked of yours: " + ", ".join(f"{name} {pct}%" for name, pct in s["strengths"]))
    if s["gaps"]:
        lines.append("🧱 Most asked that you lack: " + ", ".join(
            f"{name} {pct}%" + (" (learning)" if learning else "") for name, pct, learning in s["gaps"]))
    top = s["top_gaps"]
    if top and s["small_after"] > s["small_now"]:
        names = ", ".join(top[:-1]) + (" and " if len(top) > 1 else "") + top[-1]
        lines.append(f"🚀 Learning {names} would give {s['small_after']} roles a small gap, up from {s['small_now']}")
    return lines


def title_for(day: datetime) -> str:
    return f"Jobs for you · {day:%a} {day.day} {day:%b}"


def _tier_counts(picked: list[dict]) -> str:
    """'🏢 3 established · 🌱 3 startups · 🏆 2 top tier'."""
    out = []
    for tier, in_tier in groupby(picked, key=lambda g: g["tier"]):
        emoji, name = TIER_LABELS[tier].split(" ", 1)
        n = len(list(in_tier))
        out.append(f"{emoji} {n} {name.lower().removesuffix('s') if n == 1 else name.lower()}")
    return " · ".join(out)


def render(picked: list[dict], considered: int, unread: int, title: str, footer: str,
           standing: dict | None = None) -> tuple[str, list[str]]:
    """picked comes group by group (established, startups, top tier); each group gets a heading, and a
    message ends between groups when the next group doesn't fit, never inside one. "Where you stand" closes it."""
    e = html.escape
    lead = f"{len(picked)} pick{'s' * (len(picked) != 1)} from {considered} new posting{'s' * (considered != 1)} this week"
    counts = _tier_counts(picked)
    wait = f"{unread} more new postings wait for the model to read them." if unread else ""
    md = [f"# {title}", "", lead, counts] + ([wait] if wait else []) + [""]
    units = [f"💼 <b>{e(title)}</b>\n{e(lead)}\n{e(counts)}" + (f"\n<i>{e(wait)}</i>" if wait else "")]
    i = 0
    for tier, in_tier in groupby(picked, key=lambda g: g["tier"]):
        md += [f"## {TIER_LABELS[tier]}", ""]
        blocks = []
        for g in in_tier:
            i += 1
            posted = _day(datetime.fromisoformat(g["posted"]))
            facts, pay, skill_lines = " · ".join(_facts(g)), _pay(g.get("salary")), _skill_lines(g, posted)
            md += [f"### {i}. [{g['title']}]({g['url']})", "", facts] + ([f"💰 {pay}"] if pay else [])
            md += [f"_{g['summary']}_"] if g.get("summary") else []
            md += skill_lines + [""]
            block = [f'<b>{i}. <a href="{e(g["url"], quote=True)}">{e(g["title"])}</a></b>', e(facts)]
            block += [f"💰 {e(pay)}"] if pay else []
            block += [f"<i>{e(g['summary'])}</i>"] if g.get("summary") else []
            block += [e(line) for line in skill_lines]
            blocks.append("\n".join(block))
        head = f"{DIVIDER}\n<b>{e(TIER_LABELS[tier])}</b>"
        whole = "\n\n".join([head] + blocks)
        if visible_len(whole) <= PACK_LIMIT:
            units.append(whole)
        else:   # a group too long for one message: its heading travels with its first opening
            units += [f"{head}\n\n{blocks[0]}"] + blocks[1:]
    if standing:
        head = f"📊 Where you stand · {standing['roles']} roles for you this week"
        lines = _standing_lines(standing)
        md += [f"## {head}", ""] + lines + [""]
        units.append(f"{DIVIDER}\n<b>{e(head)}</b>\n" + "\n".join(e(line) for line in lines))
    md += ["---", footer]
    units.append(f"{DIVIDER}\n<i>{e(footer)}</i>")
    return "\n".join(md), _pack(units)


def build_openings(conn: sqlite3.Connection, settings, *, per_tier: dict[str, int] | None = None,
                   redo: bool = False, now_dt: datetime | None = None) -> dict:
    """Ranks this week's unsent postings and stores the message (kind "jobs_openings"); nothing is sent here.
    per_tier overrides some groups' places for this run (e.g. {"top": 0}); redo makes the postings of the
    latest sent openings candidates again, to rebuild them (say, after a layout change)."""
    now_dt = now_dt or datetime.now(timezone.utc)
    run_id = start_run(conn, "openings")
    try:
        redone = None
        if redo:
            last = conn.execute("SELECT id FROM reports WHERE kind = 'jobs_openings' AND telegram_message_ids IS NOT NULL"
                                " ORDER BY id DESC LIMIT 1").fetchone()
            if last:
                redone = last["id"]
                conn.execute("DELETE FROM job_alerts WHERE report_id = ?", (redone,))
                conn.commit()
        skills = Skills(settings.skills_path)
        prefs = load_prefs(settings.openings_path)
        prefs["per_tier"] = {**prefs["per_tier"], **(per_tier or {})}
        profile = load_fit_profile(settings.fit_path, skills)
        employers = {c.id: c for c in load_companies(settings.companies_path)}
        found = pick_openings(conn, skills, profile, prefs, employers, now_dt=now_dt)
        result = {"report_id": None, **found, "markdown": "", "telegram": [], "has_profile": profile is not None,
                  "redone": redone}
        if found["picked"]:
            local = now_dt.astimezone()
            title = title_for(local)
            footer = FOOTER + ("" if profile else NO_PROFILE)
            result["markdown"], result["telegram"] = render(found["picked"], found["considered"], found["unread"],
                                                            title, footer, found["standing"])
            stats = {"posting_ids": [i for g in found["picked"] for i in g["ids"]],
                     "fits": {str(i): g["fit"] for g in found["picked"] for i in g["ids"]},
                     "tiers": {t: sum(g["tier"] == t for g in found["picked"]) for t in prefs["per_tier"]},
                     "considered": found["considered"], "unread": found["unread"],
                     "standing": found["standing"]}   # kept, so you can see how your gap changes over time
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
