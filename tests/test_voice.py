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
from ainews.config import ROOT
from ainews.pipeline.brief import drop_latest_brief
from ainews.pipeline.narrate import narrate

BRIEF = {"headline": "Two launches and a price cut.", "sections": [
    {"key": "top", "items": [
        {"title": "Model X 2 is out", "story_ids": [1], "summary": "It reads 2M tokens → a whole repo (https://x.ai/blog).",
         "explainer": {"intro": "A long-context model.", "points": ["How it works: sparse attention", "Cost: ~$2 per 1M"]},
         "pro_take": "Worth a try for code review."},
        {"title": "🔥 Flash gets cheaper", "story_ids": [2], "summary": "Input price halves.", "explainer": "",
         "pro_take": {"intro": "", "points": []}}]},
    {"key": "tools", "items": [{"title": "SDK 3.0", "story_ids": [3], "summary": "Breaking changes & a new client."}]}]}


def test_without_the_narrator_the_voice_reads_the_headlines():
    script = to_speech(BRIEF, "AI Brief for Thursday, October 15")
    first, *stories, last = script.split("\n\n")
    assert first == "AI Brief for Thursday, October 15. Two launches and a price cut."
    assert stories == ["Top stories.\nNumber 1. Model X 2 is out. It reads 2M tokens to a whole repo.",
                       "Number 2. Flash gets cheaper. Input price halves.",
                       "Tools and SDKs.\nNumber 3. SDK 3.0. Breaking changes and a new client."]
    assert "http" not in script and "🔥" not in script and last == OUTRO


class Narrator:
    def __init__(self, out):
        self.out, self.asked = out, {}

    def json(self, **kw):
        self.asked = kw
        return self.out


def test_the_narrator_explains_each_story_in_order_from_the_brief_only():
    llm = Narrator({"opening": "Good morning. It is Thursday, October 15, and two launches lead.",
                    "stories": [   # out of order, story 2 missing, story 3 too long
                        {"number": 3, "what_happened": "In tools, number 3. SDK 3.0 is out. It is big.",
                         "in_simple_words": "A new client.", "why_it_matters": "For you, an upgrade. Plan for it."},
                        {"number": 1, "what_happened": "Starting with the top stories, number 1. Model X 2 reads 2M tokens.",
                         "in_simple_words": "It is 40% faster.   Put simply, it reads a whole repo.",
                         "why_it_matters": "Since you've built RAG, you may need less of it."}],
                    "closing": "Read number 1 first."})
    script, version, warnings = narrate(llm, BRIEF, model="gemini-3.8-flash", prompts_dir=ROOT / "prompts",
                                        profile="An FDE.", spoken_date="Thursday, October 15", minutes=8)
    assert version == "narrator-v5" and llm.asked["purpose"] == "narrator" and llm.asked["model"] == "gemini-3.8-flash"
    assert "about 896 words in total" in llm.asked["user"] and "about 272 words per story" in llm.asked["user"]
    assert "An FDE." in llm.asked["system"]
    sent = json.loads(llm.asked["user"].split("\n", 1)[1])
    assert [x["number"] for x in sent["stories"]] == [1, 2, 3] and sent["stories"][2]["section"] == "Tools & SDKs"
    assert sent["stories"][0]["What is it?"] == "A long-context model."   # the plain idea, not how it works
    # The brief's order, every story with its number; a number from nowhere drops its sentence.
    assert script.split("\n\n") == [
        "Good morning. It is Thursday, October 15, and two launches lead.",
        "Starting with the top stories, number 1. Model X 2 reads 2M tokens. Put simply, it reads a whole repo."
        " Since you've built RAG, you may need less of it.",
        "Number 2. Flash gets cheaper.",
        "In tools, number 3. SDK 3.0 is out. A new client. For you, an upgrade.",   # one sentence a part
        "Read number 1 first."]
    assert warnings == ["narrator: dropped sentence with unsupported ['40']: 'It is 40% faster.'",
                        "narrator: story 2 missing; its title is read"]


def test_the_narrator_keeps_to_its_length_leaving_out_details_first():
    def said(tag):
        return f"{tag} " + " ".join(["word"] * 9) + "."   # 10 words

    def story(n):
        return {"number": n, "what_happened": f"Number {n}. {said(f'W{n}a')} {said(f'W{n}b')}",
                "in_simple_words": f"{said(f'S{n}a')} {said(f'S{n}b')}", "why_it_matters": said(f"M{n}a")}

    llm = Narrator({"opening": "Good morning.", "stories": [story(1), story(2), story(3)], "closing": "Bye."})
    # 149 words with the per-part limits (story 3 is a minor one: one sentence of what happened); 1 minute is 112
    script, _, warnings = narrate(llm, BRIEF, model="gemini-3.8-flash", prompts_dir=ROOT / "prompts",
                                  profile="An FDE.", spoken_date="Thursday, October 15", minutes=1)
    left_out = {"W1b", "W2b", "W3b", "S3b", "S2b"}   # details first, then simple words from the last story back
    assert not any(tag in script for tag in left_out) and len(script.split()) == 109 and not warnings
    assert all(f"{p}{n}a" in script for p in "WSM" for n in (1, 2, 3)) and "S1b" in script


def test_the_narrator_may_say_in_digits_what_the_brief_spells_out():
    from ainews.pipeline.narrate import _spelled

    assert _spelled("raised six point eight billion dollars, hired one thousand people") == {"6.8", "1000"}
    assert _spelled("version one point four point six, one hundred thirty-seven billion, seven hundred") == {
        "1.4.6", "137", "700"}
    assert _spelled("thirty-eight billion, fifty percent, two thousand and twenty-six, a hundred") == {
        "38", "50", "2026", "100"}
    assert _spelled("seventeen, ninety-nine, the point is, someone, tens") == {"17", "99"}


def test_the_brief_keeps_what_its_voice_said(conn, settings):
    from test_pipeline import NOW, SOURCES, FakeLLM, seed

    from ainews.pipeline.brief import build_brief

    class Failing(FakeLLM):
        def json(self, *, purpose, **kw):
            if purpose == "narrator":
                raise BudgetExceeded("narrator: refused")
            return super().json(purpose=purpose, **kw)

    seed(conn)
    result = build_brief(conn, settings, SOURCES, llm=Failing(), now_dt=NOW)
    assert result["speech"].split("\n\n")[1].startswith("Top stories.\nNumber 1. Model X ships")   # the headlines
    assert result["stats"]["speech"]["source"] == "headlines" and "refused" in result["stats"]["narrator_error"]
    row = conn.execute("SELECT source, script FROM brief_speech WHERE brief_id = ?", (result["brief_id"],)).fetchone()
    assert tuple(row) == ("headlines", result["speech"])
    drop_latest_brief(conn)
    assert conn.execute("SELECT COUNT(*) FROM brief_speech").fetchone()[0] == 0


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
    six_minutes = "Word. " * 750   # 4,500 characters: worst case $0.13
    with pytest.raises(BudgetExceeded, match="AINEWS_VOICE_MAX_USD_PER_BRIEF"):
        speaker(conn, settings, tts, voice_max_usd_per_brief=0.10).read(six_minutes)
    conn.execute("INSERT INTO llm_calls (at, purpose, model, input_tokens, output_tokens, cost_usd)"
                 " VALUES (?, 'voice', 'gemini-2.5-flash-tts', 0, 0, 2.90)", (now(),))
    with pytest.raises(BudgetExceeded, match="month spend \\$2.90"):
        speaker(conn, settings, tts).read(six_minutes)
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
