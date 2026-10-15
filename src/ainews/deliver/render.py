"""Render a verified brief as Markdown (local archive) and Telegram HTML messages."""
from __future__ import annotations

import html
import re
from datetime import datetime, time, timedelta, timezone

from ..pipeline.fulltext import COVERAGE_RANK, coverage
from ..pipeline.rank import Story

LABELS = {
    "top": "🔝 Top stories",
    "big_picture": "🌍 Big picture",
    "trends": "🔭 Trends & future of work",
    "models": "🧠 Models & pricing",
    "tools": "🛠 Tools & SDKs",
    "research": "📄 Research",
    "systems": "⚙️ Low-level systems",
    "community": "💬 Community pulse",
}
FLAGS = {   # how much of the story the editor had to work from
    "article": "📄 Full article",
    "abstract": "📄 Paper abstract",
    "excerpt": "📝 Excerpt only",
    "headline": "📌 Headline only",
}
TELEGRAM_LIMIT = 4096   # characters of visible text per message, after Telegram parses the HTML
PACK_LIMIT = 4000       # what we fill a message to, leaving a margin
SUMMARY_LIMIT = 3000    # so a story always fits in one message with its title and links
EXPLAINER = "What is it?"
PRO_TAKE = "Comment from a pro"
WHAT_HAPPENED = "What happened"
DIVIDER = "━━━━━━━━━━━━"
LABEL = re.compile(r"^([^:]{2,30}):\s+(.+)$", re.S)   # "How it works: …" -> bold label + text
TAG = re.compile(r"<[^>]+>")


def visible_len(text_html: str) -> int:
    """Message length as Telegram counts it: the text without HTML tags, in UTF-16 code units."""
    return len(html.unescape(TAG.sub("", text_html)).encode("utf-16-le")) // 2


def _links(ids: list[int], stories: dict[int, Story], source_names: dict[str, str]) -> list[tuple[str, str]]:
    """[(label, url)] for every source item of the cited stories, one per source."""
    out, seen = [], set()
    for sid in ids:
        for it in stories[sid].items:
            if it["source_id"] in seen:
                continue
            seen.add(it["source_id"])
            out.append((source_names.get(it["source_id"], it["source_id"]), it["url"]))
            if it.get("discussion_url") and it["discussion_url"] != it["url"]:
                out.append(("discussion", it["discussion_url"]))
    return out


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _day(dt: datetime) -> str:
    # Feeds that give only a date send midnight UTC; converting that to local time would shift the day.
    if not (dt.utcoffset() == timedelta(0) and dt.time() == time(0)):
        dt = dt.astimezone()
    return f"{dt:%a} {dt.day} {dt:%b}"


def _meta(ids: list[int], stories: dict[int, Story]) -> str:
    """'🗓 Mon 12 Oct · 📄 Full article': when the story was published (else first seen by us),
    and how much of it the editor read."""
    items = [it for sid in ids for it in stories[sid].items]
    published = [t for t in (_parse(it.get("published_at")) for it in items) if t]
    seen = [t for t in (_parse(it.get("fetched_at")) for it in items) if t]
    parts = []
    if published:
        parts.append(f"🗓 {_day(min(published))}")
    elif seen:
        parts.append(f"🗓 first seen {_day(min(seen))}")
    parts.append(FLAGS[max((coverage(stories[sid]) for sid in ids), key=COVERAGE_RANK.get)])
    return " · ".join(parts)


def _numbered(brief: dict):
    """(section key, is first item of its section, running number, item) over the whole brief."""
    n = 0
    for sec in brief["sections"]:
        for i, it in enumerate(sec["items"]):
            n += 1
            yield sec["key"], i == 0, n, it


def _note(value) -> tuple[str, list[str]]:
    """A note is {"intro", "points"}; older briefs and the no-LLM digest use plain text."""
    if isinstance(value, dict):
        return value.get("intro", "") or "", [p for p in value.get("points") or [] if p]
    return value or "", []


def _split_label(point: str) -> tuple[str, str]:
    m = LABEL.match(point.strip())
    return (m.group(1), m.group(2)) if m else ("", point.strip())


def to_markdown(brief: dict, stories: dict[int, Story], source_names: dict[str, str], title: str,
                footer: str) -> str:
    lines = [f"# {title}", "", f"_{brief['headline']}_", ""]
    for key, first, n, it in _numbered(brief):
        if first:
            lines += [f"## {LABELS[key]}", ""]
        links = " · ".join(f"[{label}]({url})" for label, url in _links(it["story_ids"], stories, source_names))
        lines += [f"### {n}. {it['title']}", "", f"_{_meta(it['story_ids'], stories)}_", "", it["summary"], ""]
        for emoji, name, field in (("💡", EXPLAINER, "explainer"), ("🎓", PRO_TAKE, "pro_take")):
            intro, points = _note(it.get(field))
            if not intro and not points:
                continue
            lines.append(f"> {emoji} **{name}** {intro}".rstrip())
            for point in points:
                label, text = _split_label(point)
                lines.append(f"> - **{label}:** {text}" if label else f"> - {text}")
            lines.append("")
        lines += [f"🔗 {links}", ""]
    lines += ["---", footer]
    return "\n".join(lines)


def _note_html(emoji: str, name: str, value) -> str:
    e = html.escape
    intro, points = _note(value)
    if not intro and not points:
        return ""
    body = [f"{emoji} <b>{name}</b>"]
    if intro:
        body.append(e(intro))
    if points:
        bullets = []
        for point in points:
            label, text = _split_label(point)
            bullets.append(f"• <b>{e(label)}:</b> {e(text)}" if label else f"• {e(text)}")
        body.append("\n".join(bullets))
    return "<blockquote>" + "\n\n".join(body) + "</blockquote>"


def _fit_note(emoji: str, name: str, value) -> str:
    """The note as HTML within one message: drop trailing points, then shorten the intro."""
    intro, points = _note(value)
    while True:
        note = _note_html(emoji, name, {"intro": intro, "points": points})
        if visible_len(note) <= PACK_LIMIT:
            return note
        if points:
            points = points[:-1]
        else:
            intro = intro[: len(intro) // 2].rsplit(" ", 1)[0] + "…"


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"


def _pack(units: list[str]) -> list[str]:
    """Fill each message with as many whole units as fit, in order."""
    messages, current = [], ""
    for unit in units:
        joined = f"{current}\n\n{unit}" if current else unit
        if current and visible_len(joined) > PACK_LIMIT:
            messages.append(current)
            current = unit
        else:
            current = joined
    return messages + [current] if current else messages


def to_telegram(brief: dict, stories: dict[int, Story], source_names: dict[str, str], title: str,
                footer: str) -> list[str]:
    """Telegram HTML in as few messages as possible (each is one phone notification).

    The first message opens with the title, headline and a numbered contents list. Items follow in
    order, each kept whole: section heading, bold numbered title, date and coverage flag, what happened,
    the "What is it?" and "Comment from a pro" notes as quoted blocks with bullet points, and the
    source links. A message holds as many items as fit; an item too long for a message of its own is
    sent as the story, then its notes."""
    e = html.escape
    numbered = list(_numbered(brief))

    contents = [f"📰 <b>{e(title)}</b>", f"<i>{e(brief['headline'])}</i>"]
    for key, first, n, it in numbered:
        if first:
            contents.append(f"\n<b>{e(LABELS[key])}</b>")
        contents.append(f"{n}. {e(it['title'])}")
    units = ["\n".join(contents)]

    for key, first, n, it in numbered:
        links = " · ".join(f'<a href="{e(url, quote=True)}">{e(label)}</a>'
                           for label, url in _links(it["story_ids"], stories, source_names))
        story = [f"<b>{n}. {e(it['title'])}</b>\n<i>{e(_meta(it['story_ids'], stories))}</i>"]
        if first:
            story.insert(0, f"{DIVIDER}\n<b>{e(LABELS[key])}</b>")
        if it.get("summary"):
            story.append(f"📰 <b>{WHAT_HAPPENED}</b>\n{e(_clip(it['summary'], SUMMARY_LIMIT))}")
        notes = [x for x in (_note_html("💡", EXPLAINER, it.get("explainer")),
                             _note_html("🎓", PRO_TAKE, it.get("pro_take"))) if x]
        whole = "\n\n".join(story + notes) + f"\n🔗 {links}"
        if visible_len(whole) <= PACK_LIMIT:
            units.append(whole)
            continue
        # Rare: the story with its links, then each note, labelled so it reads on its own.
        units.append("\n\n".join(story) + f"\n🔗 {links}")
        for emoji, name, field in (("💡", EXPLAINER, "explainer"), ("🎓", PRO_TAKE, "pro_take")):
            note = _fit_note(emoji, name, it.get(field))
            if note:
                units.append(f"<i>{n}. {e(it['title'])}, continued</i>\n{note}")

    units.append(f"{DIVIDER}\n<i>{e(footer)}</i>")
    return _pack(units)


SPOKEN = {"→": " to ", "&": " and ", "~": "about ", "≈": "about ", "×": " times ", "•": ""}
URL = re.compile(r"\(?https?://[^\s)\]]+\)?")
EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️‍]")
OUTRO = "That's the brief. The links to every story are in the messages below."


def _spoken(text: str) -> str:
    """Text as it should be read: no links, emoji or Markdown marks, symbols said in words."""
    text = EMOJI.sub("", URL.sub("", text or ""))
    for symbol, words in SPOKEN.items():
        text = text.replace(symbol, words)
    text = " ".join(re.sub(r"[*_`#|]", "", text).split())
    return re.sub(r" ([.,;:!?])", r"\1", text)   # where a link was taken out


def _sentence(text: str) -> str:
    text = _spoken(text)
    return text if not text or text[-1] in ".!?…:" else text + "."


def to_speech(brief: dict, title: str) -> str:
    """The brief as a script to read aloud: title and headline, then each story with its number, what
    happened, and the two notes, in paragraphs; links and coverage flags are left to the text messages."""
    paragraphs = [_sentence(title) + " " + _sentence(brief["headline"])]
    for key, first, n, it in _numbered(brief):
        lines = [_sentence(LABELS[key])] if first else []
        lines.append(f"Story {n}. {_sentence(it['title'])}")
        if it.get("summary"):
            lines.append(_sentence(it["summary"]))
        for name, field in ((EXPLAINER, "explainer"), (f"{PRO_TAKE}.", "pro_take")):
            intro, points = _note(it.get(field))
            if intro or points:
                lines.append(" ".join([name] + [_sentence(t) for t in [intro, *points] if t]))
        paragraphs.append("\n".join(lines))
    return "\n\n".join(paragraphs + [OUTRO])
