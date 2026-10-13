from dataclasses import replace
from types import SimpleNamespace

import pytest

from ainews.db import now
from ainews.llm import LLM, BudgetExceeded, LLMDisabled, worst_case_usd

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"], "additionalProperties": False}


class FakeClient:
    """Stands in for AnthropicVertex; counts calls and reports fixed usage."""

    def __init__(self):
        self.calls = 0
        self.messages = self

    def create(self, **params):
        self.calls += 1
        usage = SimpleNamespace(input_tokens=1000, output_tokens=500, cache_read_input_tokens=0,
                                cache_creation_input_tokens=0)
        return SimpleNamespace(usage=usage, stop_reason="end_turn", content=[SimpleNamespace(type="text", text='{"ok": true}')])


def call(llm, max_tokens=1000, model="claude-haiku-5-5"):
    return llm.json(model=model, system="s", user="u", schema=SCHEMA, max_tokens=max_tokens, purpose="t")


def test_switch_off_blocks_all_calls(conn, settings):
    with pytest.raises(LLMDisabled):
        LLM(replace(settings, llm_enabled=False), conn, client=FakeClient())


def test_check_can_bypass_switch(conn, settings):
    llm = LLM(replace(settings, llm_enabled=False), conn, client=FakeClient(), ignore_switch=True)
    assert call(llm) == {"ok": True}


def test_calls_are_logged_with_cost(conn, settings):
    llm = LLM(replace(settings, llm_enabled=True), conn, client=FakeClient())
    call(llm)
    row = conn.execute("SELECT model, input_tokens, output_tokens, cost_usd FROM llm_calls").fetchone()
    assert (row["model"], row["input_tokens"], row["output_tokens"]) == ("claude-haiku-5-5", 1000, 500)
    assert row["cost_usd"] == pytest.approx((1000 * 0.10 + 500 * 0.50) / 1e6)


def test_run_cap_refuses_before_calling(conn, settings):
    client = FakeClient()
    llm = LLM(replace(settings, llm_enabled=True, max_usd_per_run=0.10), conn, client=client)
    # Opus with 16k output tokens has a worst case of > $0.32: refused without contacting Vertex.
    with pytest.raises(BudgetExceeded, match="run"):
        call(llm, max_tokens=16000, model="claude-opus-5-5")
    assert client.calls == 0


def test_daily_cap_counts_earlier_runs(conn, settings):
    conn.execute("INSERT INTO llm_calls (at, purpose, model, input_tokens, output_tokens, cost_usd)"
                 " VALUES (?, 'editor', 'claude-opus-5-5', 0, 0, 1.45)", (now(),))
    llm = LLM(replace(settings, llm_enabled=True, max_usd_per_day=1.50), conn, client=FakeClient())
    with pytest.raises(BudgetExceeded, match="day"):
        call(llm, max_tokens=16000, model="claude-opus-5-5")
    call(llm, max_tokens=1000)  # a cheap Haiku call still fits under the cap


def test_worst_case_is_an_upper_bound():
    # 30k characters of prompt and 16k output tokens on Opus.
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
