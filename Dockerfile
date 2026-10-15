# One image for every lane; each Cloud Run job passes its own command (see infra/deploy.sh).
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
# tzdata: the jobs run in your time zone (TZ), so brief and openings titles carry your date.
# opus-tools: opusenc encodes the brief read aloud as OGG/Opus, the format of Telegram voice messages.
RUN apt-get update && apt-get install -y --no-install-recommends tzdata opus-tools && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
COPY config ./config
COPY prompts ./prompts
# Editable, so the app finds config/ and prompts/ next to src/ as it does in the repo.
RUN pip install -e ".[cloud]" && useradd --create-home app && mkdir -p data out private && chown -R app /app
USER app

# The database and private files come from the state bucket at the start of each run (src/ainews/cloud.py).
ENTRYPOINT ["ainews"]
