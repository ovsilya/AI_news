import json
from argparse import Namespace
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from ainews import cli, db
from ainews.config import ROOT
from ainews.deliver.render import TELEGRAM_LIMIT, visible_len
from ainews.jobs.boards import TIERS, Company, load_companies
from ainews.jobs.extract import Skills
from ainews.jobs.openings import (DEFAULTS, _city, _pay, _share_out, _where, build_openings, fit_score, load_prefs,
                                  mark_sent, pick_openings)

SKILLS = Skills(ROOT / "config" / "skills.yaml")
NOW = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)
PROFILE = {"have": ["Python", "RAG"], "learning": ["MCP"]}
PREFS = {**DEFAULTS, "years": 7}


def _seed(conn, key, *, company="acme", title="Forward Deployed Engineer", days=1, markets=("US",),
          locations=("New York, NY",), family="fde_applied_ai", seniority="senior", required=("Python", "RAG"),
          preferred=(), years=3, closed=False, read=True):
    posted = (NOW - timedelta(days=days)).isoformat()
    conn.execute(
        "INSERT INTO job_postings (key, company, title, url, locations, markets, title_family, text_hash, posted_at,"
        " first_seen, last_seen, closed_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'h', ?, ?, ?, ?)",
        (key, company, title, f"https://jobs.example/{key}", json.dumps(list(locations)), json.dumps(list(markets)),
         family, posted, posted, posted, posted if closed else None))
    pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    if read:
        data = {"role_family": family, "seniority": seniority, "years_min": years, "skills_required": list(required),
                "skills_preferred": list(preferred), "remote_policy": "hybrid", "visa_sponsorship": "unknown",
                "summary": f"Summary of {key}.", "salary": None}
        conn.execute("INSERT INTO job_extractions VALUES (?, 'v', 'h', ?, 'now')", (pid, json.dumps(data)))
    conn.commit()
    return pid


def test_fit_score_weighs_skills_and_level():
    full = {"skills_required": ["Python", "RAG"], "seniority": "senior"}
    assert fit_score(full, PROFILE, PREFS) == 100
    # Required: Python (have) + Kubernetes (no) = 1 of 2; preferred MCP (learning) = 0.5 at half weight.
    mixed = {"skills_required": ["Python", "Kubernetes"], "skills_preferred": ["MCP"], "seniority": "mid"}
    assert fit_score(mixed, PROFILE, PREFS) == round(70 * (1 + 0.25) / 2.5 + 30 * 0.4)
    assert fit_score({"seniority": "unknown"}, PROFILE, PREFS) == round(70 * 0.5 + 30 * 0.6)   # no skills listed
    assert fit_score(full, None, PREFS) == round(70 * 0.5 + 30)                                  # no skills file


def test_pick_filters_groups_and_caps_per_employer(conn):
    best = _seed(conn, "acme:1")
    london = _seed(conn, "acme:2", title="Forward  Deployed Engineer ", markets=("UK",), locations=("London, UK",))
    _seed(conn, "acme:old", days=10)                                       # older than a week
    _seed(conn, "acme:junior", title="Junior FDE", seniority="junior")      # a level you skip
    _seed(conn, "acme:dubai", title="FDE Dubai", markets=("AE",))           # outside these prefs' markets
    _seed(conn, "acme:ds", title="Data Scientist", family="data_science")   # not a family you want
    _seed(conn, "acme:years", title="Principal FDE", years=15)              # asks for 15 years; you have 7
    _seed(conn, "acme:closed", title="Closed FDE", closed=True)
    for i in range(3):
        _seed(conn, f"big:{i}", company="big", title=f"AI Engineer {i}", family="ai_eng", required=("Python",))
    _seed(conn, "acme:weak", title="ML Engineer", family="ml_eng", seniority="mid", required=("Go", "Rust"))
    _seed(conn, "acme:unread", title="Applied AI Engineer", read=False)
    prefs = {**PREFS, "markets": ["US", "UK", "CA", "CH", "remote"]}

    _seed(conn, "gone:1", company="gone", title="FDE at an employer taken off the list")
    employers = {"acme": Company("acme", "Acme AI", "ashby", "acme", tier="startup"),
                 "big": Company("big", "BigCo", "workday", "big.wd1/Jobs", tier="established")}

    found = pick_openings(conn, SKILLS, PROFILE, prefs, employers, now_dt=NOW)
    picked = found["picked"]
    assert [(g["company"], g["tier"]) for g in picked] == [("BigCo", "established")] * 2 + [("Acme AI", "startup")]
    fde = picked[2]                                                        # three BigCo postings, two places
    assert fde["ids"] == [best, london] and fde["title"] == "Forward Deployed Engineer"
    assert fde["places"] == ["New York, NY", "London, UK"]
    assert all(g["fit"] >= prefs["min_fit"] for g in picked)
    assert "ML Engineer" not in [g["title"] for g in picked]                # fit 12, below the bar
    assert found["considered"] == 6 and found["unread"] == 1
    assert fde["have"] == ["Python", "RAG"] and fde["missing"] == []


def _g(company, tier, fit):
    return {"company": company, "tier": tier, "fit": fit}


def test_places_are_shared_between_groups_and_top_tier_never_takes_more():
    groups = sorted([_g(f"lab{i}", "top", 99 - i) for i in range(6)] + [_g(f"bank{i}", "established", 80 - i) for i in range(6)]
                    + [_g("tiny", "startup", 60), _g("weak", "startup", 40)], key=lambda g: -g["fit"])
    tiers = lambda picked: [(g["tier"], g["company"]) for g in picked]   # noqa: E731
    # Top tier has the best fits but keeps its 2 places; startups fill 1 of 3 and established takes the rest.
    assert tiers(_share_out(groups, PREFS)) == ([("established", f"bank{i}") for i in range(5)] + [("startup", "tiny")]
                                                + [("top", "lab0"), ("top", "lab1")])
    no_top = {**PREFS, "per_tier": {"established": 3, "startup": 3, "top": 0}}
    assert "top" not in [g["tier"] for g in _share_out(groups, no_top)]
    no_banks = {**PREFS, "per_tier": {"established": 0, "startup": 3, "top": 2}}   # 0 means none, even spare places
    assert tiers(_share_out(groups, no_banks)) == [("startup", "tiny"), ("top", "lab0"), ("top", "lab1")]


def test_every_employer_has_a_tier(tmp_path):
    companies = load_companies(ROOT / "config" / "companies.yaml", include_disabled=True)
    assert {c.tier for c in companies} == set(TIERS)
    path = tmp_path / "companies.yaml"
    path.write_text("companies:\n  - {id: acme, name: Acme, ats: ashby, board: acme}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="acme: tier"):
        load_companies(path)


def test_build_sends_once_and_fits_telegram(conn, settings, tmp_path):
    for i in range(12):
        _seed(conn, f"co{i}:1", company=f"co{i}", title=f"Applied AI Engineer {i}", required=("Python", "Kubernetes"),
              preferred=("MCP",), locations=("San Francisco, CA | New York City, NY", "San Francisco, California"))
    fit = tmp_path / "skills.yaml"
    fit.write_text("have: [Python]\nlearning: [MCP]\n", encoding="utf-8")
    companies = tmp_path / "companies.yaml"   # four employers in each group
    companies.write_text("companies:\n" + "".join(
        f"  - {{id: co{i}, name: Co {i}, ats: ashby, board: co{i}, tier: {TIERS[i // 4]}}}\n" for i in range(12)))
    s = replace(settings, fit_path=fit, openings_path=tmp_path / "none.yaml", companies_path=companies)
    result = build_openings(conn, s, now_dt=NOW)
    assert len(result["picked"]) == 8 and result["considered"] == 12
    assert [g["tier"] for g in result["picked"]] == ["established"] * 3 + ["startup"] * 3 + ["top"] * 2
    assert all(visible_len(m) <= TELEGRAM_LIMIT for m in result["telegram"])
    first, text = result["telegram"][0], "\n".join(result["telegram"])
    assert len(result["telegram"]) == 1                                       # all eight fit in one message
    assert "Jobs for you" in first and "🏢 3 established · 🌱 3 startups · 🏆 2 top tier" in first
    assert "🎯 Fit 65 · 1 of 2 required skills · posted" in first
    assert "➕ To learn: Kubernetes (learning: MCP)" in first
    assert "Co 4 · San Francisco / New York City 🇺🇸 · hybrid · senior" in first
    assert text.index("<b>🏢 Established</b>") < text.index("<b>🌱 Startups</b>") < text.index("<b>🏆 Top tier</b>")
    assert '<b>7. <a href="https://jobs.example/co0:1">Applied AI Engineer 0</a></b>' in text   # numbered across groups
    row = conn.execute("SELECT kind, stats, telegram_message_ids FROM reports WHERE id = ?",
                       (result["report_id"],)).fetchone()
    assert row["kind"] == "jobs_openings" and row["telegram_message_ids"] is None
    assert json.loads(row["stats"])["tiers"] == {"established": 3, "startup": 3, "top": 2}

    assert mark_sent(conn, result["report_id"], [101, 102]) == 8
    again = build_openings(conn, s, now_dt=NOW)                              # the 8 sent ones never come back
    assert len(again["picked"]) == 4 and not {i for g in again["picked"] for i in g["ids"]} & {
        i for g in result["picked"] for i in g["ids"]}
    assert len(build_openings(conn, s, per_tier={"top": 0}, now_dt=NOW)["picked"]) == 2
    conn.execute("UPDATE reports SET telegram_message_ids = '[101, 102]' WHERE id = ?", (result["report_id"],))
    redo = build_openings(conn, s, redo=True, now_dt=NOW)                    # the sent eight are candidates again
    assert redo["redone"] == result["report_id"] and len(redo["picked"]) == 8


def test_prefs_reject_unknown_names(tmp_path):
    path = tmp_path / "openings.yaml"
    path.write_text("markets: [US, Germany]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Germany"):
        load_prefs(path)
    path.write_text("per_tier: {faang: 2}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="faang"):
        load_prefs(path)
    path.write_text("per_tier: {top: 0}\n", encoding="utf-8")                # the other groups keep their defaults
    assert load_prefs(path)["per_tier"] == {"established": 3, "startup": 3, "top": 0}
    assert load_prefs(tmp_path / "missing.yaml") == DEFAULTS


def test_places_and_pay():
    assert [_city(p)[0] for p in ("USA, GA, Atlanta", "California - San Francisco", "United States of America, Eagan, "
                                  "Minnesota", "New York - New York", "San Jose Office (First St)", "UK, Cambridge",
                                  "Austin, TX", "Washington, District of Columbia, United States")] == [
        "Atlanta", "San Francisco", "Eagan", "New York", "San Jose", "Cambridge", "Austin", "Washington DC"]
    assert _city("US, CA, Remote") == ("", True) and _city("Remote - Illinois") == ("", True)
    assert _where({"places": ["Washington, DC", "Washington, District of Columbia, United States"],
                   "markets": ["US"]}) == "Washington DC 🇺🇸"
    assert _where({"places": ["London", "Toronto, ON", "Zurich"], "markets": ["UK", "CA", "CH"]}) == (
        "London / Toronto +1 more 🇬🇧🇨🇦🇨🇭")
    assert _where({"places": ["Remote"], "markets": ["remote"]}) == "Remote"
    assert _pay({"min": 175100, "max": 245000, "currency": "USD", "period": "year"}) == "$175k–$245k a year"
    assert _pay({"min": 60, "max": 80, "currency": "GBP", "period": "hour"}) == "£60–£80 an hour"
    assert _pay(None) == ""


class FakeTelegram:
    sent: list = []

    def __init__(self, token, chat_id):
        pass

    def send_messages(self, messages):
        FakeTelegram.sent.append(messages)
        return list(range(len(messages)))


def test_send_picks_the_right_kind_of_report(settings, monkeypatch):
    conn = db.connect(settings.db_path)
    for kind, text in (("jobs_weekly", "weekly"), ("jobs_openings", "openings")):
        conn.execute("INSERT INTO reports (created_at, kind, stats, markdown, telegram_html) VALUES ('t', ?, ?, '', ?)",
                     (kind, json.dumps({"posting_ids": [], "fits": {}}), json.dumps([text])))
    conn.commit()
    monkeypatch.setattr("ainews.deliver.telegram.Telegram", FakeTelegram)
    FakeTelegram.sent = []
    cli.cmd_send(Namespace(report=True, openings=False, force=False), settings)
    cli.cmd_send(Namespace(report=False, openings=True, force=False), settings)
    assert FakeTelegram.sent == [["weekly"], ["openings"]]
    with pytest.raises(SystemExit, match="already sent"):
        cli.cmd_send(Namespace(report=False, openings=True, force=False), settings)


class FakeDeleter:
    asked: list = []

    def __init__(self, token, chat_id):
        pass

    def delete_messages(self, ids):
        FakeDeleter.asked = ids
        return {"deleted": ids, "missing": [], "refused": []}


def test_tg_clean_also_removes_replaced_posts(settings, monkeypatch, capsys):
    conn = db.connect(settings.db_path)
    conn.execute("INSERT INTO briefs (created_at, edition, story_ids, markdown, telegram_html, telegram_message_ids)"
                 " VALUES ('t', 'am', '[]', '', '[]', '[31, 32]')")
    for ids in ("[39, 40]", "[41]"):   # openings, then the same openings sent again with --redo
        conn.execute("INSERT INTO reports (created_at, kind, stats, markdown, telegram_html, telegram_message_ids)"
                     " VALUES ('t', 'jobs_openings', '{}', '', '[]', ?)", (ids,))
    conn.commit()
    monkeypatch.setattr("ainews.deliver.telegram.Telegram", FakeDeleter)
    monkeypatch.setattr("builtins.input", lambda prompt: print(prompt) or "y")
    cli.cmd_tg_clean(Namespace(yes=False), settings)
    assert FakeDeleter.asked == list(range(1, 31)) + [39, 40]                 # the brief and the newer openings stay
    assert "1-30 and 39, 40 (replaced by newer posts)" in capsys.readouterr().out
