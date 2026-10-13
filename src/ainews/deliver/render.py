"""Render a verified brief as Markdown (local archive) and Telegram HTML messages."""
from __future__ import annotations

import html

from ..pipeline.rank import Story

LABELS = {
    "top": "🔝 Top stories",
    "big_picture": "🌍 Big picture",
    "models": "🧠 Models & pricing",
    "tools": "🛠 Tools & SDKs",
    "research": "📄 Research",
    "systems": "⚙️ Low-level systems",
    "community": "💬 Community pulse",
}
TELEGRAM_LIMIT = 4096
EXPLAINER = "What is it?"
PRO_TAKE = "Comment from a pro"
EXPANDABLE = True   # collapse the explanation + pro comment until tapped, so the brief is quick to scan
DIVIDER = "━━━━━━━━━━━━"


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


def to_markdown(brief: dict, stories: dict[int, Story], source_names: dict[str, str], title: str,
                footer: str) -> str:
    lines = [f"# {title}", "", f"_{brief['headline']}_", ""]
    for key, first, n, it in _numbered(brief):
        if first:
            lines += [f"## {LABELS[key]}", ""]
        links = " · ".join(f"[{label}]({url})" for label, url in _links(it["story_ids"], stories, source_names))
        lines += [f"### {n}. {it['title']}", "", it["summary"], ""]
        notes = []
        if it.get("explainer"):
            notes.append(f"> 💡 **{EXPLAINER}** {it['explainer']}")
        if it.get("pro_take"):
            notes.append(f"> 🎓 **{PRO_TAKE}:** {it['pro_take']}")
        if notes:
            lines += ["\n>\n".join(notes), ""]
        lines += [f"🔗 {links}", ""]
    lines += ["---", footer]
    return "\n".join(lines)


def to_telegram(brief: dict, stories: dict[int, Story], source_names: dict[str, str], title: str,
                footer: str) -> list[str]:
    """Telegram HTML, split into messages at item boundaries so no message exceeds the limit.

    Each item: bold numbered title, what happened, then the explanation and the pro comment in a
    blockquote that is collapsed to a few lines until tapped, then the source links."""
    e = html.escape
    quote_tag = "blockquote expandable" if EXPANDABLE else "blockquote"
    blocks = [f"📰 <b>{e(title)}</b>\n<i>{e(brief['headline'])}</i>"]
    for key, first, n, it in _numbered(brief):
        links = " · ".join(f'<a href="{e(url, quote=True)}">{e(label)}</a>'
                           for label, url in _links(it["story_ids"], stories, source_names))
        text = f"<b>{n}. {e(it['title'])}</b>"
        if it.get("summary"):
            text += f"\n{e(it['summary'])}"
        notes = []
        if it.get("explainer"):
            notes.append(f"💡 <b>{EXPLAINER}</b>\n{e(it['explainer'])}")
        if it.get("pro_take"):
            notes.append(f"🎓 <b>{PRO_TAKE}</b>\n{e(it['pro_take'])}")
        if notes:
            text += f"\n<{quote_tag}>" + "\n\n".join(notes) + "</blockquote>"
        else:
            text += "\n"
        text += f"🔗 {links}"
        if first:  # keep a section heading in the same message as its first item
            text = f"{DIVIDER}\n<b>{e(LABELS[key].upper())}</b>\n\n{text}"
        blocks.append(f"\n{text}")
    blocks.append(f"\n{DIVIDER}\n<i>{e(footer)}</i>")

    messages, current = [], ""
    for block in blocks:
        candidate = f"{current}\n{block}" if current else block.lstrip("\n")
        if len(candidate) > TELEGRAM_LIMIT and current:
            messages.append(current)
            current = block.lstrip("\n")
        else:
            current = candidate
    if current:
        messages.append(current)
    return [m[:TELEGRAM_LIMIT] for m in messages]
