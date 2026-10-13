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
    # Spending guard. Model calls are off unless AINEWS_LLM_ENABLED=true (the "button").
    llm_enabled: bool = False
    max_usd_per_run: float = 0.75
    max_usd_per_day: float = 1.50
    max_usd_per_month: float = 20.0
    max_judge_stories: int = 120

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
            model_judge=env("MODEL_JUDGE", "claude-haiku-5-5"),
            model_editor=env("MODEL_EDITOR", "claude-opus-5-5"),
            telegram_bot_token=env("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=env("TELEGRAM_CHAT_ID", ""),
            hf_token=env("HF_TOKEN", ""),
            db_path=path("AINEWS_DB", "data/ainews.sqlite"),
            profile_path=path("AINEWS_PROFILE", "private/profile.md"),
            out_dir=path("AINEWS_OUT", "out"),
            sources_path=path("AINEWS_SOURCES", "config/sources.yaml"),
            prompts_dir=ROOT / "prompts",
            llm_enabled=env("AINEWS_LLM_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"},
            max_usd_per_run=float(env("AINEWS_MAX_USD_PER_RUN", "0.75")),
            max_usd_per_day=float(env("AINEWS_MAX_USD_PER_DAY", "1.50")),
            max_usd_per_month=float(env("AINEWS_MAX_USD_PER_MONTH", "20")),
            max_judge_stories=int(env("AINEWS_MAX_JUDGE_STORIES", "120")),
        )

    def profile_text(self) -> str:
        if self.profile_path.exists():
            return self.profile_path.read_text(encoding="utf-8")
        return (ROOT / "config" / "profile.example.md").read_text(encoding="utf-8")
