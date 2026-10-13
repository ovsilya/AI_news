"""Which postings we keep: target role families (by title) in the five markets, plus remote.

Titles give a first guess at the role family; the model's extraction refines it. A title alone is
not enough for "Solutions Architect" or "Research Engineer", so those count only at AI companies
or when the title itself names AI."""
from __future__ import annotations

import re

FAMILIES = {   # display names, in report order
    "fde_applied_ai": "FDE / applied AI",
    "solutions_eng": "Solutions engineer",
    "llm_genai_eng": "LLM / GenAI engineer",
    "ai_eng": "AI engineer",
    "ml_eng": "ML engineer",
    "ml_research": "ML research",
    "data_science": "Data science",
    "other": "Other",
}
MARKETS = {"US": "USA", "CA": "Canada", "UK": "UK", "CH": "Switzerland", "AE": "UAE", "remote": "Remote"}

_AI = r"(\bai\b|\bml\b|machine learning|\bllms?\b|gen(erative)?[ -]?ai|deep learning|\bnlp\b|artificial intelligence)"
_ROLE = r"(engineer|engineering|architect|developer|scientist|deployed|deployment|strategist|researcher|technical staff)"
_EXCLUDE = re.compile(r"\b(intern|internship|recruit\w*|account (executive|manager)|counsel|attorney|paralegal|"
                      r"designer|writer|sales development|business development|talent)\b", re.I)
_RULES = [   # first match wins
    ("fde_applied_ai", re.compile(r"forward[- ]deployed|deployment strategist|applied ai|ai deployment|"
                                  r"(ai|ml|llm|genai) solutions? (engineer|architect)|solutions? (engineer|architect)"
                                  r",? (ai|ml|llm|genai|applied ai)", re.I), False),
    ("solutions_eng", re.compile(r"solutions? (engineer|architect)|sales engineer|customer engineer|"
                                 r"field engineer|technical (account|success) (manager|engineer)", re.I), True),
    ("llm_genai_eng", re.compile(r"\bllms?\b|large language model|gen(erative)?[ -]?ai|prompt engineer|"
                                 r"\bagents?\b.*engineer|engineer.*\bagents?\b", re.I), False),
    ("ml_eng", re.compile(r"machine learning|\bml\b|mlops|ml ?ops|deep learning|\bnlp\b|computer vision|"
                          r"inference|model (serving|training)", re.I), False),
    ("ml_research", re.compile(r"research (engineer|scientist)|applied scientist|researcher", re.I), True),
    ("ai_eng", re.compile(r"\bai\b|artificial intelligence", re.I), False),
    ("ai_eng", re.compile(r"member of technical staff", re.I), True),   # the AI labs' generic title
    ("data_science", re.compile(r"data scientist", re.I), True),
]


def role_family(title: str, ai_native: bool) -> str | None:
    """The target role family a title suggests, or None for roles we don't track."""
    if _EXCLUDE.search(title) or not re.search(_ROLE, title, re.I):
        return None
    names_ai = bool(re.search(_AI, title, re.I))
    for family, pattern, needs_ai in _RULES:
        if pattern.search(title) and (not needs_ai or ai_native or names_ai):
            return family
    return None


_STATES = ("AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND "
           "OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC").split()
_PATTERNS = {
    "US": re.compile(r"\b(united states|usa|u\.s\.a?\.?|us|america|new york|san francisco|seattle|boston|austin|"
                     r"chicago|los angeles|denver|atlanta|washington,? d\.?c|bay area|silicon valley|palo alto|"
                     r"mountain view|menlo park|santa clara|sunnyvale|san jose|san mateo|redwood city|"
                     r"san diego|miami|dallas|houston|pittsburgh|philadelphia)\b", re.I),
    "CA": re.compile(r"\b(canada|toronto|montr[eé]al|vancouver|ottawa|calgary|waterloo|edmonton|qu[eé]bec)\b", re.I),
    "UK": re.compile(r"\b(united kingdom|uk|u\.k\.|great britain|england|scotland|wales|london|manchester|"
                     r"edinburgh|bristol|belfast|oxford)\b|\bcambridge, (uk|england)\b", re.I),
    "CH": re.compile(r"\b(switzerland|schweiz|suisse|zurich|z[uü]rich|geneva|gen[eè]ve|basel|lausanne|bern|zug|"
                     r"lugano)\b", re.I),
    "AE": re.compile(r"\b(united arab emirates|uae|u\.a\.e\.|dubai|abu dhabi|sharjah)\b", re.I),
}
_CODES = {   # state and province codes after a comma: "Austin, TX", "Toronto, ON" (exact case)
    "US": re.compile(r", (" + "|".join(_STATES) + r")\b"),
    "CA": re.compile(r", (ON|BC|QC|AB)\b"),
}
_COUNTRY_CODES = {"US": "US", "CA": "CA", "GB": "UK", "UK": "UK", "CH": "CH", "AE": "AE"}
_REMOTE = re.compile(r"\bremote\b", re.I)


def markets(locations: list[str], remote: bool | None = None, countries: list[str] = ()) -> list[str]:
    """Markets a posting is open in (US, CA, UK, CH, AE), plus "remote" for remote roles.

    A remote role with no country named counts as remote only; one that names another country
    (say "Remote - Germany") is not in our markets and is dropped."""
    texts = list(locations) + [c for c in countries if c and len(c) > 2]   # Ashby: "United States"
    found = {code for code, p in _PATTERNS.items() if any(p.search(t) for t in texts)}
    found |= {code for code, p in _CODES.items() if any(p.search(loc) for loc in locations)}
    found |= {_COUNTRY_CODES[c.upper()] for c in countries if c and c.upper() in _COUNTRY_CODES}
    is_remote = bool(remote) or any(_REMOTE.search(loc) for loc in locations)
    if is_remote and (found or not _names_other_place(locations, countries)):
        found.add("remote")
    return [m for m in MARKETS if m in found]


def _names_other_place(locations: list[str], countries) -> bool:
    """True if the locations name some place besides 'Remote' (e.g. 'Remote - Germany')."""
    if any(c for c in countries if c):
        return True
    rest = [_REMOTE.sub("", loc).strip(" -–,|/()") for loc in locations]
    return any(r and r.lower() not in {"global", "anywhere", "worldwide", "friendly", "first"} for r in rest)
