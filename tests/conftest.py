from dataclasses import replace
from pathlib import Path

import pytest

from ainews import db
from ainews.config import ROOT, Settings

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


@pytest.fixture
def settings(tmp_path):
    base = Settings.from_env()
    return replace(base, gcp_project_id="test-project", telegram_bot_token="", telegram_chat_id="",
                   db_path=tmp_path / "t.sqlite", profile_path=ROOT / "config" / "profile.example.md",
                   out_dir=tmp_path / "out")


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")
