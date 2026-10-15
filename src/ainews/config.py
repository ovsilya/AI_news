"""Settings from environment variables, with an optional .env file in the repo root."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
USER_AGENT = "AI_news-bot/0.1 (+https://github.com/ovsilya/AI_news)"


def load_dotenv(path: Path = ROOT / ".env") -> None:
    """Minimal .env reader: KEY=VALUE lines; real environment variables win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Settings:
    gcp_project_id: str
    vertex_region: str
    llm_provider: str
    model_judge: str
    model_editor: str
    telegram_bot_token: str
    telegram_chat_id: str
    hf_token: str
    db_path: Path
    profile_path: Path
    out_dir: Path
    sources_path: Path
    prompts_dir: Path
    companies_path: Path = ROOT / "config" / "companies.yaml"
    skills_path: Path = ROOT / "config" / "skills.yaml"
    fit_path: Path = ROOT / "private" / "skills.yaml"   # your skills, for the weekly fit report
    openings_path: Path = ROOT / "private" / "openings.yaml"   # which daily openings reach you
    # Spending guard. Model calls are off unless AINEWS_LLM_ENABLED=true (the "button").
    llm_enabled: bool = False
    max_usd_per_run: float = 0.30
    max_usd_per_day: float = 0.75
    max_usd_per_month: float = 10.0
    brief_reserve_usd: float = 0.15   # the jobs lane runs first and leaves this much of the day's cap for the brief
    max_judge_stories: int = 120
    max_jobs_extract: int = 200    # postings read by the model per jobs run
    brief_lookback_hours: int = 26  # how far back a brief may reach for stories: a day plus slack; more if briefs are rarer
    state_bucket: str = ""         # in the cloud: the bucket holding the database and private files (cloud.py)
    # A spoken overview of the brief, sent as one voice message (deliver/voice.py), with caps of its own.
    voice: str = "brief"           # "brief", or "off"
    model_voice: str = "gemini-2.5-flash-tts"
    voice_name: str = "Charon"     # one of Gemini TTS's prebuilt voices
    voice_minutes: int = 6         # length of the spoken overview the narrator writes
    voice_max_usd_per_brief: float = 0.30
    voice_max_usd_per_month: float = 3.0

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        env = os.environ.get

        def path(key: str, default: str) -> Path:
            p = Path(env(key) or default)
            return p if p.is_absolute() else ROOT / p

        return cls(
            gcp_project_id=env("GCP_PROJECT_ID", ""),
            vertex_region=env("VERTEX_REGION", "global"),
            llm_provider=env("LLM_PROVIDER", "gemini").strip().lower(),
            model_judge=env("MODEL_JUDGE", "gemini-3.1-flash-lite"),
            model_editor=env("MODEL_EDITOR", "gemini-3.5-flash-lite"),
            telegram_bot_token=env("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=env("TELEGRAM_CHAT_ID", ""),
            hf_token=env("HF_TOKEN", ""),
            db_path=path("AINEWS_DB", "data/ainews.sqlite"),
            profile_path=path("AINEWS_PROFILE", "private/profile.md"),
            out_dir=path("AINEWS_OUT", "out"),
            sources_path=path("AINEWS_SOURCES", "config/sources.yaml"),
            prompts_dir=ROOT / "prompts",
            companies_path=path("AINEWS_COMPANIES", "config/companies.yaml"),
            fit_path=path("AINEWS_FIT", "private/skills.yaml"),
            openings_path=path("AINEWS_OPENINGS", "private/openings.yaml"),
            llm_enabled=env("AINEWS_LLM_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"},
            max_usd_per_run=float(env("AINEWS_MAX_USD_PER_RUN", "0.30")),
            max_usd_per_day=float(env("AINEWS_MAX_USD_PER_DAY", "0.75")),
            max_usd_per_month=float(env("AINEWS_MAX_USD_PER_MONTH", "10")),
            brief_reserve_usd=float(env("AINEWS_BRIEF_RESERVE_USD", "0.15")),
            max_judge_stories=int(env("AINEWS_MAX_JUDGE_STORIES", "120")),
            max_jobs_extract=int(env("AINEWS_MAX_JOBS_EXTRACT", "200")),
            brief_lookback_hours=int(env("AINEWS_BRIEF_LOOKBACK_HOURS", "26")),
            state_bucket=env("AINEWS_STATE_BUCKET", ""),
            voice=env("AINEWS_VOICE", "brief").strip().lower(),
            model_voice=env("MODEL_VOICE", "gemini-2.5-flash-tts"),
            voice_name=env("AINEWS_VOICE_NAME", "Charon"),
            voice_minutes=int(env("AINEWS_VOICE_MINUTES", "6")),
            voice_max_usd_per_brief=float(env("AINEWS_VOICE_MAX_USD_PER_BRIEF", "0.30")),
            voice_max_usd_per_month=float(env("AINEWS_VOICE_MAX_USD_PER_MONTH", "3")),
        )

    def profile_text(self) -> str:
        if self.profile_path.exists():
            return self.profile_path.read_text(encoding="utf-8")
        return (ROOT / "config" / "profile.example.md").read_text(encoding="utf-8")
