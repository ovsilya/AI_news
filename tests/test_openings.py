import json
from argparse import Namespace
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from ainews import cli, db
from ainews.config import ROOT
from ainews.deliver.render import TELEGRAM_LIMIT, visible_len
from ainews.jobs.extract import Skills
from ainews.jobs.openings import DEFAULTS, _pay, _places, build_openings, fit_score, load_prefs, mark_sent, pick_openings

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

    found = pick_openings(conn, SKILLS, PROFILE, prefs, {"acme": "Acme AI", "big": "BigCo"}, now_dt=NOW)
    picked = found["picked"]
    assert picked[0]["ids"] == [best, london] and picked[0]["title"] == "Forward Deployed Engineer"
    assert picked[0]["places"] == ["New York, NY", "London, UK"] and picked[0]["company"] == "Acme AI"
    assert [g["company"] for g in picked].count("BigCo") == 2               # three postings, two places
    assert all(g["fit"] >= prefs["min_fit"] for g in picked)
    assert "ML Engineer" not in [g["title"] for g in picked]                # fit 12, below the bar
    assert found["considered"] == 6 and found["unread"] == 1
    assert picked[0]["have"] == ["Python", "RAG"] and picked[0]["missing"] == []


def test_build_sends_once_and_fits_telegram(conn, settings, tmp_path):
    for i in range(12):
        _seed(conn, f"co{i}:1", company=f"co{i}", title=f"Applied AI Engineer {i}", required=("Python", "Kubernetes"),
              preferred=("MCP",), locations=("San Francisco, CA | New York City, NY", "San Francisco, California"))
    fit = tmp_path / "skills.yaml"
    fit.write_text("have: [Python]\nlearning: [MCP]\n", encoding="utf-8")
    s = replace(settings, fit_path=fit, openings_path=tmp_path / "none.yaml")
    result = build_openings(conn, s, now_dt=NOW)
    assert len(result["picked"]) == 8 and result["considered"] == 12
    assert all(visible_len(m) <= TELEGRAM_LIMIT for m in result["telegram"])
    first = result["telegram"][0]
    assert "Jobs for you" in first and "✅ Yours: Python (1 of 2 required)" in first and "➕ New to you: Kubernetes" in first
    assert "📍 San Francisco, CA / New York City, NY · hybrid · senior" in first
    row = conn.execute("SELECT kind, telegram_message_ids FROM reports WHERE id = ?", (result["report_id"],)).fetchone()
    assert row["kind"] == "jobs_openings" and row["telegram_message_ids"] is None

    assert mark_sent(conn, result["report_id"], [101, 102]) == 8
    again = build_openings(conn, s, now_dt=NOW)                              # the 8 sent ones never come back
    assert len(again["picked"]) == 4 and not {i for g in again["picked"] for i in g["ids"]} & {
        i for g in result["picked"] for i in g["ids"]}


def test_prefs_reject_unknown_names(tmp_path):
    path = tmp_path / "openings.yaml"
    path.write_text("markets: [US, Germany]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Germany"):
        load_prefs(path)
    assert load_prefs(tmp_path / "missing.yaml") == DEFAULTS


def test_places_and_pay():
    assert _places(["Washington, DC", "Washington, District of Columbia, United States"]) == "Washington, DC"
    assert _places(["New Jersey; Remote - Illinois; Remote - Texas"]) == "New Jersey / Remote - Illinois +1 more"
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
