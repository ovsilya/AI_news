"""Render a verified brief as Markdown (local archive) and Telegram HTML messages."""
from __future__ import annotations

import html
import re

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
TELEGRAM_LIMIT = 4096
EXPLAINER = "What is it?"
PRO_TAKE = "Comment from a pro"
WHAT_HAPPENED = "What happened"
DIVIDER = "━━━━━━━━━━━━"
LABEL = re.compile(r"^([^:]{2,30}):\s+(.+)$", re.S)   # "How it works: …" -> bold label + text


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
        lines += [f"### {n}. {it['title']}", "", it["summary"], ""]
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
    """The note as HTML within the message limit: drop trailing points, then shorten the intro."""
    intro, points = _note(value)
    while True:
        note = _note_html(emoji, name, {"intro": intro, "points": points})
        if len(note) <= TELEGRAM_LIMIT:
            return note
        if points:
            points = points[:-1]
        else:
            intro = intro[: len(intro) // 2].rsplit(" ", 1)[0] + "…"


def to_telegram(brief: dict, stories: dict[int, Story], source_names: dict[str, str], title: str,
                footer: str) -> list[str]:
    """Telegram HTML: a contents message, then one message per item.

    An item message: section and position, bold numbered title, what happened, the "What is it?" and
    "Comment from a pro" notes as quoted blocks with bullet points, then the source links. An item
    too long for one message is sent as two (the story, then the notes)."""
    e = html.escape
    numbered = list(_numbered(brief))
    total = len(numbered)

    contents = [f"📰 <b>{e(title)}</b>", f"<i>{e(brief['headline'])}</i>"]
    for key, first, n, it in numbered:
        if first:
            contents.append(f"\n<b>{e(LABELS[key])}</b>")
        contents.append(f"{n}. {e(it['title'])}")
    if not numbered:
        contents.append(f"\n<i>{e(footer)}</i>")
    messages = ["\n".join(contents)[:TELEGRAM_LIMIT]]

    for key, first, n, it in numbered:
        links = " · ".join(f'<a href="{e(url, quote=True)}">{e(label)}</a>'
                           for label, url in _links(it["story_ids"], stories, source_names))
        story = [f"<i>{e(LABELS[key])} · {n} of {total}</i>", f"<b>{n}. {e(it['title'])}</b>"]
        if it.get("summary"):
            story.append(f"📰 <b>{WHAT_HAPPENED}</b>\n{e(it['summary'])}")
        notes = [x for x in (_note_html("💡", EXPLAINER, it.get("explainer")),
                             _note_html("🎓", PRO_TAKE, it.get("pro_take"))) if x]
        tail = f"🔗 {links}" + (f"\n\n{DIVIDER}\n<i>{e(footer)}</i>" if n == total else "")
        whole = "\n\n".join(story + notes) + "\n" + tail
        if len(whole) <= TELEGRAM_LIMIT:
            messages.append(whole)
        else:  # rare: send the story and the notes separately, each within the limit
            messages.append(("\n\n".join(story) + "\n\n" + tail)[:TELEGRAM_LIMIT])
            for emoji, name, field in (("💡", EXPLAINER, "explainer"), ("🎓", PRO_TAKE, "pro_take")):
                note = _fit_note(emoji, name, it.get(field))
                if note:
                    messages.append(note)
    return messages
