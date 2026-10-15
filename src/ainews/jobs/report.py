"""W2 / W3: the weekly jobs report. Tables come straight from the statistics; the model writes three
short notes (market, skills, your fit), number-checked against the same statistics."""
from __future__ import annotations

import html
import json
import logging
import sqlite3
from datetime import datetime, timezone

from ..db import finish_run, now, start_run
from ..deliver.render import DIVIDER, _note, _note_html, _pack, _split_label
from ..llm import BudgetExceeded
from ..pipeline.editor import NOTE
from ..pipeline.score import load_prompt
from ..pipeline.verify import _check_field, _unsupported
from .boards import load_companies
from .extract import Skills
from .stats import SMALL_GAP, TOP_SKILLS, load_fit_profile, market_stats

log = logging.getLogger(__name__)

SCHEMA = {"type": "object",
          "properties": {"headline": {"type": "string"}, "market": NOTE, "skills": NOTE, "fit": NOTE},
          "required": ["headline", "market", "skills", "fit"], "additionalProperties": False}
NOTES = (("market", "💬", "What it means"), ("skills", "💬", "What it means"), ("fit", "🎓", "Comment from a pro"))


def write_notes(llm, stats: dict, *, model: str, prompts_dir, profile: str) -> tuple[dict, list[str]]:
    system, _ = load_prompt(prompts_dir, "weekly", profile)
    src = json.dumps(stats, ensure_ascii=False)
    draft = llm.json(model=model, system=system, user="This week's statistics:\n" + src, schema=SCHEMA,
                     max_tokens=12000, effort="medium", purpose="weekly")
    warnings: list[str] = []
    notes = {k: _check_field(draft.get(k), src, k, warnings) for k, _, _ in NOTES}
    headline = draft.get("headline", "")
    if _unsupported(headline, src):
        warnings.append(f"headline {headline!r} has unsupported numbers; dropped")
        headline = ""
    return {"headline": headline, **notes}, warnings


def _sections(stats: dict) -> list[tuple[str, list[str], str | None]]:
    """[(heading, plain-text lines, note key)] shared by both renderings."""
    target = [f for f in stats["by_family"] if f["open"] and f["target"]]
    others = [f for f in stats["by_family"] if f["open"] and not f["target"]]
    roles = [f"• {f['name']}: {f['open']} open, {f['new_7d']} new this week" for f in target]
    if others:
        roles.append("Also open: " + " · ".join(f"{f['name']} {f['open']}" for f in others))
    markets = " · ".join(f"{m['market']} {m['open']}" for m in stats["by_market"])
    levels = " · ".join(f"{k} {v}%" for k, v in stats["seniority_pct"].items() if v and k != "unknown")
    roles += [f"Markets: {markets}" if markets else "Markets: none yet", f"Seniority: {levels}",
              f"Remote: {stats['remote_pct']}% · customer-facing: {stats['customer_facing_pct']}% · "
              f"visa sponsorship offered in {stats['visa_yes']} postings"]

    skills = [f"{i}. {s['name']} {s['pct']}%" for i, s in enumerate(stats["top_skills"][:15], 1)]
    for role, names in stats["top_skills_by_role"].items():
        skills.append(f"• {role}: {', '.join(names[:5])}")
    mv = stats["movement"]
    if mv["ready"]:
        for key, arrow in (("rising", "↑"), ("falling", "↓")):
            if mv[key]:
                skills.append(f"{arrow} {key.title()}: " + ", ".join(
                    f"{e['name']} {e['pct_before']}% → {e['pct_now']}%" for e in mv[key][:5]))
        if not mv["rising"] and not mv["falling"]:
            skills.append("No skill moved clearly this week.")
    else:
        start = datetime.fromisoformat(mv["trends_from"])
        skills.append(f"Rising and falling skills start once we have two weeks of daily tracking "
                      f"(from {start:%a} {start.day} {start:%b}).")

    pay = [f"• {s['role']}: {s['range']} ({s['postings']} postings)"
           for s in stats["salary"][:8]] or ["Not enough postings with pay yet."]
    hiring = [" · ".join(f"{e['name']} {e['open']}" + (f" (FDE/applied {e['fde_applied_ai']})" if e["fde_applied_ai"] else "")
                         for e in stats["top_employers"])]

    fit = stats.get("fit")
    if fit is None:
        you = ["Add private/skills.yaml (have / learning lists) to see how your skills match the demand."]
    else:
        you = []
        market = fit.get("market")
        if market:   # first: every open role, not just the best fits the openings show
            tech = market["technical_median_pct"]
            you.append(f"A typical open role: you cover {market['median_pct']}% of its skills"
                       + (f", {tech}% of its technical ones" if tech is not None else "")
                       + f"; {market['small_gap_pct']}% of the {market['roles']} open role{'s' * (market['roles'] != 1)}"
                       f" have a small gap ({SMALL_GAP}%+).")
            if market["by_family"]:
                you.append("By role: " + " · ".join(f"{f['name']} {f['median_pct']}%" for f in market["by_family"]))
        higher = market and fit["coverage_pct"] > market["median_pct"]
        you += [f"Of the {TOP_SKILLS} most-asked skills, weighted by demand, you have {fit['coverage_pct']}%"
                + (" (higher, as most of your gaps are in less common skills)." if higher else "."),
                "In demand and yours: " + (", ".join(fit["have_in_top"][:10]) or "none yet"),
                "Biggest gaps: " + ", ".join(
                    f"{g['name']} {g['pct']}%" + (" (learning)" if g["learning"] else "") + (" ↑" if g["rising"] else "")
                    for g in fit["gaps"][:8])]
    return [("🧭 Roles and markets", roles, "market"),
            ("🧰 Skills employers ask for (share of open postings)", skills, "skills"),
            ("💰 Pay: typical posted base range per year (medians)", pay, None),
            ("🏢 Who's hiring most", hiring, None),
            ("🎯 Your fit", you, "fit")]


def render(stats: dict, notes: dict, title: str, footer: str) -> tuple[str, list[str]]:
    e = html.escape
    n = stats["employers"]
    lead = (f"{stats['target_open']} open roles in your target families at {n} employer{'s' * (n != 1)} · "
            f"{stats['new_7d']} new this week · {stats['closed_7d']} closed")
    headline = notes.get("headline", "")
    md = [f"# {title}", ""] + ([f"_{headline}_", ""] if headline else []) + [lead, ""]
    units = [f"📊 <b>{e(title)}</b>\n" + (f"<i>{e(headline)}</i>\n" if headline else "") + e(lead)]
    labels = {k: (emoji, name) for k, emoji, name in NOTES}
    for heading, lines, key in _sections(stats):
        md += [f"## {heading}", "", *lines, ""]
        unit = f"{DIVIDER}\n<b>{e(heading)}</b>\n" + "\n".join(e(x) for x in lines)
        if key:
            emoji, name = labels[key]
            intro, points = _note(notes.get(key))
            if intro or points:
                md.append(f"> {emoji} **{name}** {intro}".rstrip())
                for p in points:
                    label, text = _split_label(p)
                    md.append(f"> - **{label}:** {text}" if label else f"> - {text}")
                md.append("")
                unit += "\n\n" + _note_html(emoji, name, notes.get(key))
        units.append(unit)
    md += ["---", footer]
    units.append(f"{DIVIDER}\n<i>{e(footer)}</i>")
    return "\n".join(md), _pack(units)


def build_weekly(conn: sqlite3.Connection, settings, *, llm=None, now_dt: datetime | None = None) -> dict:
    now_dt = now_dt or datetime.now(timezone.utc)
    run_id = start_run(conn, "weekly")
    stats_run: dict = {"llm": llm is not None}
    try:
        companies = load_companies(settings.companies_path)
        skills = Skills(settings.skills_path)
        stats = market_stats(conn, skills, {c.id: c.name for c in companies}, now_dt=now_dt,
                             fit_profile=load_fit_profile(settings.fit_path, skills))
        notes, warnings = {"headline": ""}, []
        if llm is not None and stats["target_open"]:
            try:
                notes, warnings = write_notes(llm, stats, model=settings.model_editor,
                                              prompts_dir=settings.prompts_dir, profile=settings.profile_text())
            except BudgetExceeded as exc:
                log.warning("weekly notes skipped: %s", exc)
                stats_run["budget_stop"] = str(exc)
        cost = llm.total_cost if llm is not None else 0.0
        title = f"AI jobs report · week to {now_dt.astimezone():%a} {now_dt.astimezone().day} {now_dt.astimezone():%b}"
        n_wd = sum(c.ats == "workday" for c in companies)
        footer = (f"From open postings on {len(companies)} employers' own job boards: {len(companies) - n_wd} mostly AI "
                  f"companies (Greenhouse, Lever, Ashby)" + (f" and {n_wd} banks, pharma and big tech (Workday, "
                  f"searched for AI roles)" if n_wd else "") + f" · run cost ${cost:.3f}")
        markdown, telegram = render(stats, notes, title, footer)
        report_id = conn.execute(
            "INSERT INTO reports (created_at, kind, stats, markdown, telegram_html, cost_usd) VALUES (?, ?, ?, ?, ?, ?)",
            (now(), "jobs_weekly", json.dumps(stats), markdown, json.dumps(telegram), cost)).lastrowid
        conn.commit()
        stats_run.update({"warnings": len(warnings), "cost_usd": round(cost, 4), "target_open": stats["target_open"]})
        finish_run(conn, run_id, "ok", stats_run)
        return {"report_id": report_id, "markdown": markdown, "telegram": telegram, "warnings": warnings,
                "stats": stats}
    except Exception as exc:
        finish_run(conn, run_id, f"error: {exc}"[:300], stats_run)
        raise
