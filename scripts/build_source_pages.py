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
    ("Official feed", "RSS/Atom, release feeds", ["rss", "rss-filter", "releases"]),
    ("Official or public API", "JSON, free key", ["json-api", "key-api"]),
    ("Structured files the publisher already serves", "sitemaps, raw files, datasets, career-site JSON",
     ["sitemap", "raw-diff", "dataset", "career-json"]),
    ("Diff of a public page", "only when nothing above exists", ["page-diff"]),
    ("Proxy", "a search feed, community feed or commit log stands in for a missing feed",
     ["gnews", "community-feed", "commits"]),
    ("Paid search", "only where the site forbids direct access", ["search-api", "x-search"]),
    ("Email sent to you", "for job boards that forbid automated access", ["email"]),
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
        f'<p><span class="k">How it works:</span> {e(m["how"])}</p>'
        f'<p><span class="k">Why this method:</span> {e(m["why"])}</p>'
        f'<p><span class="k">Watch-outs:</span> {e(m["watch"])}</p>'
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
<p class="lede">Why each source is fetched the way it is. The <a href="methods.html">methods overview</a> explains the order of preference and the rules every collector follows.</p>
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
        f'<li><b>{e(name)}</b> ({e(note)}): '
        + ", ".join(f'<a href="#m-{t}">{e(METHODS[t]["name"])}</a>' for t in ids) + "</li>"
        for name, note, ids in LADDER
    )
    order = [t for _, _, ids in LADDER for t in ids] + [t for t in METHODS if t not in sum((l[2] for l in LADDER), [])]
    cards = "".join(
        method_card(t, "Used in: " + ", ".join(f'<a href="{b["slug"]}.html#m-{t}">{e(b["name"])}</a>' for b in where[t]))
        for t in order
    )
    body = f"""<h1 id="top">Retrieval methods</h1>
<p class="lede">How the collectors get data from each kind of source, and why that route was chosen over the alternatives.</p>
<h2>How a method is chosen</h2>
<div class="callout">
<p>For each source we take the first route on this list that works. The criteria, in order: the site's terms and robots.txt allow it; it costs nothing or close to it; it doesn't break when the site is redesigned; it is fresh enough for a twice-daily brief; and it returns structured fields rather than HTML.</p>
<ol>{ladder}</ol>
<p>Never: logging in to a site, solving CAPTCHAs, scraping LinkedIn, Indeed, jobs.ch, Bayt or Naukrigulf, or opening links from alert emails.</p>
</div>
<h2>Rules every collector follows</h2>
<div class="callout"><ul>
<li><b>Identify the bot.</b> A User-Agent naming the project with a contact URL, so site owners can reach us instead of blocking us.</li>
<li><b>Conditional GET.</b> Send ETag / If-Modified-Since; a 304 reply means no download and no parsing.</li>
<li><b>Rate limits per host.</b> One request at a time per host, delays between requests, exponential backoff on 429 and 5xx.</li>
<li><b>robots.txt is respected</b> for anything that isn't an official feed or API.</li>
<li><b>Source health check.</b> A source that returns zero items, errors, or nothing new for longer than its usual cadence raises an alert in the weekly report.</li>
<li><b>Keys stay in Secret Manager</b>, never in the repo or the container image.</li>
</ul></div>
<h2>All methods</h2>
<div class="methods">{cards}</div>"""
    return page("Retrieval methods", "methods", body, "")


if __name__ == "__main__":
    for b in BLOCKS:
        (OUT / f'{b["slug"]}.html').write_text(block_page(b), encoding="utf-8")
    (OUT / "methods.html").write_text(methods_page(), encoding="utf-8")
    print(f"wrote {len(BLOCKS) + 1} pages to {OUT.relative_to(ROOT)}")
