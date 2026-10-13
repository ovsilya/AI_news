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


def to_markdown(brief: dict, stories: dict[int, Story], source_names: dict[str, str], title: str,
                footer: str) -> str:
    lines = [f"# {title}", "", f"_{brief['headline']}_", ""]
    for sec in brief["sections"]:
        lines += [f"## {LABELS[sec['key']]}", ""]
        for it in sec["items"]:
            links = " · ".join(f"[{label}]({url})" for label, url in _links(it["story_ids"], stories, source_names))
            lines.append(f"- **{it['title']}** {it['summary']}".rstrip())
            if it.get("why_it_matters"):
                lines.append(f"  - _Why it matters:_ {it['why_it_matters']}")
            lines.append(f"  - {links}")
        lines.append("")
    lines += ["---", footer]
    return "\n".join(lines)


def to_telegram(brief: dict, stories: dict[int, Story], source_names: dict[str, str], title: str,
                footer: str) -> list[str]:
    """Telegram HTML, split into messages at item boundaries so no message exceeds the limit."""
    e = html.escape
    blocks = [f"<b>{e(title)}</b>\n<i>{e(brief['headline'])}</i>"]
    for sec in brief["sections"]:
        blocks.append(f"\n<b>{e(LABELS[sec['key']])}</b>")
        for it in sec["items"]:
            links = " · ".join(f'<a href="{e(url, quote=True)}">{e(label)}</a>'
                               for label, url in _links(it["story_ids"], stories, source_names))
            text = f"• <b>{e(it['title'])}</b> {e(it['summary'])}".rstrip()
            if it.get("why_it_matters"):
                text += f"\n<i>Why it matters:</i> {e(it['why_it_matters'])}"
            blocks.append(f"{text}\n{links}")
    blocks.append(f"\n<i>{e(footer)}</i>")

    messages, current = [], ""
    for block in blocks:
        candidate = f"{current}\n{block}" if current else block
        if len(candidate) > TELEGRAM_LIMIT and current:
            messages.append(current)
            current = block
        else:
            current = candidate
    if current:
        messages.append(current)
    return [m[:TELEGRAM_LIMIT] for m in messages]
