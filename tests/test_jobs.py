import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from ainews.config import ROOT
from ainews.deliver.render import TELEGRAM_LIMIT, visible_len
from ainews.jobs.boards import Company, Posting, parse_ashby, parse_greenhouse, parse_lever
from ainews.jobs.extract import Skills, extract_postings
from ainews.jobs.filters import markets, role_family
from ainews.jobs import workday
from ainews.jobs.poll import poll_workday, store_board
from ainews.jobs.report import build_weekly
from ainews.jobs.stats import market_stats, wilson

SKILLS = Skills(ROOT / "config" / "skills.yaml")
NOW = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
CO = Company("acme", "Acme AI", "greenhouse", "acme")


def test_board_parsers():
    gh = parse_greenhouse({"jobs": [{
        "id": 1, "title": "Forward Deployed Engineer", "absolute_url": "https://x/1",
        "location": {"name": "London, UK"}, "offices": [{"location": "London, England, United Kingdom"}],
        "content": "&lt;p&gt;Build &amp;amp; ship RAG.&lt;/p&gt;", "first_published": "2026-10-01T10:00:00-04:00",
        "metadata": [{"name": "Location Type", "value": "Remote"}],
        "pay_input_ranges": [{"min_cents": 15000000, "max_cents": 20000000, "currency_type": "GBP"}]}]})[0]
    assert gh.description == "Build & ship RAG." and gh.remote is True
    assert gh.salary == {"min": 150000, "max": 200000, "currency": "GBP", "period": "year"}

    lv = parse_lever([{"id": "a", "text": "ML Engineer", "hostedUrl": "https://x/a", "createdAt": 1790000000000,
                       "categories": {"location": "Toronto, ON", "allLocations": ["Toronto, ON"]},
                       "workplaceType": "hybrid", "descriptionPlain": "Train models.",
                       "lists": [{"text": "Requirements", "content": "<li>PyTorch</li>"}], "country": "CA"}])[0]
    assert "Requirements: PyTorch" in lv.description and lv.remote is None and lv.posted_at.startswith("2026")

    ab = parse_ashby({"jobs": [
        {"id": "h", "title": "Applied AI Engineer", "jobUrl": "https://x/h", "location": "San Francisco",
         "isRemote": True, "workplaceType": "Hybrid", "descriptionPlain": "Agents.",
         "compensation": {"summaryComponents": [{"compensationType": "Salary", "interval": "1 YEAR",
                                                 "currencyCode": "USD", "minValue": 1, "maxValue": 2}]}},
        {"id": "hidden", "title": "x", "isListed": False}]})
    assert len(ab) == 1 and ab[0].remote is False    # isRemote is also true for hybrid jobs
    assert ab[0].salary["currency"] == "USD"


@pytest.mark.parametrize("title,ai_native,family", [
    ("Forward Deployed Engineer", False, "fde_applied_ai"),
    ("Solutions Architect", True, "solutions_eng"),
    ("Solutions Architect", False, None),                    # at a non-AI company it needs AI in the title
    ("Senior Solutions Architect, GenAI", False, "fde_applied_ai"),
    ("Member of Technical Staff", True, "ai_eng"),
    ("Account Executive, AI Native", True, None),
    ("Software Engineer, Claude Code", True, None),
    ("Machine Learning Engineer", False, "ml_eng"),
])
def test_role_family(title, ai_native, family):
    assert role_family(title, ai_native) == family


@pytest.mark.parametrize("locations,remote,countries,expected", [
    (["New York City, NY; San Francisco, CA"], None, [], ["US"]),
    (["London, On-site"], None, [], ["UK"]),              # "On" is not Ontario
    (["Toronto, ON"], None, [], ["CA"]),
    (["Zürich, Switzerland"], None, [], ["CH"]),
    (["Remote - Germany"], True, [], []),                 # remote, but outside our markets
    (["Remote"], None, [], ["remote"]),
    (["Singapore"], None, ["SG"], []),
    (["San Francisco"], None, ["United States"], ["US"]),
])
def test_markets(locations, remote, countries, expected):
    assert markets(locations, remote, countries) == expected


def _posting(i, title="Forward Deployed Engineer", text="Python and RAG", loc="London, UK"):
    return Posting(job_id=str(i), title=title, url=f"https://x/{i}", locations=[loc], description=text,
                   posted_at="2026-10-10T00:00:00+00:00")


def test_poll_diff_new_changed_closed(conn):
    t0 = NOW.isoformat()
    first = store_board(conn, CO, [_posting(1), _posting(2), _posting(3, title="Office Manager")], at=t0)
    assert first == {"listed": 3, "matching": 2, "new": 2, "changed": 0, "closed": 0}
    # A day later posting 2 is missing: within the grace period it stays open; posting 1 changed its text.
    t1 = (NOW + timedelta(hours=24)).isoformat()
    second = store_board(conn, CO, [_posting(1, text="Python, RAG and MCP")], at=t1)
    assert (second["changed"], second["closed"]) == (1, 0)
    t2 = (NOW + timedelta(hours=40)).isoformat()
    third = store_board(conn, CO, [_posting(1, text="Python, RAG and MCP")], at=t2)
    assert third["closed"] == 1
    closed = conn.execute("SELECT key, closed_at FROM job_postings WHERE closed_at IS NOT NULL").fetchone()
    assert closed["key"] == "acme:2" and closed["closed_at"] == t0     # closed when last seen
    # It comes back: open again.
    store_board(conn, CO, [_posting(1, text="Python, RAG and MCP"), _posting(2)], at=t2)
    assert conn.execute("SELECT COUNT(*) FROM job_postings WHERE closed_at IS NULL").fetchone()[0] == 2


WD = Company("bank", "Big Bank", "workday", "bank.wd3/Careers", ai_native=False)
WD_FACETS = [{"facetParameter": "locationMainGroup", "values": [
    {"facetParameter": "locationHierarchy1", "values": [
        {"descriptor": "United States of America", "id": "us1"}, {"descriptor": "Germany", "id": "de1"},
        {"descriptor": "Canada", "id": "ca1"}]},
    {"facetParameter": "timeType", "values": [{"descriptor": "Full time", "id": "ft"}]}]},
    # A city list with one entry named like a country: not a country filter.
    {"facetParameter": "locations", "values": [{"descriptor": c, "id": c} for c in
                                               ("Basel", "Boston", "Berlin", "Paris", "Madrid", "Toronto",
                                                "Lisbon", "Vienna", "Seattle", "Oslo", "Prague", "United Kingdom")]}]


def _wd_item(req, title, place):
    return {"title": title, "externalPath": f"/job/{place}/{title.replace(' ', '-')}_{req}", "bulletFields": [req]}


class FakeWorkday:
    """Search results and job pages of one Workday site; records what was asked."""

    def __init__(self):
        self.items = [_wd_item("R1", "Forward Deployed Engineer", "New-York"), _wd_item("R2", "Bank Teller", "Ohio"),
                      _wd_item("R3", "Machine Learning Engineer", "Berlin")]
        self.pages = {"R1": {"title": "Forward Deployed Engineer", "location": "New York", "posted": True,
                             "country": {"descriptor": "United States of America"}, "startDate": "2026-10-09",
                             "jobDescription": "<p>Python, RAG and agents.</p>",
                             "externalUrl": "https://bank.wd3.myworkdayjobs.com/en-US/Careers"
                                            "/job/New-York/Forward-Deployed-Engineer_R1"},
                      "R3": {"title": "Machine Learning Engineer", "location": "Berlin", "posted": True,
                             "country": {"descriptor": "Germany"}}}
        self.searches, self.reads = [], []

    def post_json(self, url, payload):
        assert url == "https://bank.wd3.myworkdayjobs.com/wday/cxs/bank/Careers/jobs"
        self.searches.append(payload)
        if payload["limit"] == 1:
            return {"total": 3, "facets": WD_FACETS, "jobPostings": self.items[:1]}
        page = self.items[payload["offset"]:payload["offset"] + payload["limit"]]
        return {"total": len(self.items) if payload["offset"] == 0 else 0, "jobPostings": page}

    def get(self, url, conditional=True):
        path = url.removeprefix("https://bank.wd3.myworkdayjobs.com/wday/cxs/bank/Careers")
        req = path.rsplit("_", 1)[-1]
        self.reads.append(req)
        if req not in self.pages:
            raise httpx.HTTPStatusError("gone", request=httpx.Request("GET", url),
                                        response=httpx.Response(404, request=httpx.Request("GET", url)))
        return type("R", (), {"text": json.dumps({"jobPostingInfo": self.pages[req]})})()


class AllowAll:
    def allowed(self, url):
        return True


def test_workday_robots_and_country_facet():
    robots = "Sitemap: x\r\nUser-agent: *\r\nAllow: /Careers/\r\nallow: /Careers_Students/\r\nDisallow: /Global/\r\n"
    assert workday.allowed_sites(robots) == ["Careers", "Careers_Students"]
    assert workday.country_facet(WD_FACETS) == ("locationHierarchy1", ["us1", "ca1"])
    assert workday.country_facet(WD_FACETS[1:]) is None
    named = [{"facetParameter": "Location_Country", "descriptor": "Location Country",
              "values": [{"descriptor": c, "id": c} for c in ("Switzerland", "Singapore", "Spain", "Hong Kong", "India",
                                                               "Poland", "Japan", "Italy", "Monaco", "Israel", "Chile")]}]
    assert workday.country_facet(named) == ("Location_Country", ["Switzerland"])   # one match, but named country
    assert workday.Site.parse("bank.wd3/Careers").api == "https://bank.wd3.myworkdayjobs.com/wday/cxs/bank/Careers"


def test_workday_poll_reads_once_and_checks_before_closing(conn):
    wd = FakeWorkday()
    t0 = NOW.isoformat()
    first = poll_workday(conn, WD, wd, AllowAll(), at=t0)
    # Searched within our countries only; the teller is dropped by title, the Berlin job after reading it.
    assert all(s["appliedFacets"] == {"locationHierarchy1": ["us1", "ca1"]} for s in wd.searches[1:])
    assert (first["listed"], first["new"], first["closed"]) == (2, 1, 0) and sorted(wd.reads) == ["R1", "R3"]
    row = conn.execute("SELECT * FROM job_postings").fetchone()
    assert row["key"] == "bank:R1" and json.loads(row["markets"]) == ["US"] and row["posted_at"].startswith("2026-10-09")
    assert "RAG" in row["description"]

    # Next day: nothing is read again, neither the stored posting nor the skipped one.
    wd.reads.clear()
    second = poll_workday(conn, WD, wd, AllowAll(), at=(NOW + timedelta(hours=24)).isoformat())
    assert (second["matching"], second["new"], wd.reads) == (1, 0, [])

    # It drops out of the search results: past the grace period its own page is asked first.
    wd.items = wd.items[1:]
    t2 = (NOW + timedelta(hours=62)).isoformat()
    third = poll_workday(conn, WD, wd, AllowAll(), at=t2)
    assert third["closed"] == 0 and wd.reads == ["R1"]
    assert conn.execute("SELECT last_seen FROM job_postings").fetchone()[0] == t2     # still open
    del wd.pages["R1"]
    fourth = poll_workday(conn, WD, wd, AllowAll(), at=(NOW + timedelta(hours=100)).isoformat())
    assert fourth["closed"] == 1


def test_store_board_leaves_unknown_postings_open(conn):
    store_board(conn, CO, [_posting(1)], at=NOW.isoformat())
    later = (NOW + timedelta(hours=48)).isoformat()
    assert store_board(conn, CO, [], at=later, still_open=lambda row: None)["closed"] == 0
    assert store_board(conn, CO, [], at=later, still_open=lambda row: False)["closed"] == 1


class FakeLLM:
    def __init__(self, notes=None):
        self.total_cost, self.calls, self.notes = 0.0, [], notes

    def json(self, *, purpose, user, **kw):
        self.calls.append(purpose)
        if purpose == "weekly":
            return self.notes
        postings = json.loads(user.split("\n", 1)[1])
        return {"postings": [{
            "id": p["id"], "role_family": "fde_applied_ai", "seniority": "senior", "years_min": 5,
            "skills_required": ["python3", "k8s", "Retrieval-Augmented Generation", "Python"],
            "skills_preferred": ["K8S", "Model Context Protocol", "FooBar"],
            "customer_facing": True, "travel": "some", "remote_policy": "hybrid", "visa_sponsorship": "unknown",
            "salary_min": 100, "salary_max": 200, "salary_currency": "USD", "salary_period": "year",
            "summary": "Builds agents with customers."} for p in postings]}


def test_extraction_normalizes_skills_and_prefers_board_salary(conn, settings):
    p = _posting(1)
    p.salary = {"min": 150000, "max": 200000, "currency": "GBP", "period": "year"}
    store_board(conn, CO, [p, _posting(2)], at=NOW.isoformat())
    llm = FakeLLM()
    stats = extract_postings(conn, llm, model="m", prompts_dir=settings.prompts_dir, skills=SKILLS)
    assert stats == {"todo": 2, "extracted": 2, "failed": 0}
    rows = {r["key"]: json.loads(r["data"]) for r in conn.execute(
        "SELECT p.key, e.data FROM job_extractions e JOIN job_postings p ON p.id = e.posting_id")}
    d = rows["acme:1"]
    assert d["skills_required"] == ["Python", "Kubernetes", "RAG"]       # aliases mapped, duplicates gone
    assert d["skills_preferred"] == ["MCP", "FooBar"]                     # required ones not repeated
    assert d["salary"]["source"] == "board" and d["salary"]["currency"] == "GBP"
    assert rows["acme:2"]["salary"] == {"min": 100, "max": 200, "currency": "USD", "period": "year", "source": "text"}
    # Nothing new to read; a changed posting is read again.
    assert extract_postings(conn, llm, model="m", prompts_dir=settings.prompts_dir, skills=SKILLS)["todo"] == 0
    store_board(conn, CO, [_posting(1, text="Now with Terraform"), _posting(2)], at=NOW.isoformat())
    assert extract_postings(conn, llm, model="m", prompts_dir=settings.prompts_dir, skills=SKILLS)["todo"] == 1


def _seed_extracted(conn, n, *, posted, skills, first_seen, family="fde_applied_ai", start=0, salary=None):
    for i in range(start, start + n):
        conn.execute(
            "INSERT INTO job_postings (key, company, title, url, locations, markets, title_family, text_hash,"
            " posted_at, first_seen, last_seen) VALUES (?, 'acme', 't', 'u', '[]', '[\"UK\"]', ?, 'h', ?, ?, ?)",
            (f"acme:{i}", family, posted.isoformat(), first_seen.isoformat(), first_seen.isoformat()))
        pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        data = {"role_family": family, "seniority": "senior", "skills_required": skills(i), "skills_preferred": [],
                "customer_facing": True, "remote_policy": "hybrid", "visa_sponsorship": "unknown", "salary": salary}
        conn.execute("INSERT INTO job_extractions VALUES (?, 'v', 'h', ?, 'now')", (pid, json.dumps(data)))
    conn.commit()


def test_market_stats_shares_fit_and_trend_gate(conn):
    sal = {"min": 100000, "max": 150000, "currency": "GBP", "period": "year"}
    # Earlier period: MCP in 2 of 40; this week: MCP in 30 of 40.
    _seed_extracted(conn, 40, posted=NOW - timedelta(days=20), first_seen=NOW - timedelta(days=30),
                    skills=lambda i: ["Python"] + (["MCP"] if i < 2 else []), salary=sal)
    _seed_extracted(conn, 40, posted=NOW - timedelta(days=2), first_seen=NOW - timedelta(days=2), start=100,
                    skills=lambda i: ["Python"] + (["MCP"] if i < 130 else []))
    profile = {"have": ["Python"], "learning": ["MCP"]}
    stats = market_stats(conn, SKILLS, {"acme": "Acme AI"}, now_dt=NOW, fit_profile=profile)
    assert stats["target_open"] == 80 and stats["top_skills"][0] == {
        "name": "Python", "category": "languages", "pct": 100, "postings": 80, "required_pct": 100}
    assert stats["movement"]["ready"] and stats["movement"]["rising"][0]["name"] == "MCP"
    assert stats["salary"] == [{"role": "FDE / applied AI", "currency": "GBP", "low": 100, "high": 150, "postings": 40,
                                "range": "£100k–£150k"}]
    fit = stats["fit"]
    assert fit["coverage_pct"] == round(100 * 100 / (100 + 40))      # Python 100%, MCP 40%
    assert fit["gaps"][0] == {"name": "MCP", "pct": 40, "learning": True, "rising": True}
    # With less than two weeks of tracking there are no trend claims.
    conn.execute("UPDATE job_postings SET first_seen = ?", ((NOW - timedelta(days=3)).isoformat(),))
    assert not market_stats(conn, SKILLS, {}, now_dt=NOW)["movement"]["ready"]


def test_wilson_interval():
    lo, hi = wilson(30, 40)
    assert 0.59 < lo < 0.62 and 0.85 < hi < 0.87
    assert wilson(0, 0) == (0.0, 0.0)


def test_weekly_report_checks_numbers_and_fits_telegram(conn, settings):
    _seed_extracted(conn, 40, posted=NOW - timedelta(days=2), first_seen=NOW - timedelta(days=2),
                    skills=lambda i: ["Python", "RAG"])
    notes = {"headline": "Python is in every posting.",
             "market": {"intro": "All 40 open roles are in the UK.", "points": ["Roles: 93 new roles appeared."]},
             "skills": {"intro": "Python leads at 100%.", "points": []},
             "fit": {"intro": "You cover the core.", "points": ["Learn: pick up MCP next."]}}
    llm = FakeLLM(notes)
    s = replace(settings, fit_path=settings.out_dir / "missing.yaml")
    result = build_weekly(conn, s, llm=llm, now_dt=NOW)
    md = result["markdown"]
    assert "All 40 open roles are in the UK." in md and "93" not in md       # unsupported number dropped
    assert "**Learn:** pick up MCP next." in md and "Add private/skills.yaml" in md
    assert "1. Python 100%" in md and "Rising and falling skills start" in md
    assert all(visible_len(m) <= TELEGRAM_LIMIT for m in result["telegram"])
    assert len(result["warnings"]) == 1 and llm.calls == ["weekly"]
    row = conn.execute("SELECT kind, stats FROM reports").fetchone()
    assert row["kind"] == "jobs_weekly" and json.loads(row["stats"])["target_open"] == 40
