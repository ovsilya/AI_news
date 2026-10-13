"""Render docs/jobs/*.html, the pages that explain how the job search (lane ②) works.

The text of each page is in docs/jobs/src/<page>.html. Lists and limits come straight from the config
and the code (employers, skills, role rules, location examples, limits), so the pages stay in sync.
Run after editing either:  .venv/bin/python scripts/build_job_pages.py
"""
import html
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import yaml  # noqa: E402

from ainews.jobs import extract, filters, openings, poll, stats, workday  # noqa: E402
from ainews.jobs.boards import DESCRIPTION_CHARS, load_companies  # noqa: E402

OUT = ROOT / "docs" / "jobs"
SRC = OUT / "src"
e = html.escape

PAGES = [   # (slug, nav label, <title>)
    ("index", "Overview", "How the job search works"),
    ("sources", "1 · Sources", "Job search: where postings come from"),
    ("poll", "2 · Filter & track", "Job search: filter and track"),
    ("extract", "3 · Read", "Job search: reading postings"),
    ("openings", "4 · Openings", "Job search: openings for you"),
    ("report", "5 · Report & fit", "Job search: weekly report and fit"),
    ("operate", "Run & change it", "Job search: run and change it"),
]

CSS = """
:root{--bg:#f7f7f5;--panel:#fff;--ink:#1d1d1f;--muted:#5f6368;--border:#d5d7db;--sel:#2f6fdb;--line:#8a8f98;
--chip:#eef3fb;--chip-border:#9db5dc;--llm:#f3ecfd;--llm-border:#9a7ad1;--store:#eaf6ef;--store-border:#6fae8a;
--ext:#fdf3e6;--ext-border:#d39b4a;--good:#1e7d4f;--bad:#b3261e;--code:#f1f2f4}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#141518;--panel:#1c1e22;--ink:#e8e9eb;--muted:#a0a4ab;
--border:#3a3e45;--sel:#6ea2ff;--line:#7d838c;--chip:#18202c;--chip-border:#3f5a85;--llm:#261d36;--llm-border:#8c6cc6;
--store:#16271e;--store-border:#4f8d69;--ext:#2b2216;--ext-border:#b07f38;--good:#5cc28d;--bad:#ff8a80;--code:#25282d}}
:root[data-theme="dark"]{--bg:#141518;--panel:#1c1e22;--ink:#e8e9eb;--muted:#a0a4ab;--border:#3a3e45;--sel:#6ea2ff;
--line:#7d838c;--chip:#18202c;--chip-border:#3f5a85;--llm:#261d36;--llm-border:#8c6cc6;--store:#16271e;
--store-border:#4f8d69;--ext:#2b2216;--ext-border:#b07f38;--good:#5cc28d;--bad:#ff8a80;--code:#25282d}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.6 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:1100px;margin:0 auto;padding:20px 16px 48px}
a{color:var(--sel)}
.top{display:flex;flex-wrap:wrap;gap:6px 14px;align-items:center;font-size:14px;margin-bottom:18px}
.top .home{font-weight:600;margin-right:6px}
.top nav{display:flex;flex-wrap:wrap;gap:6px}
.top nav a{padding:2px 9px;border:1px solid var(--border);border-radius:999px;text-decoration:none;color:var(--ink);background:var(--panel)}
.top nav a[aria-current]{border-color:var(--sel);color:var(--sel);font-weight:600}
h1{font-size:24px;margin:0 0 6px}
h2{font-size:19px;margin:36px 0 8px;scroll-margin-top:12px}
h3{font-size:16px;margin:22px 0 6px}
p,li{max-width:84ch}
.lede{color:var(--muted);margin:0 0 8px;font-size:16px}
.lede b{color:var(--ink)}
.snap{font-size:13.5px;color:var(--muted)}
.muted{color:var(--muted);font-size:14px}
code{font:13px ui-monospace,SFMono-Regular,Menlo,monospace;background:var(--code);padding:1px 5px;border-radius:5px}
pre{font:12.5px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;background:var(--code);border:1px solid var(--border);
border-radius:10px;padding:12px 14px;overflow-x:auto;margin:8px 0}
pre code{background:none;padding:0}
.tg{white-space:pre-wrap;background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:12px 14px;font-size:14px;line-height:1.45;max-width:560px;overflow-wrap:anywhere}
.callout{background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:12px 16px;margin:10px 0}
.callout.key{border-left:4px solid var(--sel)}
.callout ol,.callout ul{margin:6px 0 0;padding-left:22px}
.callout li{margin:5px 0}
.frame{background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:12px;overflow-x:auto;margin:10px 0}
.frame svg{display:block;width:100%;height:auto;min-width:760px}
.frame.small svg{min-width:560px}
svg text{fill:var(--ink);font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
svg .t{font-size:14px;font-weight:600}
svg .s{font-size:12px;fill:var(--muted)}
svg .h{font-size:12px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;fill:var(--muted)}
svg .ext{fill:var(--ext);stroke:var(--ext-border)}
svg .comp{fill:var(--panel);stroke:var(--border)}
svg .llm{fill:var(--llm);stroke:var(--llm-border)}
svg .store{fill:var(--store);stroke:var(--store-border)}
svg .dash{fill:none;stroke:var(--border);stroke-dasharray:5 4}
svg rect{stroke-width:1.3}
svg .a{fill:none;stroke:var(--line);stroke-width:1.6}
svg .a.dim{stroke-dasharray:5 4}
svg .tip{fill:var(--line)}
svg .good{fill:var(--good)} svg .bad{fill:var(--bad)}
svg a:hover rect,svg a:focus-visible rect{stroke:var(--sel);stroke-width:2.2}
.steps{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:10px;margin:10px 0}
.step{background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:12px 14px;text-decoration:none;color:var(--ink);display:block}
a.step:hover{border-color:var(--sel)}
.step b{display:block;margin-bottom:4px}
.step span{font-size:14px;color:var(--muted)}
.flow{list-style:none;padding:0;margin:10px 0;counter-reset:f}
.flow>li{counter-increment:f;position:relative;background:var(--panel);border:1px solid var(--border);border-radius:10px;
padding:10px 14px 10px 50px;margin:0 0 18px;max-width:none}
.flow>li::before{content:counter(f);position:absolute;left:12px;top:10px;width:26px;height:26px;border-radius:50%;
background:var(--chip);border:1px solid var(--chip-border);text-align:center;font:600 13px/26px system-ui,sans-serif}
.flow>li:not(:last-child)::after{content:"↓";position:absolute;left:20px;bottom:-19px;color:var(--line);font-size:14px}
.flow ul{margin:4px 0 0;padding-left:20px}
.tablewrap{overflow-x:auto;background:var(--panel);border:1px solid var(--border);border-radius:10px;margin:10px 0}
table{border-collapse:collapse;width:100%;font-size:14px;min-width:640px}
th,td{text-align:left;padding:8px 12px;border-bottom:1px solid var(--border);vertical-align:top}
th{color:var(--muted);font-weight:600;font-size:13px}
tr:last-child td{border-bottom:none}
td.num,th.num{text-align:right;white-space:nowrap}
tr.group td{background:var(--chip);font-weight:600;font-size:13px}
.chip{display:inline-block;font-size:12px;padding:0 8px;margin:1px 2px 1px 0;border:1px solid var(--chip-border);background:var(--chip);border-radius:999px;white-space:nowrap}
.yes{color:var(--good);font-weight:600}.no{color:var(--bad);font-weight:600}
.bars{display:grid;gap:8px;margin:12px 0}
.bar{display:grid;grid-template-columns:minmax(0,1fr);gap:2px}
.bar .fill{height:22px;border-radius:5px;background:var(--chip);border:1px solid var(--chip-border);min-width:6px}
.bar .fill.llm{background:var(--llm);border-color:var(--llm-border)}
.bar .fill.store{background:var(--store);border-color:var(--store-border)}
.bar .fill.ext{background:var(--ext);border-color:var(--ext-border)}
.bar .lbl{font-size:14px}.bar .lbl b{font-variant-numeric:tabular-nums}
details{margin:8px 0}
summary{cursor:pointer;color:var(--sel);font-weight:600;font-size:14px}
.words{display:grid;grid-template-columns:minmax(120px,210px) 1fr;gap:6px 16px;margin:0}
.words dt{font-weight:600}.words dd{margin:0}
@media (max-width:560px){.words{grid-template-columns:1fr}.words dd{margin-bottom:8px}}
.tool{background:var(--panel);border:1px solid var(--sel);border-radius:10px;padding:12px 16px;margin:10px 0}
.tool label{font-weight:600;font-size:14px;display:block;margin-bottom:4px}
.tool input[type=text]{width:100%;max-width:520px;font:15px system-ui,sans-serif;padding:7px 10px;border:1px solid var(--border);
border-radius:8px;background:var(--bg);color:var(--ink)}
.tool input[type=number]{width:72px;font:15px system-ui,sans-serif;padding:5px 8px;border:1px solid var(--border);border-radius:8px;background:var(--bg);color:var(--ink)}
.tool .out{margin-top:8px;font-size:15px}
.tool .row{display:flex;flex-wrap:wrap;gap:6px 12px;align-items:center;margin:6px 0}
.tool .nw{white-space:nowrap}
.tool button{font:14px system-ui,sans-serif;padding:3px 10px;border:1px solid var(--border);border-radius:999px;background:var(--bg);color:var(--ink);cursor:pointer}
.tool button:hover{border-color:var(--sel)}
.foot{margin-top:32px;font-size:14px;display:flex;gap:16px;flex-wrap:wrap}
.next{margin-top:28px;display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap}
.next a{padding:8px 14px;border:1px solid var(--border);border-radius:10px;background:var(--panel);text-decoration:none}
.next a:hover{border-color:var(--sel)}
"""


def page(slug: str, body: str) -> str:
    cur = ' aria-current="page"'
    nav = "".join(f'<a href="{s}.html"{cur if s == slug else ""}>{e(label)}</a>' for s, label, _ in PAGES)
    i = [s for s, _, _ in PAGES].index(slug)
    prev = f'<a href="{PAGES[i - 1][0]}.html">← {e(PAGES[i - 1][1])}</a>' if i else "<span></span>"
    nxt = f'<a href="{PAGES[i + 1][0]}.html">{e(PAGES[i + 1][1])} →</a>' if i + 1 < len(PAGES) else "<span></span>"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(PAGES[i][2])}</title>
<style>{CSS}</style>
</head>
<body>
<main>
<div class="top" id="top"><a class="home" href="../architecture-diagram.html#card-b1">← Architecture diagram</a><nav aria-label="Job search pages">{nav}</nav></div>
{body}
<div class="next">{prev}{nxt}</div>
<p class="foot"><a href="#top">↑ Top</a><a href="../architecture-diagram.html">Architecture diagram</a><a href="../sources/job-boards.html">Job-board source list</a></p>
</main>
</body>
</html>
"""


# ---------- generated blocks ----------

def board_link(c) -> str:
    if c.ats == "workday":
        site = workday.Site.parse(c.board)
        return f"https://{site.host}/{site.site}"
    if c.ats == "greenhouse":
        return f"https://job-boards.greenhouse.io/{c.board}"
    if c.ats == "lever":
        return f"https://jobs.{'eu.' if c.region == 'eu' else ''}lever.co/{c.board}"
    return f"https://jobs.ashbyhq.com/{c.board}"


def company_groups() -> dict[str, str]:
    """Company id -> the group comment above it in companies.yaml ('Frontier labs…', 'Workday · US')."""
    groups, current, prev_comment = {}, "", None
    for line in (ROOT / "config" / "companies.yaml").read_text(encoding="utf-8").splitlines():
        m = re.match(r"^  # (.+)$", line)
        if m:
            prev_comment = m.group(1).strip()
            continue
        m = re.match(r"^  - \{id: ([^,]+),", line)
        if m:
            if prev_comment and not prev_comment.endswith(".") and len(prev_comment) < 90:
                current = prev_comment
            groups[m.group(1).strip()] = current
        prev_comment = None
    return groups


def employers_table(companies) -> str:
    groups = company_groups()
    ats_name = {"greenhouse": "Greenhouse", "lever": "Lever", "ashby": "Ashby", "workday": "Workday"}
    rows, last = [], None
    for c in companies:
        g = groups.get(c.id, "")
        g = f"Workday · {g}" if c.ats == "workday" else g
        if g != last:
            n = sum(1 for x in companies if (f"Workday · {groups.get(x.id, '')}" if x.ats == "workday"
                                             else groups.get(x.id, "")) == g)
            rows.append(f'<tr class="group"><td colspan="3">{e(g)} · {n}</td></tr>')
            last = g
        kind = "AI company" if c.ai_native else ("enterprise" if c.ats == "workday" else "tech company")
        rows.append(f'<tr><td>{e(c.name)}</td><td><a href="{e(board_link(c))}" target="_blank" rel="noopener">'
                    f'{ats_name[c.ats]}{" (EU)" if c.region == "eu" else ""}</a></td><td>{kind}</td></tr>')
    return ('<div class="tablewrap"><table><thead><tr><th>Employer</th><th>Career system (opens their job page)</th>'
            '<th>Counts as</th></tr></thead><tbody>' + "".join(rows) + "</tbody></table></div>")


CATEGORY = {"languages": "Languages", "ml_frameworks": "ML frameworks", "llm_frameworks": "LLM frameworks",
            "llm_techniques": "LLM techniques", "ml_practice": "ML practice", "model_providers": "Model providers",
            "serving": "Serving and inference", "vector_dbs": "Vector databases and search",
            "eval_observability": "Evaluation and observability", "cloud_infra": "Cloud and infrastructure",
            "backend_web": "Backend and web", "customer_facing": "Customer-facing and soft skills"}


def skills_table(raw: dict) -> str:
    rows = []
    for cat, names in raw.items():
        chips = "".join(
            f'<span class="chip" title="{e("also: " + ", ".join(al)) if al else ""}">{e(n)}'
            f'{" <span class=\"muted\">(" + e(", ".join(al)) + ")</span>" if al else ""}</span>'
            for n, al in (names or {}).items())
        rows.append(f"<tr><td>{e(CATEGORY.get(cat, cat))}<br><span class=\"muted\">{len(names or {})} names</span></td>"
                    f"<td>{chips}</td></tr>")
    return ('<div class="tablewrap"><table><thead><tr><th style="width:22%">Category</th>'
            '<th>Canonical names <span class="muted">(aliases that map to them)</span></th></tr></thead><tbody>'
            + "".join(rows) + "</tbody></table></div>")


def rules_detail() -> str:
    lines = [f"Title must name a role:  {filters._ROLE}",
             f"Never kept:              {filters._EXCLUDE.pattern}",
             f"'Names AI':              {filters._AI}", "", "Rules, first match wins:"]
    for fam, pat, needs_ai in filters._RULES:
        lines.append(f"  {fam:<15} {'(AI company or AI in title) ' if needs_ai else ''}{pat.pattern}")
    return "<pre><code>" + e("\n".join(lines)) + "</code></pre>"


def tester_data() -> str:
    data = {"role": filters._ROLE, "exclude": filters._EXCLUDE.pattern, "ai": filters._AI,
            "rules": [[f, p.pattern, n] for f, p, n in filters._RULES], "names": filters.FAMILIES}
    return json.dumps(data)


MARKET_EXAMPLES = [   # (locations, remote flag, board countries, note)
    (["San Francisco, CA"], None, [], "a US city"),
    (["Austin, TX"], None, [], "a state code after a comma"),
    (["Toronto, ON"], None, [], "a province code after a comma"),
    (["London, UK"], None, [], ""),
    (["Zurich"], None, [], ""),
    (["Dubai"], None, [], ""),
    (["Wilmington"], None, ["United States of America"], "city unknown, but the board says the country"),
    (["New York, On-site"], None, [], "“On” is not Ontario: codes match in capitals only"),
    (["Remote"], None, [], "remote, no country named"),
    (["Remote - US"], None, [], "remote within a market"),
    (["US, CA, Remote"], None, [], "Workday style: country, state, city"),
    (["Remote - Germany"], None, [], "remote, but in another country"),
    (["Paris"], True, [], "the board says remote, but names another place"),
    (["Berlin"], None, [], "outside our markets"),
]


def market_examples() -> str:
    rows = []
    for locs, remote, countries, note in MARKET_EXAMPLES:
        got = filters.markets(locs, remote, countries)
        extra = []
        if remote:
            extra.append("board: remote")
        if countries:
            extra.append("board country: " + ", ".join(countries))
        res = (" ".join(f'<span class="chip">{filters.MARKETS[m]}</span>' for m in got) if got
               else '<span class="no">dropped</span>')
        rows.append(f"<tr><td><code>{e(' · '.join(locs))}</code>"
                    f"{'<br><span class=muted>' + e('; '.join(extra)) + '</span>' if extra else ''}</td>"
                    f"<td>{res}</td><td class=\"muted\">{e(note)}</td></tr>")
    return ('<div class="tablewrap"><table><thead><tr><th>Location on the posting</th><th>Markets</th><th>Why</th>'
            '</tr></thead><tbody>' + "".join(rows) + "</tbody></table></div>")


FIELD_NOTES = {   # extraction field -> (what it holds, how the report uses it)
    "role_family": ("What the job really is, judged from the duties: one of the role families", "Which postings count; roles table"),
    "seniority": ("intern … principal, lead, manager, or unknown; from the title first, then the years asked for", "Seniority shares; the level part of an opening's fit"),
    "years_min": ("Minimum years of experience asked for (0 if not stated)", "Openings skip roles asking far more than your years"),
    "skills_required": ("Up to 15 skills the posting requires, most important first", "Skill shares, fit, openings"),
    "skills_preferred": ("Up to 10 nice-to-have skills", "Skill shares (a skill counts once per posting); openings at half weight"),
    "customer_facing": ("Does the job involve working directly with customers?", "Customer-facing share"),
    "travel": ("none, some, frequent or unknown", "Kept for later questions"),
    "remote_policy": ("onsite, hybrid, remote or unknown", "Remote share; shown with each opening"),
    "visa_sponsorship": ("yes, no or unknown, only from what the posting says", "Visa count; flagged on an opening"),
    "salary_min": ("Base salary range, only when the text states one", "Pay medians, if the board gave none"),
    "salary_max": ("", ""), "salary_currency": ("", ""), "salary_period": ("year, month, hour or unknown", ""),
    "summary": ("One plain sentence on what the person does", "Shown with each opening"),
}


def fields_table() -> str:
    props = extract.ITEM["properties"]
    missing = set(props) - set(FIELD_NOTES) - {"id"}
    if missing:
        raise SystemExit(f"describe these extraction fields in FIELD_NOTES: {sorted(missing)}")
    rows = []
    for name in props:
        if name == "id" or name in ("salary_max", "salary_currency"):
            continue
        what, use = FIELD_NOTES[name]
        label = "salary_min · max · currency" if name == "salary_min" else name
        enum = props[name].get("enum")
        kind = " · ".join(enum) if enum and name != "role_family" else props[name].get("type", "")
        if name == "salary_period":
            label, kind = "salary_period", " · ".join(enum)
        rows.append(f"<tr><td><code>{e(label)}</code></td><td>{e(what)}<br><span class=\"muted\">{e(kind)}</span></td>"
                    f"<td>{e(use)}</td></tr>")
    return ('<div class="tablewrap"><table><thead><tr><th style="width:24%">Field</th><th>What it holds</th>'
            '<th style="width:26%">Used for</th></tr></thead><tbody>' + "".join(rows) + "</tbody></table></div>")


# The openings example: the Legora posting from the extraction page, with a made-up skills list.
EXAMPLE_POSTING = {
    "role_family": "fde_applied_ai", "seniority": "mid", "years_min": 2, "remote_policy": "onsite",
    "visa_sponsorship": "unknown",
    "skills_required": ["Software engineering", "REST APIs", "Authentication", "Integration", "Data pipelines",
                        "Customer-facing work", "Communication", "Debugging"],
    "skills_preferred": ["TypeScript", "B2B", "AI agents", "Product sense"],
    "summary": "Builds and deploys integrations and software solutions by working directly with enterprise customers "
               "to solve their specific legal workflow needs.",
    "salary": {"min": 175100, "max": 245000, "currency": "USD", "period": "year", "source": "board"},
}
EXAMPLE_PROFILE = {"have": ["Software engineering", "REST APIs", "Data pipelines", "Customer-facing work", "Communication",
                            "Debugging", "AI agents"], "learning": ["TypeScript"]}


def _num(x: float) -> str:
    return f"{x:g}".replace(".5", "½") if x != 0.5 else "½"


def openings_example() -> str:
    d, prof = EXAMPLE_POSTING, EXAMPLE_PROFILE
    rows, total, earned = [], 0.0, 0.0
    for kind, names, weight in (("required", d["skills_required"], 1.0), ("preferred", d["skills_preferred"], 0.5)):
        for s in names:
            share = 1.0 if s in prof["have"] else 0.5 if s in prof["learning"] else 0.0
            yours = "have" if share == 1 else "learning" if share else "—"
            total, earned = total + weight, earned + weight * share
            rows.append(f"<tr><td>{e(s)}</td><td>{kind}</td><td>{yours}</td><td class=\"num\">{_num(weight)}</td>"
                        f"<td class=\"num\">{_num(weight * share) if share else '0'}</td></tr>")
    prefs = {**openings.DEFAULTS}
    skill_pts = openings.SKILL_POINTS * earned / total
    level_share = 1.0 if d["seniority"] in prefs["levels"] else openings.LEVEL_SHARE.get(d["seniority"], openings.OTHER_LEVEL_SHARE)
    fit = openings.fit_score(d, prof, prefs)
    if fit != round(skill_pts + openings.LEVEL_POINTS * level_share):
        raise SystemExit("openings example doesn't match fit_score")
    rows.append(f"<tr><td colspan=\"3\"><b>Total</b></td><td class=\"num\"><b>{_num(total)}</b></td>"
                f"<td class=\"num\"><b>{earned:g}</b></td></tr>")
    return ('<div class="tablewrap"><table><thead><tr><th>Skill</th><th>In the posting</th><th>In your list</th>'
            '<th class="num">Weight</th><th class="num">Earned</th></tr></thead><tbody>' + "".join(rows) +
            "</tbody></table></div>"
            f"<p>Skills: {openings.SKILL_POINTS} × {earned:g} ÷ {total:g} = {skill_pts:.2f} points. Level: “{d['seniority']}” "
            f"isn't in the default levels, so it earns {round(100 * level_share)}% of {openings.LEVEL_POINTS} = "
            f"{openings.LEVEL_POINTS * level_share:g} points. <b>Fit {fit}</b>, above the bar of "
            f"{prefs['min_fit']}, so it would be sent.</p>")


def openings_preview() -> str:
    d, prof = EXAMPLE_POSTING, EXAMPLE_PROFILE
    g = {"ids": [1], "fit": openings.fit_score(d, prof, openings.DEFAULTS), "posted": "2026-10-12T09:00:00+00:00",
         "company": "Legora", "title": "Junior/Mid-level Forward Deployed Engineer",
         "url": "https://jobs.ashbyhq.com/legora", "places": ["New York City, NY"], "family": d["role_family"],
         **openings._skills_for(d, prof),
         **{k: d.get(k) for k in ("seniority", "remote_policy", "visa_sponsorship", "salary", "summary")}}
    _, messages = openings.render([g], 60, 0, openings.title_for(datetime(2026, 10, 13)), openings.FOOTER)
    return messages[0]


def openings_defaults() -> str:
    notes = {"families": "role families to consider", "markets": "a posting needs at least one",
             "levels": "your level: full level points", "skip_levels": "never shown",
             "years": f"your years; postings asking more than this + {openings.EXTRA_YEARS} are skipped (empty: keep all)",
             "top": "openings per day", "min_fit": "the lowest fit worth sending"}
    if set(notes) != set(openings.DEFAULTS):
        raise SystemExit(f"describe these openings settings: {sorted(set(openings.DEFAULTS) ^ set(notes))}")
    rows = "".join(f"<tr><td><code>{k}</code></td><td>{e(', '.join(v) if isinstance(v, list) else str(v) if v is not None else 'empty')}</td>"
                   f"<td>{e(notes[k])}</td></tr>" for k, v in openings.DEFAULTS.items())
    return ('<div class="tablewrap"><table><thead><tr><th style="width:18%">Key</th><th style="width:40%">Default</th>'
            '<th>Meaning</th></tr></thead><tbody>' + rows + "</tbody></table></div>")


def wilson_pct(k, n) -> str:
    lo, hi = stats.wilson(k, n)
    return f"{round(100 * lo)}–{round(100 * hi)}%"


def prompt_version(name: str) -> str:
    first = (ROOT / "prompts" / f"{name}.md").read_text(encoding="utf-8").splitlines()[0]
    return re.search(r"version:\s*(\S+)", first).group(1)


def blocks() -> dict[str, str]:
    companies = load_companies(ROOT / "config" / "companies.yaml")
    skills_raw = yaml.safe_load((ROOT / "config" / "skills.yaml").read_text(encoding="utf-8"))
    n_ats = Counter(c.ats for c in companies)
    targets = [filters.FAMILIES[f] for f in stats.TARGET]
    return {
        "n_employers": str(len(companies)),
        "n_list": str(len(companies) - n_ats["workday"]),
        "n_workday": str(n_ats["workday"]),
        "n_greenhouse": str(n_ats["greenhouse"]), "n_lever": str(n_ats["lever"]), "n_ashby": str(n_ats["ashby"]),
        "n_ai_native": str(sum(c.ai_native for c in companies)),
        "employers_table": employers_table(companies),
        "n_skills": str(sum(len(v or {}) for v in skills_raw.values())),
        "n_skill_categories": str(len(skills_raw)),
        "skills_table": skills_table(skills_raw),
        "rules_detail": rules_detail(),
        "tester_data": tester_data(),
        "market_examples": market_examples(),
        "fields_table": fields_table(),
        "queries": " · ".join(f"“{q}”" for q in workday.QUERIES),
        "n_queries": str(len(workday.QUERIES)),
        "page_size": str(workday.PAGE), "max_pages": str(workday.MAX_PAGES),
        "max_details": str(workday.MAX_DETAILS), "max_checks": str(workday.MAX_CHECKS),
        "workers": str(poll.WORKDAY_WORKERS), "n_wd_hosts": str(len(workday.WD_HOSTS)),
        "wd_hosts": ", ".join(workday.WD_HOSTS),
        "close_after_h": str(int(poll.CLOSE_AFTER.total_seconds() // 3600)),
        "batch": str(extract.BATCH), "text_chars": f"{extract.TEXT_CHARS:,}",
        "description_chars": f"{DESCRIPTION_CHARS:,}",
        "extract_version": prompt_version("job_extract"), "weekly_version": prompt_version("weekly"),
        "top_skills": str(stats.TOP_SKILLS), "min_cohort": str(stats.MIN_COHORT),
        "min_tracking_days": str(stats.MIN_TRACKING.days), "min_skill": str(stats.MIN_SKILL),
        "min_salary": str(stats.MIN_SALARY),
        "targets": ", ".join(targets), "n_targets": str(len(targets)),
        "openings_fresh_days": str(openings.FRESH.days), "openings_top": str(openings.DEFAULTS["top"]),
        "openings_min_fit": str(openings.DEFAULTS["min_fit"]), "skill_points": str(openings.SKILL_POINTS),
        "level_points": str(openings.LEVEL_POINTS), "per_employer": str(openings.PER_EMPLOYER),
        "extra_years": str(openings.EXTRA_YEARS),
        "level_shares": ", ".join(f"{k} {round(100 * v)}%" for k, v in openings.LEVEL_SHARE.items()) + f", any other {round(100 * openings.OTHER_LEVEL_SHARE)}%",
        "openings_defaults": openings_defaults(), "openings_example": openings_example(),
        "openings_preview": openings_preview(),
        "example_have": ", ".join(EXAMPLE_PROFILE["have"][:-1]) + " and " + EXAMPLE_PROFILE["have"][-1],
        "example_learning": " and ".join(EXAMPLE_PROFILE["learning"]),
        "wilson_a": wilson_pct(24, 60), "wilson_b": wilson_pct(30, 200), "wilson_c": wilson_pct(9, 40),
    }


def main() -> None:
    values = blocks()
    for slug, _, _ in PAGES:
        src = (SRC / f"{slug}.html").read_text(encoding="utf-8")
        unknown = sorted(set(re.findall(r"\{\{(\w+)\}\}", src)) - set(values))
        if unknown:
            raise SystemExit(f"{slug}.html: unknown placeholders {unknown}")
        body = re.sub(r"\{\{(\w+)\}\}", lambda m: values[m.group(1)], src)
        (OUT / f"{slug}.html").write_text(page(slug, body), encoding="utf-8")
    print(f"wrote {len(PAGES)} pages to {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
