# Project conventions

- Docs live in `docs/`.
- Architecture decisions are rows in `docs/decisions.html` (decision, version, why); the architecture diagram links to it.
- Git identity for this repo is the personal account: `ovsilya` <ovsyannikovilyavl@gmail.com>.
- Push to GitHub (`ovsilya/AI_news`) with the personal `ovsilya` account — gh's default active account is the work account `ilya-bitstrapped`; don't use it here.

# Development

- Python 3.13 virtualenv in `.venv` (`pip install -e ".[dev]"`); tests: `.venv/bin/pytest`.
- Models go through Vertex AI on the personal GCP project with Application Default Credentials; no API keys. Default provider: Gemini (`google-genai`, `genai.Client(enterprise=True, ...)`); Claude (`AnthropicVertex`) is optional via `LLM_PROVIDER=anthropic`.
- Personal data (profile, skills, gap analyses) stays in `private/` (gitignored). Runtime state (`data/`) and briefs (`out/`) are gitignored too.
- `docs/sources/*.html` is generated: edit `docs/sources/sources.json`, then run `python3 scripts/build_source_pages.py`.
- `docs/jobs/*.html` is generated: edit `docs/jobs/src/*.html` (or `scripts/build_job_pages.py` for the shared layout and the tables built from code and config), then run `.venv/bin/python scripts/build_job_pages.py`.
