# AI_news

A personal daily brief for ML / GenAI / LLM engineers and Forward Deployed Engineers: AI news, model and tool releases, research, community pulse, trends, and what new job postings require. Collected from many sources, deduplicated, ranked against a personal profile, and summarized with Gemini (Claude optional).

## Docs

- [Research](docs/research.md) — landscape of existing products, data sources, job-market data, pipeline building blocks, deployment options ([sources](docs/research-sources.md))
- [Architecture proposal](docs/architecture.md) — system design, deployment, cost estimate, roadmap ([diagram](docs/architecture-diagram.html))
- [Source lists and retrieval methods](docs/sources/methods.html): one page per source block; data in `docs/sources/sources.json`, rebuilt with `python3 scripts/build_source_pages.py`

## Status

**Phase 1 (MVP), running locally; not deployed yet.**
- 47 sources across six layers, defined in [config/sources.yaml](config/sources.yaml), from business and policy down to kernels.
- Collectors: RSS/Atom, GitHub releases, a CHANGELOG diff, HF Daily Papers, Hacker News, and an OpenRouter model and price diff.
- Dedup by canonical URL and MinHash, then a heat score.
- Gemini 3.1 Flash-Lite judges stories against your profile and Gemini 3.5 Flash-Lite writes the brief, through Vertex AI (Claude is an optional provider). The brief teaches: up to 15 stories, each in its own Telegram message with a detailed "What happened" (from the article text, not just the headline), a "What is it?" explanation and a "Comment from a pro" from a senior engineer guiding your career, and a Trends & future of work section covers where AI is heading and how it changes tech work and jobs. Numbers the sources don't support are dropped before anything is published.
- Output goes to a local Markdown file and, optionally, the Telegram channel.

Next is Phase 2: embedding dedup, aggregator link votes, feedback buttons, citations, and deployment to Cloud Run.

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
```

Without any credentials, `ainews run --no-llm` builds an unranked digest by heat, with no model calls and no cost. Other commands: `ainews collect`, `ainews brief`, `ainews health` (source health report), `ainews sources`, `ainews budget` (switch state, spend and caps), `ainews tg-chats` and `ainews tg-test` (Telegram setup), `ainews tg-clean` (delete the channel's earlier posts, keeping the latest sent brief). Tests: `.venv/bin/pytest`.

Runtime state lives in `data/ainews.sqlite`, and briefs are written to `out/`. Both folders are gitignored.
