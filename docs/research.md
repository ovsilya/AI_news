# Research: building a daily AI-industry brief

*Researched 2026-10-12. Feeds and endpoints marked **[live]** were called on that date. Items marked ⚠️ could not be fully verified. Prices are list prices on that date.*

Audience: an ML / GenAI / LLM engineer or Forward Deployed Engineer (FDE) who wants one daily brief covering news, tools, research, trends and the job market, built from many sources.

---

## 1. What already exists (and what's missing)

### 1.1 Newsletters worth ingesting as inputs

| Source | Cadence | Focus | Machine access | Signal for an engineer |
|---|---|---|---|---|
| **AINews** (swyx / smol.ai) | Weekdays | Recaps AI Discords, Reddit and X | Now published as `[AINews]` posts in `latent.space/feed`. The old `news.smol.ai/rss.xml` has had no new posts since 2026-09-09. A move to Beehiiv ("v3") is planned, so the URL will probably change again ⚠️ | Very high, and long enough to be good raw LLM input |
| Simon Willison | ~Daily | Hands-on with tools and models | `simonwillison.net/atom/everything/` | Very high |
| Latent Space | Several posts/week | AI engineering | `latent.space/feed` | High |
| Interconnects (N. Lambert) | 1–2 posts/week | Post-training, open models | `interconnects.ai/feed` | High |
| Import AI | Weekly | Research, policy | `importai.substack.com/feed` | High for research trends |
| TLDR AI | Weekdays | Broad; many links | `tldr.tech/api/rss/ai` | Good link seeds |
| Last Week in AI | Weekly | Recap | `lastweekin.ai/feed` | Weekly check that nothing was missed |
| Ben's Bites | 2–3 posts/week | Products and builders | `bensbites.com/feed` | Medium |
| The Batch | Weekly | Broad | No RSS (404); scrape the page | Medium, lags the news |
| The Rundown AI | Daily | Business and consumer | `therundown.ai/feed` | Low–medium |
| AlphaSignal | ~3×/week | Repos, models, papers | No official RSS; the Substack mirror stopped in Jul 2026 | Medium-high, but needs email ingestion |

### 1.2 Aggregator products

- **Feedly (Leo / Feedly AI):** AI prioritisation and dedup. It has shifted toward enterprise threat and market intelligence, so its categories don't fit LLM engineers.
- **Inoreader and Readwise Reader:** good readers. Neither ranks content for AI-engineering relevance.
- **Particle and Perplexity Discover:** consumer news with no technical depth.
- **daily.dev:** a developer feed. Its Public API v1 (Plus plan) exposes your personalised feed, so it could be an input.
- **Emergent Mind:** trending arXiv papers with discussion. Its RSS returns an empty channel today ⚠️
- **alphaXiv:** a trending paper feed with an MCP server. No public API or RSS.
- **Releasebot** ($5/mo Pro): aggregates release notes (~1.1k OpenAI and ~880 Anthropic entries) with RSS, an API and MCP. It is the only product that tracks changelogs.
- **Shut down in 2026:** ChatGPT Pulse (replaced by Scheduled Tasks) and Huxe. A scheduled ChatGPT or Claude prompt is now the do-it-yourself baseline this project has to beat.

### 1.3 Open-source projects to borrow from

| Repo | Stars | Why look at it |
|---|---|---|
| **KKKKhazix/AIHOT** (aihot.news) | ~7.4k | Best reference for scoring. An LLM pre-screen feeds two independent scores, with a cut-off that depends on how authoritative the source is. Duplicate reports merge into one event (embeddings, 14-day window). Heat counts each independent source once per 48 h and decays with a 24 h half-life. The daily report is assembled by fixed rules, and there is a calibration bench. Prompts are in Chinese |
| **TideDra/zotero-arxiv-daily** | ~6.0k | Ranks new papers by similarity to *your own* library. Runs free on GitHub Actions |
| **frankzch/ai-news-brief** | small | 370+ sources, two-stage dedup (SimHash → pgvector), re-checks low-engagement posts later |
| **leungll/FrontierSignal** | small | Closest to the target audience: AI-engineering focus, P0/P1 tiers, bge-small, Slack/Telegram |
| **WZZNNE/AI-Resonance** | small | Scores items by how many sources mention them; runs on Actions and Pages |
| **barretlee/agent-pulse** | 240 | A workflow that audits whether sources are still alive |
| karpathy/arxiv-sanity-lite | 1.7k | A tf-idf + SVM ranking baseline (abandoned) |
| **DIYgod/RSSHub**, **trafilatura**, **miniflux** / **FreshRSS** | — | Infrastructure: RSS for sites without feeds, article text extraction, and a feed store |

### 1.4 Gaps, and where this project can stand out

1. **Job-market requirement trends.** Nothing turns job postings into skill-trend data over time. The only examples are one-off reports, such as Bloomberry's analysis of 1,000 FDE jobs and Plank's count of 1,333 FDE roles.
2. **Tool changelogs and breaking changes.** Newsletters cover launches. SDK versions, deprecations and pricing changes go uncovered (Releasebot aside).
3. **Ranking by your own relevance.** Everyone ranks by popularity. Nobody ranks against your stack, your clients or your current projects.
4. **Dedup across aggregators.** AINews, TLDR and the Rundown are themselves aggregations, so one model launch shows up 5+ times. Each item has to be resolved to its original source.
5. **The FDE angle.** Nobody covers what a release means for enterprise customers: compliance, cost, latency and availability by region and cloud.
6. **Hype vs. adoption.** Few tools cross-check a claim against real usage signals such as star growth, HF downloads or OpenRouter usage share.
7. **Sources break.** AINews has moved twice, Emergent Mind's RSS is empty, and Pulse and Huxe shut down. The system needs automatic health checks on its sources.

---

## 2. Content sources

### 2.1 Research

| Source | Access | Cost / limits | Signal |
|---|---|---|---|
| **HF Daily Papers** | `huggingface.co/api/daily_papers?date=…&sort=trending` [live] | Free. 500 req / 5 min without a token, 1,000 with a free token | **High** (curated, upvoted). Its Trending Papers replaced Papers with Code, which shut down in Jul 2025 |
| **arXiv** | `rss.arxiv.org/rss/cs.CL+cs.LG+cs.AI` [live], `export.arxiv.org/api/query` | Free. 1 request per 3 s, 1 connection | High volume; needs ranking |
| Semantic Scholar | Graph API with a free key | 1 RPS with a key; the shared pool returns 429s | Enrichment only (citations, TLDRs) |
| alphaXiv | No public API ⚠️ | — | Medium |
| OpenReview | API v2; guest calls returned 403 today ⚠️ | — | Only around ICLR/NeurIPS decisions |

### 2.2 Models, code and tools

| Source | Access | Notes | Signal |
|---|---|---|---|
| **GitHub Releases** | `github.com/{o}/{r}/releases.atom` [live], no auth | 5,000 REST requests/h with a personal access token (PAT) | **High**: vllm, sglang, llama.cpp, transformers, langgraph, ollama, anthropic/openai SDKs, `claude-code/CHANGELOG.md` |
| GitHub trending | No API. Use the OSS Insight `api.ossinsight.io/v1/trends/repos` [live], or Search `created:>D sort:stars` | Scraping HTML is a ToS grey area | High |
| **HF Hub trending** | `/api/models?sort=trendingScore`, `/api/spaces?sort=trendingScore` [live] | Free | **High** |
| **OpenRouter** | `/api/v1/models` [live], no key; rankings Data API needs a key | New models appear within hours with prices; CC-BY, cite "Source: OpenRouter" | **High** |
| Arena (formerly LMArena) | HF dataset `lmarena-ai/leaderboard-dataset` | CC-BY | Medium; diff it weekly |
| Artificial Analysis | REST v2 with a key; free tier 100 req/day | Attribution required | Medium–high |
| PyPI / npm | `pypi.org/rss/project/{pkg}/releases.xml`, `registry.npmjs.org/{pkg}/latest` | Free | Medium (SDK version bumps) |
| Product Hunt | GraphQL v2 or Atom feed | Non-commercial use only | Low–medium |

### 2.3 Community

| Source | Access | Notes | Signal |
|---|---|---|---|
| **Hacker News** | Algolia `hn.algolia.com/api/v1`, Firebase, `hnrss.org/newest?q=LLM&points=100` [live] | Free; Algolia allows 10k req/h | **High** |
| **Reddit** | `reddit.com/r/LocalLLaMA/top/.rss?t=day` returned 200 [live]. The `.json` endpoints return 403. OAuth now needs manual approval under the Responsible Builder Policy (2–4 weeks) | Use RSS ⚠️ ToS on RSS polling unclear | **High** (r/LocalLLaMA) |
| X / Twitter | Pay-per-use only since Feb 2026: $0.005/post read | ~300 posts/day ≈ $45/mo. The xAI X Search tool costs $5 per 1k calls | High, but paid; skip at first |
| Bluesky | Public AppView feeds and lists, no auth [live]; Jetstream firehose free | Many ML researchers post here | Medium |
| YouTube | Channel RSS needs no quota [live] | Transcripts: `youtube-transcript-api` is blocked from cloud IPs ⚠️ | Medium |
| Discord | Bot must be added by a server admin; self-bots are banned | — | Only for servers you control |

### 2.4 Vendor blogs and changelogs

| Vendor | Feed |
|---|---|
| OpenAI | `openai.com/news/rss.xml` [live]; developer changelog `developers.openai.com/rss.xml` |
| **Anthropic** | **No RSS.** Diff `sitemap.xml` or scrape `/news`, `/engineering`, `/research`. Release notes at `platform.claude.com/docs/en/release-notes/overview` are HTML only. Community feed: `Olshansk/rss-feeds` |
| Google | DeepMind `deepmind.google/blog/rss.xml`; `developers.googleblog.com/rss/`. **The Gemini API changelog has no feed** (scrape it) |
| Meta AI | **No feed.** Follow the `meta-llama` org on GitHub and HF |
| Mistral | `mistral.ai/news/rss` |
| AWS / Azure / GCP | AWS What's New feed; Azure release-communications RSS; `vertex-ai-release-notes.xml`. All need heavy filtering |
| Hugging Face | `huggingface.co/blog/feed.xml` |

### 2.5 Web search and extraction services (for gaps and deep dives)

| Service | Free | Paid | Use |
|---|---|---|---|
| **Jina Reader** (`r.jina.ai/{url}`) | 20 RPM with no key; 500 RPM and 10M tokens with a free key | Billed per token | HTML → markdown for pages without feeds |
| **Firecrawl** | 1k credits/mo ⚠️ | $16/mo for 5k | Page-change monitoring (Anthropic, Meta, Gemini changelogs) |
| **Exa** | $10 credit/mo | $4–15 per 1k | Semantic "find new posts about X" |
| **Tavily** | 1k credits/mo | $0.008 per credit | Agent search |
| Brave Search | $5 credit/mo (card required) | $5 per 1k | General search |
| Perplexity Sonar | No free tier | $5–12 per 1k + tokens | Cited answers |
| SerpApi | 250 searches/mo | $25/mo for 1k | Google sued it (refiled Aug 2026), so carries legal risk |
| NewsAPI / GDELT | NewsAPI is dev-only; GDELT is free and noisy | — | Low value |

---

## 3. Job-market data

**Bottom line:** poll the job-board APIs that employers' applicant-tracking systems (ATS) expose: Greenhouse, Lever and Ashby. They are free, legal, and the data comes from the employer. Add HN "Who is hiring" and a small Google Jobs quota through SerpApi for breadth. Extract structured requirements with a cheap LLM. Do **not** scrape LinkedIn or Indeed. hiQ v. LinkedIn (2022) and LinkedIn v. ProAPIs (consent judgment Sep 2026) show their bans are enforced.

### 3.1 Sources

| Source | Endpoint | Notes |
|---|---|---|
| **Greenhouse** | `boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true` | Anthropic: 646 jobs today, 5 titled "Forward Deployed". `pay_input_ranges` is mostly filled for US roles only |
| **Lever** | `api.lever.co/v0/postings/{slug}?mode=json` (`api.eu.lever.co` for EU) | Palantir: 310 postings, 79 FDE |
| **Ashby** | `api.ashbyhq.com/posting-api/job-board/{name}?includeCompensation=true` | OpenAI: 809 jobs, 26 FDE-titled. Compensation is structured. Responses are large (~8 MB) |
| SmartRecruiters / Workable | Public posting endpoints | Workable path not verified ⚠️ |
| **HN Who is hiring** | Algolia `search_by_date?tags=story,author_whoishiring`, then `items/{id}` | Monthly, ~380 comments, free text |
| **SerpApi Google Jobs** | 250 searches/mo free | Covers LinkedIn and Indeed listings indirectly |
| JSearch (RapidAPI) | 100–200 requests/mo free ⚠️ | Fallback |
| TheirStack | 200 credits/mo free | Historical / tech-stack backfill |
| Adzuna | Free tier | **ToS forbids aggregate statistics**, so it can't be used for trends |
| Indeed Hiring Lab AI Tracker | `github.com/hiring-lab/ai-tracker` CSVs, monthly, CC-BY | Best machine-readable macro series |

**Building the target-company list.** No ATS publishes its customer list, so you assemble one:
- the unofficial `yc-oss.github.io/api/companies/hiring.json` (1,493 hiring YC companies, 638 tagged AI)
- Forbes AI 50 and the frontier and applied-AI labs
- VC portfolio job boards (a16z, Accel)

Then probe each candidate slug against all three ATS APIs and keep the ones that respond.

### 3.2 Extraction schema (strict JSON via structured outputs)

Fields to extract from each posting:

- **Role:** `role_family` (LLM / GenAI / ML / AI Eng / FDE-Applied AI / Solutions Eng), `title_normalized`, `seniority`, `years_min/max`
- **Skills:** `skills_required[]`, `skills_preferred[]`
- **Stack:** `frameworks[]`, `llm_providers[]`, `cloud[]`, `vector_dbs[]`, `eval_observability[]`, `languages[]`
- **Role character:** `customer_facing`, `travel_pct`
- **Location:** `remote_policy`, `locations[]`, `visa_sponsorship`
- **Pay:** `salary{min,max,currency,period,source}`, `equity`
- **Audit:** `evidence_quotes[]`

Use the ATS's own salary and location fields where they exist, and fall back to LLM extraction otherwise.

Three practical points:
- **Classify by role family, not title.** Anthropic has only 5 jobs titled "Forward Deployed", but many Applied AI and Solutions roles are the same job.
- **Merge multi-city duplicates.** One requisition is often posted in several cities.

**Skills taxonomy:** keep your own alias dictionary of AI tools, plus ESCO (free API) or O\*NET embeddings for normalisation. Lightcast is now free only for nonprofits.

**Trends:**
- Group postings into weekly cohorts by the date first seen.
- For each skill, compute its share of new postings, the change in percentage points week over week, and a 4-week rolling average. Mark a skill as rising only when its Wilson confidence intervals don't overlap.
- Also track: companies hiring for a role family for the first time, median salary by role and location, and how long postings stay open.

**FDE context** (useful for framing):
- FT/Indeed: FDE postings rose more than 800% from Jan to Sep 2025.
- OpenAI Deployment Company (May 2026): $4B+ in backing, and it bought Tomoro.
- Anthropic set up an enterprise AI-services firm with Blackstone, H&F and Goldman (May 2026).

---

## 4. Pipeline building blocks

| Block | Default pick | Alternative | Notes |
|---|---|---|---|
| Orchestration | Plain Python 3.12 + uv, idempotent stages checkpointed in SQLite; add **DBOS** if needed | Prefect 3 (free Hobby cloud) | Temporal, Dagster and Hatchet are overkill. n8n fits poorly with Python |
| LLM calls | **Anthropic SDK** with structured outputs (or PydanticAI for typed outputs and provider swapping) | Claude Agent SDK, for 1–2 agentic "deep-dive" steps only | LangGraph and CrewAI add little to a linear pipeline |
| Fetching and parsing | httpx (ETag / If-Modified-Since) + feedparser + **trafilatura** | Jina Reader / Firecrawl for JS-heavy pages; self-hosted RSSHub; changedetection.io | |
| Dedup | 1) URL canonicalisation (courlan / w3lib), with arXiv abs/pdf/vN mapped to one ID; 2) MinHash-LSH (datasketch) at Jaccard ≈0.8; 3) embedding cosine ≥0.88 within a 72 h window | — | Tune the thresholds on labelled pairs |
| Clustering into stories | Agglomerative clustering (cosine, average linkage, distance threshold) + centroid assignment, so story IDs stay stable | HDBSCAN | BERTopic for weekly topic maps |
| Embeddings | **voyage-4-lite** ($0.02/M tokens; first 200M tokens free) | Local bge-m3 / Qwen3-Embedding-0.6B (CPU is enough) | ~45M tokens/mo ≈ $0.90 |
| Storage | **SQLite (WAL) + sqlite-vec**; DuckDB for trend analytics | Postgres + pgvector on Neon (free tier), or Turso | ~1.1M items/yr; prune vectors after 90 days but keep story centroids |
| Ranking | Rules/engagement prefilter, then an **LLM-as-judge** with a written profile (relevance 0–3, novelty vs. the last 14 days, actionability, credibility) | Logistic regression / LightGBM once there are ~300 thumbs | Rewrite the profile from feedback weekly; keep ~10% exploration slots |
| Feedback | Telegram inline buttons (👍 👎 mute, more like this) | Signed links in emails | |
| Faithfulness checks | **Claude Citations**; reject uncited sentences; check that every number and version string appears in the sources | HHEM-2.1-Open / MiniCheck NLI on CPU | Golden set of ~30 labelled days |
| Observability | **Langfuse** (Cloud Hobby free, or self-hosted under MIT) | Arize Phoenix; promptfoo for prompt regression tests in CI | |

### 4.1 Claude models (pricing per 1M tokens, cached 2026-10-06)

| Model | ID | Input | Output | Role here |
|---|---|---|---|---|
| Claude Opus 5.5 | `claude-opus-5-5` | $4 | $20 | Final "editor": writes the brief, synthesises weekly trends |
| Claude Sonnet 5.5 | `claude-sonnet-5-5` | $2 | $10 | Cheaper editor option |
| Claude Haiku 5.5 | `claude-haiku-5-5` | $0.10 | $0.50 | Bulk triage, scoring, job extraction (prices apply up to 100K-token prompts) |

The Message Batches API costs 50% less for jobs that aren't urgent (triage, job extraction). Prompt caching cuts the cost of re-sending the profile and rubric with every call.

---

## 5. Deployment and delivery

### 5.1 Where to run it

| Option | Cost | Precision | Notes |
|---|---|---|---|
| **GitHub Actions** | Free for public repos; private repos get 2,000 min/mo | **Unreliable:** one 10-day test measured median 26 min late, worst 10 h ⚠️ | Public repos' schedules are disabled after 60 days without activity. ToS grey area for non-CI workloads |
| **Cloudflare Workers + Cron + D1 / R2 / Queues / Workflows** | Free plan too tight (10 ms CPU, 50 subrequests), so $5/mo | ~12 s | Python Workers went GA 2026-09-21 on Pyodide, so check lxml and trafilatura compatibility |
| **Hetzner VPS + systemd timers** | ~€6/mo (CX23, 2 vCPU / 4 GB) | Exact | No platform limits; can run Playwright, an always-on bot and local models. You patch and back it up yourself |
| Modal | $30/mo free credits | cron with timezone | Starter allows ~5 crons ⚠️ |
| Cloud Run Jobs + Scheduler | Free tier is generous; needs billing set up | Exact | |
| Lambda + EventBridge | Free tier | Exact | 15-min limit |
| Railway / Render / Fly / Vercel | $1–5/mo | Varies; Vercel Hobby runs once a day ±59 min | Not better than the above |
| **Claude Code routines** | Counts against your Claude subscription | 1 h minimum interval | Research preview. Network allowlist must be set to Custom/Full. Good for a "Phase 0" baseline |
| **Claude Managed Agents scheduled deployments** | $0.08 per session-hour + tokens | Cron with ≤9 min jitter | Beta. Good for an agentic deep-dive step, but you still need your own DB and delivery |

**Trick:** a free Cloudflare cron Worker can call GitHub's `workflow_dispatch` API, which gives on-time GitHub Actions runs.

### 5.2 Hosted databases

| DB | Free tier | Watch out for |
|---|---|---|
| Supabase | 500 MB | Pauses after a week of inactivity |
| Neon | 1 GB, 100 compute-hours/mo | — |
| Turso | 5 GB, native vectors | — |
| Cloudflare D1 | 5 GB | Hard-fails at the limit since 2026-09 |

### 5.3 Delivery channels

- **Telegram bot:** free, and inline-keyboard buttons give a feedback loop. Messages are capped at 4,096 characters, so split them or link to the archive.
- **Email:** Resend (3k/mo free, 100/day), Postmark (100/mo free) or SES ($0.10 per 1k).
- **Archive:** a static site on GitHub Pages or Cloudflare Pages, plus a `feed.xml` RSS feed.
- **Slack / Discord webhooks:** free, but they can't take button clicks without a full app.
- **Audio** (prices per 1M characters):
  - Google Cloud TTS Chirp 3 HD: $30, with 1M free per month. A daily 10-minute brief is about 300k characters a month, so it fits the free tier.
  - Gemini 2.5 Flash TTS: ~$0.015/min, and supports **two-speaker dialogue**, the closest DIY substitute for a NotebookLM audio overview.
  - OpenAI tts-1: $15.
  - ElevenLabs Flash: $40.
  - The NotebookLM Podcast API is deprecated.

---

## Sources

The full source URLs for every claim are in the research agents' reports (archived in [`research-sources.md`](research-sources.md)).
