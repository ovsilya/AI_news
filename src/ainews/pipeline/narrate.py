"""Narrator: turns the finished brief into the script of a short audio overview (deliver/voice.py reads it).

The overview is for listening first, by someone new to many of the topics: for each story, in the brief's
order and by its number, what happened, the idea in simple words, and why it matters for the listener's own
work; then the stories most worth reading. The model writes each of those parts separately (one or two
sentences each, which keeps it short) and the code joins them, so every story is there, in order. It is
written from the verified brief only, and checked the same way: a sentence with a number the brief doesn't
contain is dropped."""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from ..deliver.render import EXPLAINER, LABELS, PRO_TAKE, _note, _numbered, _sentence, _split_label
from .score import load_prompt
from .verify import SENTENCE, _numbers

log = logging.getLogger(__name__)

WORDS_PER_MINUTE = 140   # Gemini TTS at voice.STYLE's pace, measured on the narrator's scripts
PLAIN = {"analogy", "example"}   # the "What is it?" points that explain in everyday terms; "How it works" is too deep to hear
PARTS = ("what_happened", "in_simple_words", "why_it_matters")
# Sentences kept per part, top stories first: the model tends to write two where one is asked for, and this is
# what holds the overview to its few minutes. The first sentences carry the point.
MAX_SENTENCES = {"top": (2, 2, 1), "other": (1, 2, 1)}
LEAD = re.compile(r"\bnumber \d+\.$", re.I)   # "In research, number 12." is said with the sentence after it
SCHEMA = {
    "type": "object",
    "properties": {
        "opening": {"type": "string"},
        "stories": {"type": "array", "items": {
            "type": "object",
            "properties": {"number": {"type": "integer"}, **{p: {"type": "string"} for p in PARTS}},
            "required": ["number", *PARTS],
            "additionalProperties": False,
        }},
        "closing": {"type": "string"},
    },
    "required": ["opening", "stories", "closing"],
    "additionalProperties": False,
}


def _flat(value, keep: set[str] | None = None) -> str:
    """A note as one text; with keep, only the points with those labels."""
    intro, points = _note(value)
    if keep is not None:
        points = [p for p in points if _split_label(p)[0].casefold() in keep]
    return " ".join([intro, *points]).strip()


def narrator_payload(brief: dict) -> dict:
    return {"headline": brief["headline"], "stories": [
        {"number": n, "section": LABELS[key].split(" ", 1)[1], "title": it["title"], "summary": it.get("summary", ""),
         EXPLAINER: _flat(it.get("explainer"), keep=PLAIN), PRO_TAKE: _flat(it.get("pro_take"))}
        for key, _, n, it in _numbered(brief)]}


def _sentences(text: str) -> list[str]:
    out = []
    for sentence in SENTENCE.split(" ".join((text or "").split())):
        if out and LEAD.search(out[-1]):
            out[-1] += " " + sentence
        elif sentence:
            out.append(sentence)
    return out


def _checked(text: str, allowed: set[str], warnings: list[str], limit: int | None = None) -> tuple[str, int]:
    """The text without sentences whose numbers aren't allowed, and at most limit sentences; and how many
    sentences the limit cut."""
    kept = []
    for sentence in _sentences(text):
        bad = _numbers(sentence) - allowed
        if bad:
            warnings.append(f"narrator: dropped sentence with unsupported {sorted(bad)}: {sentence[:80]!r}")
        else:
            kept.append(sentence)
    cut = max(0, len(kept) - limit) if limit else 0
    return " ".join(kept[:limit] if limit else kept), cut


def narrate(llm, brief: dict, *, model: str, prompts_dir: Path, profile: str, spoken_date: str,
            minutes: int) -> tuple[str, str, list[str]]:
    """Returns (script, prompt version, warnings): paragraphs separated by blank lines."""
    system, version = load_prompt(prompts_dir, "narrator", profile)
    payload = narrator_payload(brief)
    words = minutes * WORDS_PER_MINUTE
    per_story = round((words - 80) / max(1, len(payload["stories"])))   # ~80 words for the opening and closing
    user = (f"Today is {spoken_date}. Target length: about {words} words in total, about {minutes} minutes read"
            f" aloud: about {per_story} words per story across its three parts, a little more for the top stories"
            f" and less for minor ones. The brief:\n" + json.dumps(payload, ensure_ascii=False))
    out = llm.json(model=model, system=system, user=user, schema=SCHEMA, max_tokens=12000, effort="medium",
                   purpose="narrator")
    # Numbers it may say: the brief's, the story numbers and the date.
    allowed = _numbers(json.dumps(payload, ensure_ascii=False) + " " + spoken_date)
    allowed |= {str(s["number"]) for s in payload["stories"]}
    warnings: list[str] = []
    written = {s.get("number"): s for s in out.get("stories") or []}
    if not written:
        raise ValueError("narrator: no stories in its output")
    paragraphs, trimmed = [_checked(out.get("opening", ""), allowed, warnings)[0]], 0
    for story in payload["stories"]:   # the brief's order, every story, whatever the model returned
        n, parts = story["number"], written.get(story["number"])
        limits = MAX_SENTENCES["top" if story["section"] == LABELS["top"].split(" ", 1)[1] else "other"]
        said = [_checked(parts.get(p, ""), allowed, warnings, limit) for p, limit in zip(PARTS, limits)] if parts else []
        trimmed += sum(cut for _, cut in said)
        text = " ".join(t for t, _ in said if t)
        if not re.search(rf"\bnumber {n}\b", text, re.I):
            warnings.append(f"narrator: story {n} " + ("had no number" if text else "missing; its title is read"))
            text = f"Number {n}. " + (text or _sentence(story["title"]))
        paragraphs.append(" ".join(text.split()))
    paragraphs.append(_checked(out.get("closing", ""), allowed, warnings)[0])
    script = "\n\n".join(p for p in paragraphs if p)
    if trimmed:
        log.info("narrator: %d sentences over the per-part limits left out", trimmed)
    if len(script.split()) > 1.5 * words:
        warnings.append(f"narrator: {len(script.split())} words, target {words}")
    return script, version, warnings
