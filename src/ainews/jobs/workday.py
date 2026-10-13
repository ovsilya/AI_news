"""Workday career sites (banks, insurers, pharma, NVIDIA and other enterprises).

Each employer's public career site runs on a JSON API: POST /wday/cxs/{tenant}/{site}/jobs searches,
GET /wday/cxs/{tenant}/{site}/job/… returns one posting. robots.txt allows it and lists the sites.

Unlike Greenhouse, Lever and Ashby there is no full list to diff: search results are ranked by
relevance, 20 at a time. So we search a few AI keywords within our five countries (each tenant's own
country filter), keep the titles in our role families, read each new posting once, and check a
posting's own page before calling it closed. The polling itself is in poll.py."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

import httpx

from ..collectors import html_to_text
from .boards import DESCRIPTION_CHARS, Company, Posting
from .filters import role_family

log = logging.getLogger(__name__)

COUNTRIES = {"united states of america", "united states", "usa", "us", "canada", "united kingdom", "uk",
             "great britain", "switzerland", "united arab emirates", "uae"}
QUERIES = ("machine learning", "LLM", "generative AI", "AI engineer", "artificial intelligence",
           "forward deployed", "applied AI")
PAGE = 20
MAX_PAGES = 10        # per query; we also stop at the first page with no matching titles
MAX_DETAILS = 60      # new postings read per employer per run; the rest wait for the next run
MAX_CHECKS = 30       # missing postings whose own page is checked per employer per run
WD_HOSTS = ("wd1", "wd3", "wd5", "wd12", "wd10", "wd103", "wd101", "wd105", "wd108")
NOT_A_JOB_SITE = re.compile(r"talent|private|intern|student|early|alumni|internal|refresh|campus|graduate|university",
                            re.I)


@dataclass(frozen=True)
class Site:
    host: str
    tenant: str
    site: str

    @classmethod
    def parse(cls, board: str) -> "Site":
        """'nvidia.wd5/NVIDIAExternalCareerSite' -> the site's host, tenant and name."""
        hostpart, site = board.split("/", 1)
        return cls(f"{hostpart}.myworkdayjobs.com", hostpart.split(".")[0], site)

    @property
    def api(self) -> str:
        return f"https://{self.host}/wday/cxs/{self.tenant}/{self.site}"


def country_facet(facets: list) -> tuple[str, list[str]] | None:
    """(parameter, value ids) of the tenant's country filter, narrowed to our five markets.
    Tenants name it differently (locationCountry, Location_Country, locationHierarchy1, …), so a facet
    counts if its name says country or at least a tenth of its values are our countries. A city list
    that happens to hold a "United Kingdom" entry (Roche, AstraZeneca) would hide most postings."""
    best = None
    stack = list(facets)
    while stack:
        f = stack.pop()
        values = f.get("values") or []
        nested = [v for v in values if v.get("facetParameter")]
        if nested:
            stack.extend(nested)
            continue
        ids = [v["id"] for v in values if (v.get("descriptor") or "").strip().lower() in COUNTRIES and v.get("id")]
        named = "country" in f"{f.get('facetParameter')} {f.get('descriptor')}".lower()
        if ids and (named or len(ids) >= 0.1 * len(values)) and (best is None or len(ids) > len(best[1])):
            best = (f.get("facetParameter"), ids)
    return best


def job_id(item: dict) -> str:
    """The requisition ID when the site shows one; it survives title and location edits."""
    fields = item.get("bulletFields") or []
    return str(fields[0]) if fields else item.get("externalPath", "")


def search(c: Company, fetcher) -> dict[str, dict]:
    """Matching search results by job id: titles in our role families, within our countries."""
    site = Site.parse(c.board)
    first = fetcher.post_json(f"{site.api}/jobs", {"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""})
    facet = country_facet(first.get("facets") or [])
    applied = {facet[0]: facet[1]} if facet else {}
    found: dict[str, dict] = {}
    for query in QUERIES:
        total = None
        for page in range(MAX_PAGES):
            data = fetcher.post_json(f"{site.api}/jobs", {"appliedFacets": applied, "limit": PAGE,
                                                          "offset": page * PAGE, "searchText": query})
            total = data.get("total") or total or 0   # some tenants send the total on the first page only
            items = data.get("jobPostings") or []
            hits = [it for it in items if role_family(it.get("title") or "", c.ai_native)]
            for it in hits:
                found.setdefault(job_id(it), it)
            if not hits or len(items) < PAGE or (page + 1) * PAGE >= total:
                break
    return found


def detail(fetcher, site: Site, path: str) -> dict | None:
    """One posting's details, or None if it is gone. Other errors raise: unknown is not closed."""
    try:
        r = fetcher.get(f"{site.api}{path}", conditional=False)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (404, 410):
            return None
        raise
    info = (json.loads(r.text or "{}") or {}).get("jobPostingInfo")
    if not info or info.get("posted") is False or info.get("canApply") is False:
        return None
    return info


def to_posting(item: dict, info: dict, site: Site) -> Posting:
    country = (info.get("country") or {}).get("descriptor") or ""
    start = info.get("startDate") or ""
    remote = str(info.get("remoteType") or "").lower()
    return Posting(
        job_id=job_id(item), title=info.get("title") or item.get("title") or "",
        url=info.get("externalUrl") or f"https://{site.host}/{site.site}{item.get('externalPath', '')}",
        locations=[x for x in [info.get("location") or ""] + list(info.get("additionalLocations") or []) if x],
        description=html_to_text(info.get("jobDescription"), DESCRIPTION_CHARS),
        remote=(remote == "remote") or None,
        posted_at=f"{start}T00:00:00+00:00" if re.fullmatch(r"\d{4}-\d{2}-\d{2}", start) else None,
        extra=[country],
    )


def path_of(url: str) -> str | None:
    """The API path of a stored posting, from its public URL ('…/Site/job/City/Title_R123' -> '/job/…')."""
    i = url.find("/job/")
    return url[i:] if i >= 0 else None


def allowed_sites(robots_txt: str) -> list[str]:
    """Career sites a tenant's robots.txt allows ('Allow: /Site/'); disallowed ones are left alone."""
    return list(dict.fromkeys(re.findall(r"(?mi)^[ \t]*Allow:[ \t]*/([A-Za-z0-9_-]+)/?[ \t]*\r?$", robots_txt)))


def probe(tenant: str, fetcher) -> list[tuple[str, int]]:
    """Career sites of a Workday tenant with their number of open postings: [('tenant.wdN/Site', n)].
    Pass 'tenant' to try the usual data centres, or 'tenant.wdN' to try one."""
    hosts = [tenant] if "." in tenant else [f"{tenant}.{wd}" for wd in WD_HOSTS]
    found = []
    for hostpart in hosts:
        try:
            r = fetcher.get(f"https://{hostpart}.myworkdayjobs.com/robots.txt", conditional=False)
        except Exception:
            continue
        sites = allowed_sites(r.text or "")
        for name in sites:
            if NOT_A_JOB_SITE.search(name):
                continue
            board = f"{hostpart}/{name}"
            try:
                data = fetcher.post_json(f"{Site.parse(board).api}/jobs",
                                         {"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""})
            except Exception:
                continue
            if data.get("total"):
                found.append((board, data["total"]))
        if found:
            break
    return found
