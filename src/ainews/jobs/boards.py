"""Employers' own job boards: the public posting APIs of Greenhouse, Lever and Ashby.

Each returns the employer's full list of open postings, so a posting missing from today's list has
closed. No keys, no scraping: these APIs are published for exactly this use. Workday career sites
have no full list; they are searched instead (workday.py)."""
from __future__ import annotations

import html
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

from ..collectors import html_to_text

LIST_ATS = ("greenhouse", "lever", "ashby")   # boards that return their full list of open postings
ATS = LIST_ATS + ("workday",)
TIERS = ("top", "established", "startup")   # how hard the employer is to get into; openings mix all three
DESCRIPTION_CHARS = 12_000
PERIODS = {"1 YEAR": "year", "1 MONTH": "month", "1 HOUR": "hour", "per-year-salary": "year",
           "per-month-salary": "month", "per-hour-wage": "hour"}


@dataclass(frozen=True)
class Company:
    id: str
    name: str
    ats: str
    board: str            # the slug in the board's URL, e.g. boards.greenhouse.io/<board>; Workday: "tenant.wdN/Site"
    ai_native: bool = True   # an AI company: its solutions and research roles count as AI roles
    enabled: bool = True
    region: str = ""      # "eu" for Lever's EU instance
    tier: str = ""        # one of TIERS; required in companies.yaml


@dataclass
class Posting:
    job_id: str
    title: str
    url: str
    locations: list[str]
    description: str = ""
    department: str = ""
    remote: bool | None = None       # from the board's own workplace field, when it has one
    posted_at: str | None = None     # ISO 8601
    salary: dict | None = None       # {"min", "max", "currency", "period"} from the board
    extra: list[str] = field(default_factory=list)   # more location hints (countries)


def load_companies(path: Path, include_disabled: bool = False) -> list[Company]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out, seen = [], set()
    for row in raw.get("companies") or []:
        c = Company(**row)
        if c.ats not in ATS:
            raise ValueError(f"{c.id}: unknown ats {c.ats!r}")
        if c.tier not in TIERS:
            raise ValueError(f"{c.id}: tier must be one of {', '.join(TIERS)}, not {c.tier!r}")
        if c.id in seen:
            raise ValueError(f"duplicate company id {c.id}")
        seen.add(c.id)
        if c.enabled or include_disabled:
            out.append(c)
    return out


def _text(s: str | None) -> str:
    return html_to_text(s, DESCRIPTION_CHARS)


def _iso_ms(ms) -> str | None:
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat(timespec="seconds")


def board_url(c: Company) -> tuple[str, dict]:
    if c.ats == "greenhouse":
        return (f"https://boards-api.greenhouse.io/v1/boards/{c.board}/jobs",
                {"content": "true", "pay_transparency": "true"})
    if c.ats == "lever":
        host = "api.eu.lever.co" if c.region == "eu" else "api.lever.co"
        return f"https://{host}/v0/postings/{c.board}", {"mode": "json"}
    return f"https://api.ashbyhq.com/posting-api/job-board/{c.board}", {"includeCompensation": "true"}


def parse_greenhouse(data: dict) -> list[Posting]:
    out = []
    for j in data.get("jobs") or []:
        offices = j.get("offices") or []
        locations = [(j.get("location") or {}).get("name") or ""] + [o.get("location") or o.get("name") or ""
                                                                    for o in offices]
        meta = {m.get("name"): m.get("value") for m in j.get("metadata") or [] if isinstance(m, dict)}
        kind = str(meta.get("Location Type") or "").lower()
        salary = None
        for p in j.get("pay_input_ranges") or []:
            if p.get("min_cents") and p.get("max_cents"):
                salary = {"min": p["min_cents"] / 100, "max": p["max_cents"] / 100,
                          "currency": p.get("currency_type") or "", "period": "year"}
                break
        out.append(Posting(
            job_id=str(j["id"]), title=j.get("title") or "", url=j.get("absolute_url") or "",
            locations=[x for x in locations if x],
            description=_text(html.unescape(j.get("content") or "")),
            department=", ".join(d.get("name", "") for d in j.get("departments") or []),
            remote=("remote" in kind) or None,
            posted_at=j.get("first_published") or j.get("updated_at"),
            salary=salary,
        ))
    return out


def parse_lever(data: list) -> list[Posting]:
    out = []
    for j in data or []:
        cats = j.get("categories") or {}
        lists = "\n".join(f"{x.get('text', '')}: {_text(x.get('content'))}" for x in j.get("lists") or [])
        body = "\n".join(x for x in (j.get("descriptionPlain") or _text(j.get("description")), lists,
                                     j.get("additionalPlain") or "") if x)
        rng = j.get("salaryRange") or {}
        salary = None
        if rng.get("min") and rng.get("max"):
            salary = {"min": rng["min"], "max": rng["max"], "currency": rng.get("currency") or "",
                      "period": PERIODS.get(rng.get("interval") or "", "year")}
        workplace = (j.get("workplaceType") or "").lower()
        out.append(Posting(
            job_id=j["id"], title=j.get("text") or "", url=j.get("hostedUrl") or "",
            locations=[x for x in [cats.get("location") or ""] + list(cats.get("allLocations") or []) if x],
            description=body[:DESCRIPTION_CHARS],
            department=" / ".join(x for x in (cats.get("department"), cats.get("team")) if x),
            remote=(workplace == "remote") or None,
            posted_at=_iso_ms(j.get("createdAt")),
            salary=salary,
            extra=[j.get("country") or ""],
        ))
    return out


def parse_ashby(data: dict) -> list[Posting]:
    out = []
    for j in data.get("jobs") or []:
        if j.get("isListed") is False:
            continue
        places = [j.get("address")] + [s.get("address") for s in j.get("secondaryLocations") or []]
        countries = [((a or {}).get("postalAddress") or {}).get("addressCountry") or "" for a in places]
        locations = [j.get("location") or ""] + [s.get("location") or "" for s in j.get("secondaryLocations") or []]
        salary = None
        for comp in (j.get("compensation") or {}).get("summaryComponents") or []:
            if comp.get("compensationType") == "Salary" and comp.get("minValue") and comp.get("maxValue"):
                salary = {"min": comp["minValue"], "max": comp["maxValue"], "currency": comp.get("currencyCode") or "",
                          "period": PERIODS.get(comp.get("interval") or "", "year")}
                break
        out.append(Posting(
            job_id=j["id"], title=j.get("title") or "", url=j.get("jobUrl") or "",
            locations=[x for x in locations if x],
            description=(j.get("descriptionPlain") or _text(j.get("descriptionHtml")))[:DESCRIPTION_CHARS],
            department=" / ".join(x for x in (j.get("department"), j.get("team")) if x),
            # isRemote is also true for hybrid jobs; the workplace type, when set, says what it really is.
            remote=(j.get("workplaceType") == "Remote") if j.get("workplaceType") else (bool(j.get("isRemote")) or None),
            posted_at=j.get("publishedAt"),
            salary=salary,
            extra=[c for c in countries if c],
        ))
    return out


PARSERS = {"greenhouse": parse_greenhouse, "lever": parse_lever, "ashby": parse_ashby}


def fetch_postings(c: Company, fetcher) -> list[Posting]:
    url, params = board_url(c)
    r = fetcher.get(url, params=params, conditional=False)
    return PARSERS[c.ats](json.loads(r.text or "null") or {})


def probe(slug: str, fetcher) -> list[tuple[str, int]]:
    """Which boards answer for this slug, with their number of open postings: [(ats, count)]."""
    found = []
    for ats, region in [(a, "") for a in LIST_ATS] + [("lever", "eu")]:
        try:
            n = len(fetch_postings(Company(slug, slug, ats, slug, region=region), fetcher))
        except Exception:
            continue
        if n:
            found.append((f"{ats}-{region}" if region else ats, n))
    return found
