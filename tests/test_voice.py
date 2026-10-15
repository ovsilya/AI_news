import json
import shutil
from argparse import Namespace
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest

from ainews import cli, db
from ainews.db import now
from ainews.deliver.render import OUTRO, to_speech
from ainews.deliver.telegram import Telegram
from ainews.deliver.voice import PAUSE, RATE, STYLE, Speaker, Speech, encode_ogg, parts
from ainews.llm import BudgetExceeded, spent_since
from ainews.pipeline.brief import drop_latest_brief

BRIEF = {"headline": "Two launches and a price cut.", "sections": [
    {"key": "top", "items": [
        {"title": "Model X 2 is out", "story_ids": [1], "summary": "It reads 2M tokens → a whole repo (https://x.ai/blog).",
         "explainer": {"intro": "A long-context model.", "points": ["How it works: sparse attention", "Cost: ~$2 per 1M"]},
         "pro_take": "Worth a try for code review."},
        {"title": "🔥 Flash gets cheaper", "story_ids": [2], "summary": "Input price halves.", "explainer": "",
         "pro_take": {"intro": "", "points": []}}]},
    {"key": "tools", "items": [{"title": "SDK 3.0", "story_ids": [3], "summary": "Breaking changes & a new client."}]}]}


def test_the_spoken_brief_has_every_story_and_note_but_no_links():
    script = to_speech(BRIEF, "AI Brief for Thursday, October 15")
    first, *stories, last = script.split("\n\n")
    assert first == "AI Brief for Thursday, October 15. Two launches and a price cut."
    assert stories[0].splitlines() == [
        "Top stories.", "Story 1. Model X 2 is out.", "It reads 2M tokens to a whole repo.",
        "What is it? A long-context model. How it works: sparse attention. Cost: about $2 per 1M.",
        "Comment from a pro. Worth a try for code review."]
    assert stories[1] == "Story 2. Flash gets cheaper.\nInput price halves."    # empty notes are left out
    assert stories[2].splitlines()[:2] == ["Tools and SDKs.", "Story 3. SDK 3.0."]
    assert "http" not in script and "🔥" not in script and last == OUTRO


def test_long_scripts_are_read_in_parts_between_paragraphs_then_sentences():
    script = "\n\n".join(["A" * 40, "B" * 40, ("C" * 30 + ". ") * 4 + "End."])
    got = parts(script, limit=90)
    two = "C" * 30 + ". " + "C" * 30 + "."
    assert got == ["A" * 40 + "\n\n" + "B" * 40, two, two + " End."]
    assert "".join(got).replace("\n\n", "").replace(" ", "") == script.replace("\n\n", "").replace(" ", "")


class FakeTTS:
    """Stands in for google.genai.Client: one second of audio per call, 25 audio tokens."""

    def __init__(self, finish="STOP", audio=True):
        self.models, self.calls, self.finish, self.audio = self, [], finish, audio

    def generate_content(self, *, model, contents, config):
        self.calls.append((model, contents, config))
        part = SimpleNamespace(inline_data=SimpleNamespace(data=b"\1\0" * RATE if self.audio else b""))
        return SimpleNamespace(usage_metadata=SimpleNamespace(prompt_token_count=100, candidates_token_count=25),
                               candidates=[SimpleNamespace(finish_reason=SimpleNamespace(value=self.finish),
                                                           content=SimpleNamespace(parts=[part]))])


def speaker(conn, settings, client, **caps):
    voiced = replace(settings, llm_enabled=True, **caps)
    return Speaker(voiced, conn, client=client, encode=lambda pcm: b"OggS" + pcm[:4], sleep=lambda s: None)


def test_the_brief_is_read_in_parts_joined_by_a_pause_and_billed_as_voice(conn, settings):
    tts = FakeTTS()
    speech = speaker(conn, settings, tts).read("\n\n".join(["Para one." * 300, "Para two." * 300]))   # two parts
    assert len(tts.calls) == 2 and tts.calls[0][0] == "gemini-2.5-flash-tts"
    assert tts.calls[0][1].startswith(STYLE + "\n\nPara one.")
    assert tts.calls[0][2].speech_config.voice_config.prebuilt_voice_config.voice_name == "Charon"
    assert speech.seconds == pytest.approx(2 + len(PAUSE) / (2 * RATE)) and speech.ogg.startswith(b"OggS")
    rows = conn.execute("SELECT purpose, cost_usd FROM llm_calls").fetchall()
    assert [r["purpose"] for r in rows] == ["voice", "voice"]
    assert speech.cost_usd == pytest.approx(2 * (100 * 0.50 + 25 * 10.00) / 1e6)
    since = "2000-01-01T00:00:00+00:00"
    assert spent_since(conn, since) == 0 and spent_since(conn, since, voice=True) == pytest.approx(speech.cost_usd)


def test_voice_has_caps_of_its_own(conn, settings):
    tts = FakeTTS()
    with pytest.raises(BudgetExceeded, match="AINEWS_VOICE_MAX_USD_PER_BRIEF"):   # 20k characters: worst case $0.56
        speaker(conn, settings, tts, voice_max_usd_per_brief=0.40).read("Word. " * 3_400)
    conn.execute("INSERT INTO llm_calls (at, purpose, model, input_tokens, output_tokens, cost_usd)"
                 " VALUES (?, 'voice', 'gemini-2.5-flash-tts', 0, 0, 5.80)", (now(),))
    with pytest.raises(BudgetExceeded, match="month spend \\$5.80"):
        speaker(conn, settings, tts, voice_max_usd_per_month=6.0).read("Word. " * 3_400)
    assert tts.calls == []


def test_a_part_without_audio_stops_the_voice_but_is_still_billed(conn, settings):
    with pytest.raises(RuntimeError, match="no usable audio"):
        speaker(conn, settings, FakeTTS(finish="OTHER", audio=False)).read("Hello.")
    assert conn.execute("SELECT purpose FROM llm_calls").fetchone()["purpose"] == "voice:other"


@pytest.mark.skipif(shutil.which("opusenc") is None, reason="opus-tools not installed")
def test_pcm_is_encoded_as_ogg_opus():
    ogg = encode_ogg(b"\0\0" * RATE)
    assert ogg.startswith(b"OggS") and b"OpusHead" in ogg[:100]


def test_send_voice_uploads_the_audio():
    seen = {}

    def handler(request):
        seen["type"], seen["body"] = request.headers["content-type"], request.content
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 70}})

    tg = Telegram("123:SECRET", "@channel", client=httpx.Client(transport=httpx.MockTransport(handler)),
                  sleep=lambda s: None)
    assert tg.send_voice(b"OggS...", caption="🎧 <b>AI Brief</b>", seconds=1561.4) == 70
    assert seen["type"].startswith("multipart/form-data")
    for part in (b'name="voice"; filename="brief.ogg"', b"OggS...", b'name="duration"\r\n\r\n1561',
                 b'name="disable_notification"\r\n\r\nfalse'):
        assert part in seen["body"]


def test_the_voice_goes_first_and_takes_the_notification(settings, monkeypatch):
    sent = []

    class FakeTelegram:
        def __init__(self, token, chat_id):
            pass

        def send_voice(self, ogg, *, caption, seconds, notify=True):
            sent.append(("voice", caption, notify))
            return 9

        def send_messages(self, messages, *, notify=True):
            sent.append(("text", len(messages), notify))
            return [10, 11]

    result = {"brief_id": 1, "title": "AI Brief · Thu 15 Oct 2026", "markdown": "", "telegram": ["a", "b"],
              "speech": "Hello.", "warnings": [], "stats": {}}
    monkeypatch.setattr("ainews.pipeline.brief.build_brief", lambda *a, **kw: result)
    monkeypatch.setattr("ainews.pipeline.brief.mark_sent", lambda conn, bid, ids: sent.append(("marked", ids)))
    monkeypatch.setattr("ainews.deliver.telegram.Telegram", FakeTelegram)
    monkeypatch.setattr(cli, "_read_aloud", lambda *a: Speech(b"OggS", 125.0, 0.05))
    monkeypatch.setattr("ainews.llm.make_llm", lambda *a, **kw: None)
    cli.cmd_brief(Namespace(redo=False, no_llm=False, send=True, edition="am", no_voice=False), settings)
    assert sent == [("voice", "🎧 <b>AI Brief · Thu 15 Oct 2026</b> · 2 min", True), ("text", 2, False),
                    ("marked", [9, 10, 11])]


def test_a_redo_remembers_the_replaced_brief_for_tg_clean(settings, monkeypatch):
    conn = db.connect(settings.db_path)
    conn.execute("INSERT INTO reports (created_at, kind, stats, markdown, telegram_html, telegram_message_ids)"
                 " VALUES ('t', 'jobs_openings', '{}', '', '[]', '[60]')")
    insert = ("INSERT INTO briefs (created_at, edition, story_ids, markdown, telegram_html, telegram_message_ids)"
              " VALUES ('t', 'am', '[]', '', '[]', ?)")
    conn.execute(insert, ("[62, 63]",))   # this morning's brief
    drop_latest_brief(conn)               # redone ...
    conn.execute(insert, ("[70, 71]",))   # ... and sent again
    conn.commit()
    asked = []

    class FakeDeleter:
        def __init__(self, token, chat_id):
            pass

        def delete_messages(self, ids):
            asked.extend(ids)
            return {"deleted": ids, "missing": [], "refused": []}

    monkeypatch.setattr("ainews.deliver.telegram.Telegram", FakeDeleter)
    cli.cmd_tg_clean(Namespace(yes=True), settings)
    assert asked == list(range(1, 60)) + [62, 63]
    assert json.loads(conn.execute("SELECT telegram_message_ids FROM briefs").fetchone()[0]) == [70, 71]
