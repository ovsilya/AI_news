# Project conventions

- Docs live in `docs/`.
- Git identity for this repo is the personal account: `ovsilya` <ovsyannikovilyavl@gmail.com>.
- Push to GitHub (`ovsilya/AI_news`) with the personal `ovsilya` account — gh's default active account is the work account `ilya-bitstrapped`; don't use it here.

# Development

- Python 3.13 virtualenv in `.venv` (`pip install -e ".[dev]"`); tests: `.venv/bin/pytest`.
- Models go through Vertex AI on the personal GCP project (`AnthropicVertex`, Application Default Credentials). There is no Anthropic API key.
- Personal data (profile, skills, gap analyses) stays in `private/` (gitignored). Runtime state (`data/`) and briefs (`out/`) are gitignored too.
- `docs/sources/*.html` is generated: edit `docs/sources/sources.json`, then run `python3 scripts/build_source_pages.py`.
