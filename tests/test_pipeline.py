import json
from datetime import datetime, timezone

import httpx
import pytest

from ainews.collectors import RawItem
from ainews.deliver.render import TELEGRAM_LIMIT, to_telegram
from ainews.deliver.telegram import Telegram, TelegramError
from ainews.http import Fetcher
from ainews.pipeline.brief import build_brief, drop_latest_brief
from ainews.pipeline.ingest import store_items
from ainews.registry import Source

NOW = datetime.now(timezone.utc)
SOURCES = [
    Source(id="blog", name="Vendor blog", kind="rss", layer="models", weight=1.4),
    Source(id="hn", name="Hacker News", kind="hn", layer="community", weight=0.9),
    Source(id="news", name="Tech news", kind="rss", layer="big_picture", weight=1.0),
]
SRC = {s.id: s for s in SOURCES}


def seed(conn):
    store_items(conn, SRC["blog"], [RawItem(url="https://vendor.ai/blog/model-x?utm_source=rss",
                                            title="Vendor releases Model X with 1M context",
                                            summary="Model X costs $3 per million input tokens.")])
    store_items(conn, SRC["hn"], [RawItem(url="https://vendor.ai/blog/model-x", title="Model X is out",
                                          discussion_url="https://news.ycombinator.com/item?id=9",
                                          engagement={"points": 400, "comments": 120})])
    store_items(conn, SRC["news"], [RawItem(url="https://news.example/funding", title="Startup Y raises a big round",
                                            summary="Startup Y raised money from investors.")])
    conn.commit()


class FakeLLM:
    """Same interface as ainews.llm.LLM; returns canned judge and editor output."""

    def __init__(self):
        self.total_cost = 0.0
        self.calls = []

    def json(self, *, purpose, user, **kw):
        self.calls.append(purpose)
        stories = json.loads(user.split("\n", 1)[1])
        if purpose == "judge":
            return {"scores": [{"id": s["id"], "relevance": 3, "novelty": 3, "actionability": 2, "credibility": 3,
                                "section": "models" if "Model" in s["title"] else "big_picture",
                                "why_it_matters": "Matters."} for s in stories]}
        model_x = next(s["id"] for s in stories if "Model X" in s["title"])
        funding = next(s["id"] for s in stories if "Startup" in s["title"])
        return {"headline": "Model X lands.", "sections": [
            {"key": "top", "items": [{"story_ids": [model_x], "title": "Model X ships with 1M context",
                                      "summary": "It costs $3 per million input tokens. It is 7x faster.",
                                      "explainer": "Context is how much text a model can read at once.",
                                      "pro_take": "Long context can replace a RAG pipeline for small corpora. "
                                                  "It was released in 2024."}]},
            {"key": "big_picture", "items": [
                {"story_ids": [funding], "title": "Startup Y raises $500M", "summary": "Startup Y raised money.",
                 "explainer": "", "pro_take": ""},
                {"story_ids": [999], "title": "Invented story", "summary": "Not real.", "explainer": "",
                 "pro_take": ""}]},
        ]}


def test_same_url_from_two_sources_is_one_story(conn, settings):
    seed(conn)
    result = build_brief(conn, settings, SOURCES, llm=None, now_dt=NOW)
    assert result["stats"]["dedup"] == {"new_stories": 2, "by_url": 1, "by_text": 0}
    assert result["stats"]["candidates"] == 2


def test_brief_with_fake_llm_verifies_and_renders(conn, settings):
    seed(conn)
    llm = FakeLLM()
    result = build_brief(conn, settings, SOURCES, llm=llm, now_dt=NOW)
    assert llm.calls == ["judge", "editor"]
    md = result["markdown"]
    # Supported number kept, unsupported sentence dropped, invented title number replaced, unknown story dropped.
    assert "$3 per million input tokens" in md
    assert "7x faster" not in md
    assert "$500M" not in md and "Startup Y raises a big round" in md
    assert "Invented story" not in md
    # The teaching notes are rendered and number-checked like the summary.
    assert "What is it?" in md and "Long context can replace a RAG pipeline" in md
    assert "2024" not in md
    # Links come from the stored items, including the HN discussion.
    assert "https://vendor.ai/blog/model-x" in md and "news.ycombinator.com/item?id=9" in md
    assert len(result["warnings"]) == 4
    tg = "\n".join(result["telegram"])
    assert "<blockquote expandable>💡 <b>What is it?</b>" in tg and "🎓 <b>Comment from a pro</b>" in tg
    assert "<b>1. Model X ships with 1M context</b>" in tg and "<b>2. Startup Y raises a big round</b>" in tg
    # Shown stories are not repeated in the next brief.
    again = build_brief(conn, settings, SOURCES, llm=FakeLLM(), now_dt=NOW)
    assert again["stats"]["candidates"] == 0
    # --redo: dropping the latest (empty) and the real brief makes the stories available again,
    # and the judge reuses its scores instead of being called again.
    assert drop_latest_brief(conn) == again["brief_id"] and drop_latest_brief(conn) == result["brief_id"]
    redo_llm = FakeLLM()
    redo = build_brief(conn, settings, SOURCES, llm=redo_llm, now_dt=NOW)
    assert redo["stats"]["candidates"] == 2 and redo_llm.calls == ["editor"]


def test_telegram_messages_respect_the_limit():
    from ainews.pipeline.rank import Story

    stories = {i: Story(i, f"Story {i}", "tools", 1.0, "", items=[
        {"source_id": "blog", "url": f"https://e.com/{i}", "discussion_url": None}]) for i in range(60)}
    brief = {"headline": "h", "sections": [{"key": "tools", "items": [
        {"story_ids": [i], "title": f"Title {i}", "summary": "word " * 60, "explainer": "idea " * 30,
         "pro_take": "because"}
        for i in range(60)]}]}
    messages = to_telegram(brief, stories, {"blog": "Blog"}, "AI Brief", "footer")
    assert len(messages) > 1
    assert all(len(m) <= TELEGRAM_LIMIT for m in messages)
    assert sum(m.count(". Title") for m in messages) == 60
    # Tags are never split across messages.
    assert all(m.count("<blockquote") == m.count("</blockquote>") for m in messages)


def test_fetcher_conditional_get_and_retry(conn):
    calls = []

    def handler(request):
        calls.append(dict(request.headers))
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        if "if-none-match" in request.headers:
            return httpx.Response(304)
        return httpx.Response(200, text="<rss/>", headers={"ETag": '"v1"'})

    f = Fetcher(conn, client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda s: None)
    first = f.get("https://feeds.example/rss")
    assert first.status == 200 and first.text == "<rss/>"
    assert calls[0]["user-agent"].startswith("AI_news-bot/")  # the bot identifies itself on every request
    second = f.get("https://feeds.example/rss")
    assert second.not_modified


def test_telegram_send_and_error_hides_token():
    sent = []

    def handler(request):
        body = json.loads(request.content)
        if body.get("text") == "boom":
            return httpx.Response(400, json={"ok": False, "error_code": 400, "description": "Bad Request"})
        sent.append(body)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": len(sent)}})

    tg = Telegram("123:SECRET", "@channel", client=httpx.Client(transport=httpx.MockTransport(handler)),
                  sleep=lambda s: None)
    assert tg.send_messages(["<b>a</b>", "b"]) == [1, 2]
    assert sent[0]["parse_mode"] == "HTML" and sent[0]["chat_id"] == "@channel"
    with pytest.raises(TelegramError) as exc:
        tg.send_messages(["boom"])
    assert "SECRET" not in str(exc.value)


def test_reddit_429_waits_a_minute_and_retries_once(conn):
    waits, calls = [], []

    def handler(request):
        calls.append(request.url)
        return httpx.Response(429)

    f = Fetcher(conn, client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=waits.append)
    with pytest.raises(httpx.HTTPStatusError):
        f.get("https://www.reddit.com/r/MachineLearning/top/.rss?t=day")
    assert len(calls) == 2 and 60.0 in waits
