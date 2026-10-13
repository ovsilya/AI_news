"""J3 / W1: statistics for the weekly jobs report, computed from stored postings and extractions.

- Demand now: skill shares across all open postings in the target role families.
- Movement: skills whose share in target postings published this week differs from the four weeks
  before, counted only when the two Wilson 95% intervals don't overlap. It waits for two weeks of
  daily tracking: until then the earlier period holds only postings that were still open when we
  started, which skews the comparison.
- Fit: how much of the demand-weighted top skills your skills cover (private/skills.yaml)."""
from __future__ import annotations

import json
import math
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median

import yaml

from .extract import Skills
from .filters import FAMILIES, MARKETS

TARGET = ("fde_applied_ai", "solutions_eng", "llm_genai_eng", "ai_eng", "ml_eng")   # your target roles
TOP_SKILLS = 30
MIN_COHORT = 30       # postings in a week before skill movement is reported
MIN_TRACKING = timedelta(days=14)
MIN_SKILL = 5
MIN_SALARY = 5
CURRENCY = {"USD": "$", "CAD": "C$", "GBP": "£", "CHF": "CHF ", "AED": "AED ", "EUR": "€"}
PER_YEAR = {"year": 1, "month": 12, "hour": 2080}


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 0.0
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return centre - half, centre + half


def _when(row) -> datetime:
    for v in (row["posted_at"], row["first_seen"]):
        if v:
            try:
                dt = datetime.fromisoformat(v)
                return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            except ValueError:
                pass
    return datetime.min.replace(tzinfo=timezone.utc)


def _skills_of(data: dict) -> set[str]:
    return set(data.get("skills_required") or []) | set(data.get("skills_preferred") or [])


def _renormalize(data: dict, skills: Skills) -> dict:
    """Apply today's skill dictionary, so alias edits also fix postings read earlier."""
    required = skills.normalize(data.get("skills_required") or [])
    return {**data, "skills_required": required,
            "skills_preferred": skills.normalize(data.get("skills_preferred") or [], exclude=set(required))}


def _pct(k: int, n: int) -> int:
    return round(100 * k / n) if n else 0


def load_fit_profile(path: Path, skills: Skills) -> dict | None:
    """private/skills.yaml: {"have": [...], "learning": [...]}, names mapped to canonical skills."""
    if not path.exists():
        return None
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {k: skills.normalize(raw.get(k) or []) for k in ("have", "learning")}


def market_stats(conn: sqlite3.Connection, skills: Skills, names: dict[str, str], *, now_dt: datetime | None = None,
                 fit_profile: dict | None = None) -> dict:
    now_dt = now_dt or datetime.now(timezone.utc)
    week, month = now_dt - timedelta(days=7), now_dt - timedelta(days=35)
    rows = conn.execute(
        "SELECT p.company, p.markets, p.posted_at, p.first_seen, p.closed_at, e.data FROM job_postings p"
        " JOIN job_extractions e ON e.posting_id = p.id").fetchall()
    rows = [(r, _renormalize(json.loads(r["data"]), skills)) for r in rows]
    rows = [(r, d) for r, d in rows if d.get("role_family") in FAMILIES and d["role_family"] != "other"]
    open_ = [(r, d) for r, d in rows if r["closed_at"] is None]
    target = [(r, d) for r, d in open_ if d["role_family"] in TARGET]
    new_week = [(r, d) for r, d in rows if _when(r) >= week]
    tracked = [(r, d) for r, d in rows if d["role_family"] in TARGET]
    new_target = [(r, d) for r, d in tracked if _when(r) >= week]
    before = [(r, d) for r, d in tracked if month <= _when(r) < week]
    first = conn.execute("SELECT MIN(first_seen) FROM job_postings").fetchone()[0]
    tracking_since = datetime.fromisoformat(first) if first else now_dt
    closed_week = sum(1 for r, _ in rows if r["closed_at"] and r["closed_at"] >= week.isoformat())

    by_family = {f: {"name": FAMILIES[f], "open": 0, "new_7d": 0, "target": f in TARGET}
                 for f in FAMILIES if f != "other"}
    for r, d in open_:
        by_family[d["role_family"]]["open"] += 1
    for r, d in new_week:
        by_family[d["role_family"]]["new_7d"] += 1
    by_market = Counter(m for r, _ in open_ for m in json.loads(r["markets"]))

    def shares(group) -> list[dict]:
        n = len(group)
        counts = Counter(s for _, d in group for s in _skills_of(d))
        required = Counter(s for _, d in group for s in d.get("skills_required") or [])
        return [{"name": s, "category": skills.category.get(s, "other"), "pct": _pct(k, n), "postings": k,
                 "required_pct": _pct(required[s], n)}
                for s, k in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_SKILLS]]

    top = shares(target)
    by_role = {FAMILIES[f]: [s["name"] for s in shares([x for x in target if x[1]["role_family"] == f])[:8]]
               for f in TARGET if any(x[1]["role_family"] == f for x in target)}

    movement = {"ready": (len(new_target) >= MIN_COHORT and len(before) >= MIN_COHORT
                          and now_dt - tracking_since >= MIN_TRACKING),
                "tracking_since": tracking_since.date().isoformat(),
                "trends_from": (tracking_since + MIN_TRACKING).date().isoformat(),
                "new_postings": len(new_target), "earlier_postings": len(before), "rising": [], "falling": []}
    if movement["ready"]:
        now_c = Counter(s for _, d in new_target for s in _skills_of(d))
        was_c = Counter(s for _, d in before for s in _skills_of(d))
        for s in set(now_c) | set(was_c):
            lo_now, hi_now = wilson(now_c[s], len(new_target))
            lo_was, hi_was = wilson(was_c[s], len(before))
            entry = {"name": s, "pct_now": _pct(now_c[s], len(new_target)), "pct_before": _pct(was_c[s], len(before))}
            if now_c[s] >= MIN_SKILL and lo_now > hi_was:
                movement["rising"].append(entry)
            elif was_c[s] >= MIN_SKILL and hi_now < lo_was:
                movement["falling"].append(entry)
        for key in ("rising", "falling"):
            movement[key].sort(key=lambda e: abs(e["pct_now"] - e["pct_before"]), reverse=True)
            movement[key] = movement[key][:8]

    pay = defaultdict(list)
    for r, d in open_:
        sal = d.get("salary") or {}
        per = PER_YEAR.get(sal.get("period") or "year")
        if sal.get("currency") in CURRENCY and per and sal.get("min") and sal.get("max"):
            pay[(d["role_family"], sal["currency"])].append((sal["min"] * per, sal["max"] * per))
    salary = []
    for (f, cur), v in sorted(pay.items(), key=lambda kv: -len(kv[1])):
        if len(v) >= MIN_SALARY:
            low, high = round(median(lo for lo, _ in v) / 1000), round(median(hi for _, hi in v) / 1000)
            salary.append({"role": FAMILIES[f], "currency": cur, "low": low, "high": high, "postings": len(v),
                           "range": f"{CURRENCY[cur]}{low}k–{CURRENCY[cur]}{high}k"})

    employers = Counter(r["company"] for r, _ in target)
    fde = Counter(r["company"] for r, d in target if d["role_family"] == "fde_applied_ai")

    stats = {
        "week_ending": now_dt.date().isoformat(),
        "open_postings": len(open_), "target_open": len(target), "new_7d": len(new_week), "closed_7d": closed_week,
        "employers": len({r["company"] for r, _ in open_}),
        "by_family": list(by_family.values()),
        "by_market": [{"market": MARKETS[m], "open": by_market[m]} for m in MARKETS if by_market[m]],
        "remote_pct": _pct(sum(1 for _, d in target if d.get("remote_policy") == "remote"), len(target)),
        "customer_facing_pct": _pct(sum(1 for _, d in target if d.get("customer_facing")), len(target)),
        "visa_yes": sum(1 for _, d in target if d.get("visa_sponsorship") == "yes"),
        "seniority_pct": {k: _pct(v, len(target)) for k, v in
                          Counter(d.get("seniority") or "unknown" for _, d in target).most_common()},
        "top_skills": top,
        "top_skills_by_role": by_role,
        "movement": movement,
        "salary": salary,
        "top_employers": [{"name": names.get(c, c), "open": n, "fde_applied_ai": fde[c]}
                          for c, n in employers.most_common(10)],
    }
    if fit_profile is not None:
        stats["fit"] = fit(top, fit_profile, movement)
    return stats


def fit(top: list[dict], profile: dict, movement: dict) -> dict:
    """Demand-weighted share of the top skills you have, and the biggest gaps."""
    have, learning = set(profile["have"]), set(profile["learning"])
    rising = {e["name"] for e in movement.get("rising", [])}
    total = sum(s["pct"] for s in top) or 1
    covered = sum(s["pct"] for s in top if s["name"] in have)
    gaps = [{"name": s["name"], "pct": s["pct"], "learning": s["name"] in learning, "rising": s["name"] in rising}
            for s in top if s["name"] not in have]
    return {"coverage_pct": round(100 * covered / total), "have_in_top": [s["name"] for s in top if s["name"] in have],
            "gaps": gaps[:10]}
