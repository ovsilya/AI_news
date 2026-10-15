import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from ainews.collectors import RawItem
from ainews.deliver.render import TELEGRAM_LIMIT, to_telegram, visible_len
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
        if purpose == "narrator":
            return {"paragraphs": ["Good morning, here is your overview.", "Number 1. Model X ships with 1M context."]}
        stories = json.loads(user.split("\n", 1)[1])
        if purpose == "judge":
            return {"scores": [{"id": s["id"], "relevance": 3, "novelty": 3, "actionability": 2, "credibility": 3,
                                "section": "models" if "Model" in s["title"] else "big_picture",
                                "why_it_matters": "Matters."} for s in stories]}
        model_x = next(s["id"] for s in stories if "Model X" in s["title"])
        funding = next(s["id"] for s in stories if "Startup" in s["title"])
        return {"headline": "Model X lands.", "sections": [
            {"key": "top", "items": [{"story_ids": [model_x], "title": "Model X ships with 1M context",
                                      "summary": "It costs $3 per million input tokens.\n\nIt is 7x faster.",
                                      "explainer": {"intro": "Context is how much text a model can read at once.",
                                                    "points": ["How it works: the model reads it all at once.",
                                                               "Example: a 2023 contract."]},
                                      "pro_take": {"intro": "Long context can replace a RAG pipeline for small corpora.",
                                                   "points": ["Try: load a whole manual. It was released in 2024."]}}]},
            {"key": "big_picture", "items": [
                {"story_ids": [funding], "title": "Startup Y raises $500M", "summary": "Startup Y raised money.",
                 "explainer": {"intro": "", "points": []}, "pro_take": {"intro": "", "points": []}},
                {"story_ids": [999], "title": "Invented story", "summary": "Not real.",
                 "explainer": {"intro": "", "points": []}, "pro_take": {"intro": "", "points": []}}]},
        ]}


def test_same_url_from_two_sources_is_one_story(conn, settings):
    seed(conn)
    result = build_brief(conn, settings, SOURCES, llm=None, now_dt=NOW)
    assert result["stats"]["dedup"] == {"new_stories": 2, "by_url": 1, "by_text": 0}
    assert result["stats"]["candidates"] == 2


def _post(slug, days_ago):
    return RawItem(url=f"https://vendor.ai/blog/{slug}", title=f"Post {slug}",
                   published_at=(NOW - timedelta(days=days_ago)).isoformat())


def test_old_posts_a_feed_redates_stay_out(conn):
    assert store_items(conn, SRC["blog"], [_post("old", 300), _post("fresh", 1)]) == 1
    # The feed edits the old post and gives it today's date: it was listed before, so it isn't news.
    assert store_items(conn, SRC["blog"], [_post("old", 0), _post("fresh", 0)]) == 0
    assert conn.execute("SELECT count(*) FROM feed_seen").fetchone()[0] == 1


def _collect(conn, settings, monkeypatch, sources, listed):
    from ainews.pipeline import ingest

    monkeypatch.setattr(ingest, "registry", lambda: {"rss": lambda src, *a: listed[src.id]})
    return ingest.collect_all(conn, sources, None, settings)


def test_a_new_source_starts_with_a_baseline(conn, settings, monkeypatch):
    lc = Source(id="lc", name="LangChain blog", kind="rss", layer="tools")
    listed = {"blog": [_post("a", 1)], "lc": [_post("lc-1", 0), _post("lc-2", 2)]}
    first = _collect(conn, settings, monkeypatch, [SRC["blog"]], listed)       # a fresh install reports the last week
    assert first["new_items"] == 1 and first["baseline"] == []

    added = _collect(conn, settings, monkeypatch, [SRC["blog"], lc], listed)   # a source added later
    assert added["per_source"]["lc"] == 0 and added["baseline"] == ["lc"]
    listed["lc"] = [_post("lc-3", 0)] + listed["lc"]
    after = _collect(conn, settings, monkeypatch, [SRC["blog"], lc], listed)
    assert after["per_source"]["lc"] == 1 and after["baseline"] == []


def test_baseline_waits_for_a_successful_fetch_and_spares_quiet_sources(conn, settings, monkeypatch):
    from ainews.pipeline.ingest import needs_baseline

    conn.execute("INSERT INTO source_health (source_id, last_status) VALUES ('broke', 'error: 404'), ('quiet', 'ok')")
    assert needs_baseline(conn, "broke")        # its first fetch failed, so the baseline is still to come
    assert not needs_baseline(conn, "quiet")    # fetched fine before, just nothing new in a while
    assert needs_baseline(conn, "brand-new")


def test_brief_with_fake_llm_verifies_and_renders(conn, settings):
    seed(conn)
    llm = FakeLLM()
    result = build_brief(conn, settings, SOURCES, llm=llm, now_dt=NOW)
    assert llm.calls == ["judge", "editor", "narrator"]
    assert result["speech"].startswith("Good morning") and result["stats"]["speech"]["source"] == "narrator-v2"
    md = result["markdown"]
    # Supported number kept, unsupported sentence dropped, invented title number replaced, unknown story dropped.
    assert "$3 per million input tokens" in md
    assert "7x faster" not in md
    assert "$500M" not in md and "Startup Y raises a big round" in md
    assert "Invented story" not in md
    # The teaching notes are rendered as intro + labelled bullets, and number-checked like the summary:
    # a point with an unsupported number is dropped, a point's bad sentence is dropped.
    assert "What is it?" in md and "Long context can replace a RAG pipeline" in md
    assert "> - **How it works:** the model reads it all at once." in md
    assert "2023" not in md and "2024" not in md and "**Try:** load a whole manual." in md
    # Links come from the stored items, including the HN discussion.
    assert "https://vendor.ai/blog/model-x" in md and "news.ycombinator.com/item?id=9" in md
    assert len(result["warnings"]) == 5
    # Telegram: as few messages as possible; this small brief fits in one, contents first.
    tg = result["telegram"]
    assert len(tg) == 1
    m = tg[0]
    assert m.index("\n1. Model X ships with 1M context") < m.index("<b>1. Model X ships with 1M context</b>")
    assert "<b>2. Startup Y raises a big round</b>" in m and "footer" not in m and "run cost" in m
    assert "<blockquote>💡 <b>What is it?</b>" in m and "🎓 <b>Comment from a pro</b>" in m
    assert "• <b>How it works:</b>" in m and "📰 <b>What happened</b>" in m
    # Each item says when it appeared (no feed date here, so when we first saw it) and how much the editor read.
    assert m.count("🗓 first seen") == 2 and m.count("📌 Headline only") == 2
    assert result["stats"]["coverage"] == {"headline": 2}
    # Shown stories are not repeated in the next brief.
    again = build_brief(conn, settings, SOURCES, llm=FakeLLM(), now_dt=NOW)
    assert again["stats"]["candidates"] == 0
    # --redo: dropping the latest (empty) and the real brief makes the stories available again,
    # and the judge reuses its scores instead of being called again.
    assert drop_latest_brief(conn) == again["brief_id"] and drop_latest_brief(conn) == result["brief_id"]
    redo_llm = FakeLLM()
    redo = build_brief(conn, settings, SOURCES, llm=redo_llm, now_dt=NOW)
    assert redo["stats"]["candidates"] == 2 and redo_llm.calls == ["editor", "narrator"]


def _brief(n_items, pro_words):
    from ainews.pipeline.rank import Story

    stories = {i: Story(i, f"Story {i}", "tools", 1.0, "", items=[
        {"source_id": "blog", "url": f"https://e.com/{i}", "discussion_url": None, "title": f"Story {i}",
         "summary": "", "published_at": "2026-10-12T00:00:00+00:00"}]) for i in range(n_items)}
    brief = {"headline": "h", "sections": [{"key": "tools", "items": [
        {"story_ids": [i], "title": f"Title {i}", "summary": "word " * 120,
         "explainer": {"intro": "idea " * 40, "points": ["How it works: " + "step " * 60] * 3},
         "pro_take": {"intro": "because", "points": ["Learn: " + "this " * pro_words]}}
        for i in range(n_items)]}]}
    return brief, stories


def test_telegram_packs_whole_items_into_few_messages():
    brief, stories = _brief(10, 20)
    messages = to_telegram(brief, stories, {"blog": "Blog"}, "AI Brief", "footer")
    assert all(visible_len(m) <= TELEGRAM_LIMIT for m in messages)
    assert len(messages) <= 6       # was 11: a contents message plus one per item
    for i in range(10):             # every item whole: its title and its closing links in the same message
        holder = [m for m in messages if f"<b>{i + 1}. Title {i}</b>" in m]
        assert len(holder) == 1 and f'href="https://e.com/{i}"' in holder[0]
    assert "footer" in messages[-1] and messages[0].startswith("📰 <b>AI Brief</b>")
    # A date-only feed time (midnight UTC) keeps its calendar day in any time zone.
    assert "🗓 Mon 12 Oct" in messages[1] or "🗓 Mon 12 Oct" in messages[0]


def test_telegram_messages_respect_the_limit():
    brief, stories = _brief(12, 900)   # pro_take far over the limit
    messages = to_telegram(brief, stories, {"blog": "Blog"}, "AI Brief", "footer")
    assert all(visible_len(m) <= TELEGRAM_LIMIT for m in messages)
    assert sum(f"<b>{i + 1}. Title {i}</b>" in m for m in messages for i in range(12)) == 12
    assert "footer" in messages[-1] and "Title 3, continued" in "".join(messages)
    # Tags are never split across messages.
    assert all(m.count("<blockquote") == m.count("</blockquote>") for m in messages)


def test_coverage_flag():
    from ainews.pipeline.fulltext import coverage
    from ainews.pipeline.rank import Story

    def story(url="https://e.com/a", title="Kroger plans to cut 3,000 roles - Reuters", summary="", full_text=None):
        it = {"source_id": "x", "url": url, "title": title, "summary": summary}
        if full_text:
            it["full_text"] = full_text
        return Story(1, title, "trends", 1.0, "", items=[it])

    assert coverage(story(summary="Kroger plans to cut 3,000 roles Reuters")) == "headline"   # Google News
    assert coverage(story(summary="The grocer said on Monday that it will move work to AI tools. " * 3)) == "excerpt"
    assert coverage(story(full_text="Body text. " * 100)) == "article"
    assert coverage(story(full_text="A paywall teaser.")) == "headline"
    assert coverage(story(url="https://arxiv.org/abs/2610.01234", summary="We propose a method. " * 40)) == "abstract"


def test_telegram_delete_reports_what_was_removed():
    def handler(request):
        mid = json.loads(request.content)["message_id"]
        if mid == 1:
            return httpx.Response(400, json={"ok": False, "error_code": 400,
                                             "description": "Bad Request: message can't be deleted"})
        if mid == 2:
            return httpx.Response(400, json={"ok": False, "error_code": 400,
                                             "description": "Bad Request: message to delete not found"})
        return httpx.Response(200, json={"ok": True, "result": True})

    tg = Telegram("123:SECRET", "@channel", client=httpx.Client(transport=httpx.MockTransport(handler)),
                  sleep=lambda s: None)
    assert tg.delete_messages([1, 2, 3, 4]) == {"deleted": [3, 4], "missing": [2], "refused": [1]}


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


def test_one_story_per_source():
    from ainews.pipeline.rank import Story, select

    def story(i, source, section):
        s = Story(i, f"S{i}", section, 1.0, "", items=[{"source_id": source}])
        s.score = {"relevance": 3, "novelty": 3, "actionability": 3, "credibility": 3, "section": section}
        return s

    stories = [story(i, "google-dev", "tools") for i in range(5)] + [story(10, "vllm", "systems"),
                                                                      story(11, "hn", "community")]
    chosen = select(stories)
    assert [s.lead_source for s in chosen].count("google-dev") == 1
    assert {10, 11} <= {s.id for s in chosen}


def test_every_source_gets_a_candidate_slot():
    from ainews.pipeline.rank import Story, _diverse

    heavy = [Story(i, f"B{i}", "big_picture", 10.0 - i * 0.01, "", items=[{"source_id": "bloomberg"}])
             for i in range(50)]
    light = Story(99, "AI and entry-level hiring", "big_picture", 0.1, "", items=[{"source_id": "ai-jobs"}])
    picked = _diverse(heavy + [light], limit=12)
    assert 99 in {s.id for s in picked} and len(picked) == 12


def test_trend_stories_are_not_sunk_by_actionability():
    from ainews.pipeline.rank import Story, final_score

    trend = Story(1, "t", "trends", 1.0, "")
    trend.score = {"relevance": 3, "novelty": 2, "actionability": 0, "credibility": 3, "section": "trends"}
    tool = Story(2, "t", "tools", 1.0, "")
    tool.score = {**trend.score, "section": "tools"}
    assert final_score(trend, 1.0) > final_score(tool, 1.0)


def test_multi_outlet_proxy_may_lead_more_stories():
    from ainews.pipeline.rank import Story, select

    def story(i, source):
        s = Story(i, f"S{i}", "trends", 1.0, "", items=[{"source_id": source}])
        s.score = {"relevance": 3, "novelty": 3, "actionability": 0, "credibility": 3, "section": "trends"}
        return s

    chosen = select([story(i, "ai-jobs-news") for i in range(6)], per_source={"ai-jobs-news": 3})
    assert len(chosen) == 3


def test_article_text_for_thin_stories(conn):
    from ainews.pipeline.fulltext import enrich
    from ainews.pipeline.rank import Story

    article = "<html><body><article>" + "".join(
        f"<p>Paragraph {i} explains how the new serving system reuses cached work across turns.</p>"
        for i in range(12)) + "</article></body></html>"

    def handler(request):
        if request.url.path == "/robots.txt":
            body = "User-agent: *\nDisallow: /private/\n" if request.url.host == "blog.example" else ""
            return httpx.Response(200 if body else 404, text=body)
        return httpx.Response(200, text=article)

    f = Fetcher(conn, client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda s: None)
    thin = Story(1, "Thin", "systems", 1.0, "", items=[{"source_id": "b", "url": "https://blog.example/post", "summary": "x"}])
    blocked = Story(2, "Blocked", "systems", 1.0, "", items=[{"source_id": "b", "url": "https://blog.example/private/p"}])
    proxy = Story(3, "Proxy", "trends", 1.0, "", items=[{"source_id": "g", "url": "https://news.google.com/rss/articles/x"}])
    stats = enrich(conn, f, [thin, blocked, proxy], lambda s: len(s.items[0].get("summary") or ""))
    assert stats == {"thin": 3, "enriched": 1}
    assert "reuses cached work" in thin.items[0]["full_text"]
    assert "full_text" not in blocked.items[0] and "full_text" not in proxy.items[0]
    statuses = dict(conn.execute("SELECT url, status FROM article_text").fetchall())
    assert statuses == {"https://blog.example/post": "ok", "https://blog.example/private/p": "robots"}


def test_brief_is_capped_at_15_items():
    from ainews.pipeline.rank import Story
    from ainews.pipeline.verify import verify

    stories = [Story(i, f"S{i}", "tools", 1.0, "", items=[{"source_id": "b", "url": "u", "title": f"S{i}"}])
               for i in range(20)]
    draft = {"headline": "h", "sections": [{"key": "tools", "items": [
        {"story_ids": [i], "title": f"T{i}", "summary": "s", "explainer": {"intro": "", "points": []},
         "pro_take": {"intro": "", "points": []}} for i in range(20)]}]}
    brief, warnings = verify(draft, stories)
    assert sum(len(s["items"]) for s in brief["sections"]) == 15 and len(warnings) == 5
