# AI_news

A personal daily brief for ML / GenAI / LLM engineers and Forward Deployed Engineers: AI news, model and tool releases, research, community pulse, trends, and what new job postings require. Collected from many sources, deduplicated, ranked against a personal profile, and summarized with Gemini (Claude optional).

## Docs

Browse the HTML docs online: **[ovsilya.github.io/AI_news](https://ovsilya.github.io/AI_news/architecture-diagram.html)** (GitHub Pages, served from `docs/` on `main`).

- [Research](docs/research.md) — landscape of existing products, data sources, job-market data, pipeline building blocks, deployment options ([sources](docs/research-sources.md))
- [Architecture proposal](docs/architecture.md) — system design, deployment, cost estimate, roadmap ([diagram](docs/architecture-diagram.html), [recent decisions](docs/decisions.html))
- [Source lists and retrieval methods](docs/sources/methods.html): one page per source block; data in `docs/sources/sources.json`, rebuilt with `python3 scripts/build_source_pages.py`
- [How the job search works](docs/jobs/index.html): the jobs lane step by step (sources, filters and tracking, extraction, the weekly report, running and changing it); written in `docs/jobs/src/`, rebuilt with `.venv/bin/python scripts/build_job_pages.py`

## Status

**Phase 1 (MVP) and the first part of Phase 3 (jobs), running locally; not deployed yet.**
- 54 news sources across seven layers, defined in [config/sources.yaml](config/sources.yaml), from business and policy down to kernels.
- Collectors: RSS/Atom, GitHub releases, a CHANGELOG diff, HF Daily Papers, Hacker News, and an OpenRouter model and price diff.
- Dedup by canonical URL and MinHash, then a heat score.
- Gemini 3.1 Flash-Lite judges stories against your profile and Gemini 3.5 Flash-Lite writes the brief, through Vertex AI (Claude is an optional provider). The brief teaches: up to 15 stories, packed into as few Telegram messages as fit, each with its date, a flag for how much of the article the editor read, a detailed "What happened" (from the article text, not just the headline), a "What is it?" explanation and a "Comment from a pro" from a senior engineer guiding your career, and a Trends & future of work section covers where AI is heading and how it changes tech work and jobs. Numbers the sources don't support are dropped before anything is published.
- Output goes to a local Markdown file and, optionally, the Telegram channel.
- **Jobs lane:** polls the public job boards (Greenhouse, Lever, Ashby) of 97 employers, mostly AI companies, and searches the Workday career sites of 32 banks, insurers, pharma and big-tech employers (NVIDIA, Salesforce, Capital One, TD, Barclays, Roche…) for AI roles; all 129 are listed in [config/companies.yaml](config/companies.yaml). It keeps postings in the target roles (FDE and applied AI, solutions, LLM/GenAI, AI and ML engineering) and markets (US, CA, UK, CH, UAE, remote), tracks new and closed ones, and has Gemini 3.1 Flash-Lite read each posting into skills, seniority, remote policy and pay. Every day, the new postings that fit your skills and level best go to Telegram, ranked in code and sent once each (`ainews openings`), with places shared between established companies, startups and top-tier names so the list isn't all frontier labs. A weekly jobs report shows the skills employers ask for, pay, who is hiring, and how your skills (`private/skills.yaml`) cover the demand.

Next: SerpApi Google Jobs and job-alert emails for the jobs lane (employers off the job-board systems, and better CH and UAE coverage); then Phase 2: embedding dedup, aggregator link votes, feedback buttons, citations, and deployment to Cloud Run.

## Quickstart

Full setup, covering the Telegram bot, the GCP project and spending guardrails: **[docs/setup.md](docs/setup.md)**. Model calls are off until you set `AINEWS_LLM_ENABLED=true`.

```bash
python3.13 -m venv .venv && .venv/bin/pip install -e ".[dev]"
cp .env.example .env                      # fill in GCP_PROJECT_ID and the Telegram values
cp config/profile.example.md private/profile.md   # your profile; private/ is gitignored
gcloud auth application-default login     # personal Google account; the app uses these credentials for Vertex AI
.venv/bin/ainews check                    # one tiny call per model (< $0.01) + Telegram getMe
# set AINEWS_LLM_ENABLED=true in .env once the guardrails are in place (docs/setup.md §3)
.venv/bin/ainews run                      # collect, judge, write → out/brief-<date>-am.md
.venv/bin/ainews run --send               # same, and post to the Telegram channel
.venv/bin/ainews send                     # post the latest brief (after reading it in out/)
.venv/bin/ainews run --redo               # rebuild the latest brief; judge scores are reused, so it's cheap
.venv/bin/ainews jobs                     # poll the employers' job boards, read up to 200 new postings (~$0.001 each)
.venv/bin/ainews weekly                   # the weekly jobs report → out/jobs-<date>.md (--send to post it)
```

Without any credentials, `ainews run --no-llm` builds an unranked digest by heat, with no model calls and no cost. Other commands: `ainews collect`, `ainews brief`, `ainews health` (source health report), `ainews sources`, `ainews budget` (switch state, spend and caps), `ainews tg-chats` and `ainews tg-test` (Telegram setup), `ainews tg-clean` (delete the channel's earlier posts, keeping the latest sent brief and jobs report), `ainews send --report` (post the latest jobs report), `ainews jobs-probe <slug>…` (find which job board an employer uses). Tests: `.venv/bin/pytest`.

Runtime state lives in `data/ainews.sqlite`, and briefs are written to `out/`. Both folders are gitignored.
