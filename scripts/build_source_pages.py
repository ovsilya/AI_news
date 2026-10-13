"""Render docs/sources/*.html from docs/sources/sources.json.

Run after editing sources.json:  python3 scripts/build_source_pages.py
"""
import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "sources"
DATA = json.loads((OUT / "sources.json").read_text(encoding="utf-8"))
BLOCKS, METHODS = DATA["blocks"], {m["id"]: m for m in DATA["methods"]}
e = html.escape

# Order of preference when a source offers more than one route (shown on methods.html).
LADDER = [
    ("Official feed",
     "The site publishes a list of its latest posts for programs to read. Easiest, free, and clearly allowed.",
     ["rss", "rss-filter", "releases"]),
    ("Official API",
     "The site offers a door for programs that answers with clean data, sometimes after a free sign-up.",
     ["json-api", "key-api"]),
    ("Files the site already publishes for machines",
     "Lists of pages, plain text files, data files, or the job data a careers page loads into your browser.",
     ["sitemap", "raw-diff", "dataset", "career-json"]),
    ("Compare copies of a page",
     "No feed and no API: save the page each time and see what changed.",
     ["page-diff"]),
    ("Proxy (an indirect route)",
     "Read something that stands in for the missing feed, such as a saved Google News search or a feed made by volunteers.",
     ["gnews", "community-feed", "commits"]),
    ("Paid search",
     "A company that collects the data legally on its side sells us search results. Used only where the site forbids programs.",
     ["search-api", "x-search"]),
    ("Emails sent to you",
     "For job boards that allow no programs at all: they email you the jobs, and we read the emails.",
     ["email"]),
]

# Plain-language glossary at the top of methods.html.
GLOSSARY = [
    ("Collector", "The small program in our pipeline that visits a source on a schedule and brings back what's new."),
    ("Web page vs. data", "A web page is designed for people to look at. Data is designed for programs to read: "
     "fixed labels such as title, date and link. Collectors always prefer data, because pages change their look often."),
    ("Feed (RSS / Atom)", "A file a site publishes that lists its latest posts with title, date and link. "
     "RSS and Atom are two formats for the same thing. Feed readers and our collector check it regularly."),
    ("API", "A web address made for programs. You ask a question (\"ML jobs at NVIDIA\") and get data back instead of a page."),
    ("JSON", "The text format most APIs answer in. It looks like \"title\": \"ML Engineer\", \"city\": \"Zurich\"."),
    ("API key", "A free code you get by registering, which tells the provider who is making the request. Treated like a password."),
    ("Release", "A new version of a piece of software, published with notes on what changed."),
    ("Sitemap", "A list of every page on a website, published so that search engines such as Google can find them."),
    ("Diff", "The difference between two copies of something. We compare today's copy with the last one and keep only what's new."),
    ("Proxy", "An indirect source standing in for one we can't read directly, such as a Google News search instead of a missing feed."),
    ("Scraping", "Having a program read pages meant for people and cut out the pieces it needs. Fragile, and many sites forbid it."),
    ("robots.txt", "A small file where a site says which of its parts automated programs may and may not visit."),
    ("Terms of service", "A site's rules of use. Several job boards (LinkedIn, Indeed) forbid any automated reading."),
    ("Rate limit", "The most requests a site accepts in a given time. Going over it gets you slowed down or blocked."),
]

# Rules every collector follows: (rule, plain explanation, technical detail).
RULES = [
    ("Say who we are",
     "When our program visits a site it introduces itself by name, with a link to this project, much like a delivery "
     "driver wearing a uniform. If the site owner has a problem, they can contact us instead of simply blocking us.",
     "A User-Agent header naming the bot with a contact URL."),
    ("Only download what changed",
     "Before downloading, the collector asks: \"has anything changed since my last visit?\" If not, the site replies "
     "with a two-line \"nothing new\" and we skip the download. This saves time and bandwidth on both sides.",
     "Conditional GET with ETag / If-Modified-Since; a 304 Not Modified reply means no download and no parsing."),
    ("Don't overload anyone",
     "We visit each site gently: one request at a time, with a pause in between. If a site says \"too many requests\" "
     "or has a problem, we wait and try later, waiting longer after each failed try.",
     "Per-host concurrency of 1, delays between requests, exponential backoff on HTTP 429 and 5xx. Strict sites get "
     "more room: Reddit is visited at most every 30 seconds, and after a 429 we wait a full minute and try once more."),
    ("Follow the site's own rules",
     "Feeds and APIs are made for programs, so using them is always fine. For anything else, the collector first reads "
     "the site's robots.txt and stays out of any part the site asks programs to avoid.",
     "robots.txt is checked for everything that isn't an official feed or API."),
    ("Read the article, not just the headline",
     "Many feeds only send a headline or a one-line teaser, which is too little to explain a story. For the stories "
     "that make it into the brief, the collector opens the article page once, checks robots.txt first, and keeps only "
     "the main text for the writer to read. It never gets around a paywall: a paywalled page simply gives a teaser.",
     "For each chosen story with under 800 characters of text: one GET of the article URL (robots.txt honoured), main "
     "text extracted with trafilatura, cached for 30 days. Google News links can't be opened directly and are skipped."),
    ("Keep only what's on topic",
     "Some search feeds are broad: a search for Reuters articles about AI also returns elections and oil prices. These "
     "feeds get a keyword filter, so only headlines about AI (or, for the jobs search, about work and jobs) are kept.",
     "Per-source keyword filters on title and summary; acronyms such as AI match whole words only."),
    ("Notice when a source goes quiet",
     "Sources break silently. After a redesign, a feed can simply come back empty. Each source has an expected rhythm "
     "(say, about one post a week). If it errors, returns nothing, or stays silent much longer than usual, it is flagged "
     "in the weekly report so it can be fixed or replaced.",
     "Source health check: zero items, errors, or no new items beyond the usual cadence raise an alert."),
    ("Keep keys locked away",
     "API keys work like passwords. They live in Google Secret Manager, a locked vault in our cloud account that the "
     "program opens when it runs. They are never written into the code, which is public on GitHub.",
     "Secrets come from Secret Manager at run time; never in the repo or the container image."),
]

CSS = """
:root{--bg:#f7f7f5;--panel:#fff;--ink:#1d1d1f;--muted:#5f6368;--border:#d5d7db;--sel:#2f6fdb;--chip:#eef3fb;--chip-border:#9db5dc}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#141518;--panel:#1c1e22;--ink:#e8e9eb;--muted:#a0a4ab;--border:#3a3e45;--sel:#6ea2ff;--chip:#18202c;--chip-border:#3f5a85}}
:root[data-theme="dark"]{--bg:#141518;--panel:#1c1e22;--ink:#e8e9eb;--muted:#a0a4ab;--border:#3a3e45;--sel:#6ea2ff;--chip:#18202c;--chip-border:#3f5a85}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:1100px;margin:0 auto;padding:20px 16px 48px}
a{color:var(--sel)}
.top{display:flex;flex-wrap:wrap;gap:6px 14px;align-items:center;font-size:14px;margin-bottom:18px}
.top .home{font-weight:600;margin-right:6px}
.top nav{display:flex;flex-wrap:wrap;gap:6px}
.top nav a{padding:2px 9px;border:1px solid var(--border);border-radius:999px;text-decoration:none;color:var(--ink);background:var(--panel)}
.top nav a[aria-current]{border-color:var(--sel);color:var(--sel);font-weight:600}
h1{font-size:22px;margin:0 0 6px}
h2{font-size:18px;margin:32px 0 8px}
.lede{color:var(--muted);margin:0 0 6px;max-width:80ch}
.lede b{color:var(--ink);font-weight:600}
.count{font-size:14px;color:var(--muted);margin:10px 0 12px}
.tablewrap{overflow-x:auto;background:var(--panel);border:1px solid var(--border);border-radius:10px}
table{border-collapse:collapse;width:100%;font-size:14px;min-width:720px;table-layout:fixed}
th,td{text-align:left;padding:8px 12px;border-bottom:1px solid var(--border);vertical-align:top;overflow-wrap:anywhere}
th{color:var(--muted);font-weight:600;font-size:13px}
th:nth-child(1){width:26%}th:nth-child(2){width:28%}th:nth-child(3){width:46%}
tr:last-child td{border-bottom:none}
td:first-child{font-weight:500}
td:nth-child(2) a{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12.5px}
.chips{display:flex;flex-wrap:wrap;gap:4px;margin-top:5px}
.chip{font-size:12px;padding:0 8px;border:1px solid var(--chip-border);background:var(--chip);border-radius:999px;text-decoration:none;color:var(--ink);white-space:nowrap}
.chip:hover{border-color:var(--sel)}
.methods{display:grid;gap:10px}
.m{background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:12px 16px;scroll-margin-top:12px}
.m:target{border-color:var(--sel);box-shadow:0 0 0 1px var(--sel)}
.m h3{font-size:16px;margin:0 0 6px}
.m p{margin:4px 0;max-width:90ch}
.m .k{font-weight:600;color:var(--muted)}
.m .used{font-size:13px;color:var(--muted);margin-top:6px}
.callout{background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:12px 16px}
.callout ol,.callout ul{margin:6px 0 0;padding-left:20px}
.callout li{margin:3px 0}
.m .plain{font-size:15.5px}
.m details{margin-top:6px;font-size:14px}
.m summary{cursor:pointer;color:var(--sel);font-weight:600;font-size:13.5px}
.muted{color:var(--muted);font-size:13.5px}
.callout ol li,.rules li{margin:8px 0}
.words{display:grid;grid-template-columns:minmax(120px,190px) 1fr;gap:6px 16px;margin:0}
.words dt{font-weight:600}
.words dd{margin:0}
@media (max-width:560px){.words{grid-template-columns:1fr}.words dd{margin-bottom:8px}}
.foot{margin-top:28px;font-size:14px;display:flex;gap:16px;flex-wrap:wrap}
"""


def short(url):
    s = url.split("://", 1)[-1]
    return s[4:] if s.startswith("www.") else s


def page(title, slug, body, back_anchor):
    cur = ' aria-current="page"'
    links = [(f'{b["slug"]}.html', b["slug"], b["name"]) for b in BLOCKS] + [("methods.html", "methods", "All methods")]
    nav = "".join(f'<a href="{href}"{cur if key == slug else ""}>{e(name)}</a>' for href, key, name in links)
    back = f"../architecture-diagram.html{back_anchor}"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title>
<style>{CSS}</style>
</head>
<body>
<main>
<div class="top"><a class="home" href="{back}">← Architecture diagram</a><nav aria-label="Source lists">{nav}</nav></div>
{body}
<p class="foot"><a href="#top">↑ Top</a><a href="{back}">← Back to the architecture diagram</a></p>
</main>
</body>
</html>
"""


def method_card(mid, used_by):
    m = METHODS[mid]
    return (
        f'<section class="m" id="m-{mid}"><h3>{e(m["name"])}</h3>'
        f'<p class="plain">{e(m["plain"])}</p>'
        f'<details><summary>Technical details</summary>'
        f'<p><span class="k">How it works:</span> {e(m["how"])}</p>'
        f'<p><span class="k">Why this method:</span> {e(m["why"])}</p>'
        f'<p><span class="k">Watch-outs:</span> {e(m["watch"])}</p></details>'
        f'<p class="used">{used_by}</p></section>'
    )


def block_page(b):
    rows = []
    for s in b["sources"]:
        chips = "".join(f'<a class="chip" href="#m-{t}">{e(METHODS[t]["name"])}</a>' for t in s["methods"])
        rows.append(
            f'<tr><td>{e(s["name"])}</td>'
            f'<td><a href="{e(s["url"])}" target="_blank" rel="noopener">{e(short(s["url"]))}</a></td>'
            f'<td>{e(s["how"])}<div class="chips">{chips}</div></td></tr>'
        )
    used = []
    for s in b["sources"]:
        for t in s["methods"]:
            if t not in used:
                used.append(t)
    cards = "".join(
        method_card(t, "Used here by: " + ", ".join(e(s["name"]) for s in b["sources"] if t in s["methods"]))
        for t in used
    )
    body = f"""<h1 id="top">{e(b["name"])}</h1>
<p class="lede"><b>What:</b> {e(b["what"])}</p>
<p class="lede"><b>Why:</b> {e(b["why"])}</p>
<p class="count">{len(b["sources"])} sources · {len(used)} retrieval methods · links checked {DATA["updated"]}</p>
<div class="tablewrap"><table>
<thead><tr><th>Source</th><th>Link</th><th>How it's retrieved</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table></div>
<h2>Retrieval methods on this page</h2>
<p class="lede">Why each source is fetched the way it is. New to these terms? The <a href="methods.html">methods overview</a> starts with a <a href="methods.html#words">plain-language glossary</a>, then explains how a method is chosen and the rules every collector follows.</p>
<div class="methods">{cards}</div>"""
    return page(b["name"] + " sources", b["slug"], body, f'#card-{b["id"]}')


def methods_page():
    where = {mid: [] for mid in METHODS}
    for b in BLOCKS:
        for s in b["sources"]:
            for t in s["methods"]:
                if b not in where[t]:
                    where[t].append(b)
    ladder = "".join(
        f'<li><b>{e(name)}.</b> {e(note)}<br><span class="muted">Methods: '
        + ", ".join(f'<a href="#m-{t}">{e(METHODS[t]["name"])}</a>' for t in ids) + "</span></li>"
        for name, note, ids in LADDER
    )
    words = "".join(f"<dt>{e(w)}</dt><dd>{e(d)}</dd>" for w, d in GLOSSARY)
    rules = "".join(
        f'<li><b>{e(r)}.</b> {e(plain)}<br><span class="muted">Technically: {e(tech)}</span></li>'
        for r, plain, tech in RULES
    )
    order = [t for _, _, ids in LADDER for t in ids] + [t for t in METHODS if t not in sum((l[2] for l in LADDER), [])]
    cards = "".join(
        method_card(t, "Used in: " + ", ".join(f'<a href="{b["slug"]}.html#m-{t}">{e(b["name"])}</a>' for b in where[t]))
        for t in order
    )
    body = f"""<h1 id="top">Retrieval methods</h1>
<p class="lede">How the pipeline collects news, papers and job posts from about 100 sources, and why each source is read the way it is. Written for readers new to the topic: each method starts with a plain explanation, and the technical details are one click away.</p>
<h2 id="words">Key words</h2>
<div class="callout"><dl class="words">{words}</dl></div>
<h2>How a method is chosen</h2>
<div class="callout">
<p>Many sources can be read in more than one way. We go down this list and use the first option that works for that source. Options near the top are the ones the site itself offers to programs, so they are allowed, free, and rarely break. Options further down are used only when nothing above exists.</p>
<ol>{ladder}</ol>
<p>The checks behind this order: Is it allowed by the site's rules? Is it free or nearly free? Will it keep working if the site changes its design? Is it fresh enough for a brief twice a day? Does it return clean data rather than a page we'd have to pick apart?</p>
<p><b>Never done:</b> logging in to a site as you, getting past \"are you a robot?\" checks (CAPTCHAs), scraping sites that forbid it (LinkedIn, Indeed, jobs.ch, Bayt, Naukrigulf), or clicking links inside job-alert emails, which would count as automated visits to those sites.</p>
</div>
<h2>Rules every collector follows</h2>
<div class="callout"><p>However a source is read, the collector behaves like a polite, well-identified visitor:</p><ul class="rules">{rules}</ul></div>
<h2>All methods</h2>
<div class="methods">{cards}</div>"""
    return page("Retrieval methods", "methods", body, "")


if __name__ == "__main__":
    for b in BLOCKS:
        (OUT / f'{b["slug"]}.html').write_text(block_page(b), encoding="utf-8")
    (OUT / "methods.html").write_text(methods_page(), encoding="utf-8")
    print(f"wrote {len(BLOCKS) + 1} pages to {OUT.relative_to(ROOT)}")
