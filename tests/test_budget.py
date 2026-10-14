import time
from dataclasses import replace
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from ainews.db import now
from ainews.llm import (UNKNOWN_PRICE, BudgetExceeded, ClaudeLLM, GeminiLLM, LLMDisabled, LLMError, make_llm,
                        price_for, worst_case_usd)

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"], "additionalProperties": False}


class FakeGemini:
    """Stands in for google.genai.Client; counts calls and reports fixed usage."""

    def __init__(self, finish="STOP"):
        self.calls, self.finish, self.config = 0, finish, None
        self.models = self

    def generate_content(self, *, model, contents, config):
        self.calls += 1
        self.config = config
        usage = SimpleNamespace(prompt_token_count=1200, cached_content_token_count=200,
                                candidates_token_count=300, thoughts_token_count=200)
        return SimpleNamespace(usage_metadata=usage, text='{"ok": true}',
                               candidates=[SimpleNamespace(finish_reason=SimpleNamespace(value=self.finish))])


class FakeClaude:
    def __init__(self):
        self.messages = self

    def create(self, **params):
        usage = SimpleNamespace(input_tokens=1000, output_tokens=500, cache_read_input_tokens=0,
                                cache_creation_input_tokens=0)
        return SimpleNamespace(usage=usage, stop_reason="end_turn", content=[SimpleNamespace(type="text", text='{"ok": true}')])


def call(llm, max_tokens=1000, model="gemini-3.1-flash-lite", effort="low"):
    return llm.json(model=model, system="s", user="u", schema=SCHEMA, max_tokens=max_tokens, effort=effort, purpose="t")


def test_switch_off_blocks_all_calls(conn, settings):
    with pytest.raises(LLMDisabled):
        make_llm(replace(settings, llm_enabled=False), conn, client=FakeGemini())


def test_placeholder_project_is_rejected(conn, settings):
    with pytest.raises(LLMError, match="placeholder"):
        make_llm(replace(settings, llm_enabled=True, gcp_project_id="your-personal-project-id"), conn)


def test_check_can_bypass_switch(conn, settings):
    llm = make_llm(replace(settings, llm_enabled=False), conn, client=FakeGemini(), ignore_switch=True)
    assert isinstance(llm, GeminiLLM)
    assert call(llm) == {"ok": True}


def test_gemini_request_and_cost(conn, settings):
    client = FakeGemini()
    llm = make_llm(replace(settings, llm_enabled=True), conn, client=client)
    call(llm, effort="medium")
    cfg = client.config
    assert cfg.response_mime_type == "application/json" and cfg.response_json_schema == SCHEMA
    assert cfg.max_output_tokens == 1000 and cfg.thinking_config.thinking_level.value == "MEDIUM"
    row = conn.execute("SELECT * FROM llm_calls").fetchone()
    # Cached prompt tokens are billed at 10%; thinking tokens count as output.
    assert (row["input_tokens"], row["cache_read_tokens"], row["output_tokens"]) == (1000, 200, 500)
    assert row["cost_usd"] == pytest.approx((1000 * 0.25 + 200 * 0.025 + 500 * 1.50) / 1e6)


def test_truncated_output_is_logged_and_raises(conn, settings):
    llm = make_llm(replace(settings, llm_enabled=True), conn, client=FakeGemini(finish="MAX_TOKENS"))
    with pytest.raises(LLMError, match="max_tokens"):
        call(llm)
    assert conn.execute("SELECT purpose FROM llm_calls").fetchone()["purpose"] == "t:max_tokens"


def test_claude_provider_still_works(conn, settings):
    llm = make_llm(replace(settings, llm_enabled=True, llm_provider="anthropic"), conn, client=FakeClaude())
    assert isinstance(llm, ClaudeLLM)
    assert call(llm, model="claude-haiku-5-5") == {"ok": True}


def test_prices():
    assert price_for("gemini-3.5-flash-lite") == (0.30, 2.50)          # not matched as gemini-3.5-flash
    assert price_for("gemini-3.1-pro-preview") == (2.00, 12.00)
    assert price_for("gemini-3.8-flash", on=date(2026, 12, 31)) == (0.75, 3.75)
    assert price_for("gemini-3.8-flash", on=date(2027, 1, 1)) == (1.50, 7.50)   # intro price ends
    assert price_for("some-new-model") == UNKNOWN_PRICE


def test_run_cap_refuses_before_calling(conn, settings):
    client = FakeGemini()
    llm = make_llm(replace(settings, llm_enabled=True, max_usd_per_run=0.01), conn, client=client)
    # The editor's worst case (16k output tokens on 3.8 Flash) is > $0.06: refused without contacting Vertex.
    with pytest.raises(BudgetExceeded, match="run"):
        call(llm, max_tokens=16000, model="gemini-3.8-flash")
    assert client.calls == 0


def test_daily_cap_counts_earlier_runs(conn, settings):
    conn.execute("INSERT INTO llm_calls (at, purpose, model, input_tokens, output_tokens, cost_usd)"
                 " VALUES (?, 'editor', 'gemini-3.8-flash', 0, 0, 0.74)", (now(),))
    llm = make_llm(replace(settings, llm_enabled=True, max_usd_per_day=0.75), conn, client=FakeGemini())
    with pytest.raises(BudgetExceeded, match="day"):
        call(llm, max_tokens=16000, model="gemini-3.8-flash")
    call(llm, max_tokens=1000)  # a cheap Flash-Lite call still fits under the cap


@pytest.fixture
def local_tz(monkeypatch):
    def use(name):
        monkeypatch.setenv("TZ", name)
        time.tzset()
    yield use
    monkeypatch.undo()
    time.tzset()


def test_the_day_starts_at_local_midnight(local_tz):
    from ainews.llm import period_start

    local_tz("America/Los_Angeles")
    brief_time = datetime(2026, 10, 14, 13, 0, tzinfo=timezone.utc).timestamp()   # 06:00 local
    assert period_start(now_ts=brief_time) == "2026-10-14T07:00:00+00:00"        # not 00:00 UTC (17:00 the day before)
    assert period_start(month=True, now_ts=brief_time) == "2026-10-01T07:00:00+00:00"
    after_dst = datetime(2026, 11, 2, 18, 0, tzinfo=timezone.utc).timestamp()     # clocks went back on Nov 1
    assert period_start(now_ts=after_dst) == "2026-11-02T08:00:00+00:00"
    assert period_start(month=True, now_ts=after_dst) == "2026-11-01T07:00:00+00:00"


def test_jobs_lane_leaves_a_reserve_for_the_brief(conn, settings):
    conn.execute("INSERT INTO llm_calls (at, purpose, model, input_tokens, output_tokens, cost_usd)"
                 " VALUES (?, 'jobs', 'gemini-3.1-flash-lite', 0, 0, 0.60)", (now(),))
    capped = replace(settings, llm_enabled=True, max_usd_per_day=0.75)
    jobs = make_llm(capped, conn, client=FakeGemini(), reserve=0.15)
    with pytest.raises(BudgetExceeded, match="keeping \\$0.15 for the brief"):
        call(jobs)
    call(make_llm(capped, conn, client=FakeGemini()))   # the brief itself may use the reserve


def test_worst_case_is_an_upper_bound():
    # 30k characters of prompt and 16k output tokens on Claude Opus.
    assert worst_case_usd("claude-opus-5-5", 30_000, 16_000) == pytest.approx((10_000 * 4 * 1.25 + 16_000 * 20) / 1e6)


def test_brief_falls_back_when_editor_is_over_budget(conn, settings):
    from test_pipeline import SOURCES, FakeLLM, seed

    from ainews.pipeline.brief import build_brief

    class Capped(FakeLLM):
        def json(self, *, purpose, **kw):
            if purpose == "editor":
                raise BudgetExceeded("editor: refused")
            return super().json(purpose=purpose, **kw)

    seed(conn)
    result = build_brief(conn, settings, SOURCES, llm=Capped())
    assert "Spending cap reached" in result["markdown"]
    assert result["stats"]["budget_stop"] == "editor: refused"
