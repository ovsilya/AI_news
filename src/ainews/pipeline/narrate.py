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

WORDS_PER_MINUTE = 112   # Gemini TTS at voice.STYLE's pace (about 13.3 characters a second), measured on the narrator's scripts
PLAIN = {"analogy", "example"}   # the "What is it?" points that explain in everyday terms; "How it works" is too deep to hear
PARTS = ("what_happened", "in_simple_words", "why_it_matters")
# Sentences kept per part, top stories first: the model tends to write two where one is asked for, and this is
# what holds the overview to its few minutes. The first sentences carry the point.
MAX_SENTENCES = {"top": (2, 2, 1), "other": (1, 2, 1)}
# Over its length even so (it wrote 900 to 1,150 words for the same target), the script leaves out more, one
# sentence at a time and never the first of a part: a top story's second sentence of what happened (detail),
# then the second sentence of the simple explanation, minor stories from the last one back, then top stories.
TRIM = ((0, "top"), (1, "other"), (1, "top"))
SLACK = 1.05   # how far over its word target the script may run
LEAD = re.compile(r"\bnumber \d+\.$", re.I)   # "In research, number 12." is said with the sentence after it
# Numbers the brief spells out ("six point eight billion"), which the narrator writes with digits ("6.8 billion").
# Million and up stay words either way, so a run of number words ends there.
SMALL = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen"
                                     " fourteen fifteen sixteen seventeen eighteen nineteen".split())}
TENS = {w: 10 * i for i, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split(), start=2)}
_WORD = "|".join([*SMALL, *TENS, "hundred", "thousand", "point"])
SPELLED = re.compile(rf"\b(?:{_WORD})(?:(?:[\s-]+|\s+and\s+)(?:{_WORD}))*\b", re.I)
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


def _value(words: list[str]) -> int | None:
    """['one', 'hundred', 'thirty', 'seven'] -> 137; None without a number word."""
    if not any(w in SMALL or w in TENS or w in ("hundred", "thousand") for w in words):
        return None
    total = current = 0
    for w in words:
        if w == "hundred":
            current = (current or 1) * 100
        elif w == "thousand":
            total, current = total + (current or 1) * 1000, 0
        else:
            current += SMALL.get(w, TENS.get(w, 0))
    return total + current


def _spelled(text: str) -> set[str]:
    """The numbers text spells out, with digits: 'six point eight billion' -> {'6.8'}, 'version one point four
    point six' -> {'1.4.6'}, 'one thousand' -> {'1000'}."""
    out = set()
    for run in SPELLED.finditer(text):
        whole, *decimals = " ".join(w for w in re.split(r"[\s-]+", run.group(0).casefold()) if w != "and").split("point")
        head = _value(whole.split())
        if head is None:
            continue
        digits = [str(head)]
        for part in decimals:   # each spoken digit after "point": "four point six" -> "4.6"
            ds = part.split()
            if not ds or any(SMALL.get(d, 10) > 9 for d in ds):
                break
            digits.append("".join(str(SMALL[d]) for d in ds))
        out.add(".".join(digits))
    return _numbers(" ".join(out))


def _sentences(text: str) -> list[str]:
    out = []
    for sentence in SENTENCE.split(" ".join((text or "").split())):
        if out and LEAD.search(out[-1]):
            out[-1] += " " + sentence
        elif sentence:
            out.append(sentence)
    return out


def _checked(text: str, allowed: set[str], warnings: list[str]) -> list[str]:
    """The text's sentences, without those whose numbers aren't allowed."""
    kept = []
    for sentence in _sentences(text):
        bad = _numbers(sentence) - allowed
        if bad:
            warnings.append(f"narrator: dropped sentence with unsupported {sorted(bad)}: {sentence[:80]!r}")
        else:
            kept.append(sentence)
    return kept


def _words(sentences: list[str]) -> int:
    return sum(len(s.split()) for s in sentences)


def _fit(stories: list[tuple[bool, list[list[str]]]], budget: float, fixed: int) -> int:
    """Leaves out sentences in TRIM order until the stories, with fixed more words, fit budget words; returns how
    many it left out. stories: (top story?, its sentences per part)."""
    total, cut = fixed + sum(_words(part) for _, parts in stories for part in parts), 0
    for index, kind in TRIM:
        for top, parts in reversed(stories):
            while total > budget and top == (kind == "top") and len(parts[index]) > 1:
                total -= len(parts[index].pop().split())
                cut += 1
    return cut


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
    # Numbers it may say: the brief's, in digits or spelled out, the story numbers and the date.
    said_in_brief = json.dumps(payload, ensure_ascii=False)
    allowed = _numbers(said_in_brief + " " + spoken_date) | _spelled(said_in_brief)
    allowed |= {str(s["number"]) for s in payload["stories"]}
    warnings: list[str] = []
    written = {s.get("number"): s for s in out.get("stories") or []}
    if not written:
        raise ValueError("narrator: no stories in its output")
    opening, closing = (_checked(out.get(k, ""), allowed, warnings) for k in ("opening", "closing"))
    stories, trimmed = [], 0
    for story in payload["stories"]:   # the brief's order, every story, whatever the model returned
        parts = written.get(story["number"])
        top = story["section"] == LABELS["top"].split(" ", 1)[1]
        said = [_checked(parts.get(p, ""), allowed, warnings) for p in PARTS] if parts else [[] for _ in PARTS]
        for kept, limit in zip(said, MAX_SENTENCES["top" if top else "other"]):
            trimmed += max(0, len(kept) - limit)
            del kept[limit:]
        stories.append((top, said))
    trimmed += _fit(stories, SLACK * words, _words(opening) + _words(closing))
    paragraphs = [" ".join(opening)]
    for story, (_, said) in zip(payload["stories"], stories):
        n, text = story["number"], " ".join(s for part in said for s in part)
        if not re.search(rf"\bnumber {n}\b", text, re.I):
            warnings.append(f"narrator: story {n} " + ("had no number" if text else "missing; its title is read"))
            text = f"Number {n}. " + (text or _sentence(story["title"]))
        paragraphs.append(" ".join(text.split()))
    paragraphs.append(" ".join(closing))
    script = "\n\n".join(p for p in paragraphs if p)
    if trimmed:
        log.info("narrator: %d sentences over the per-part limits or the length left out", trimmed)
    if len(script.split()) > 1.5 * words:
        warnings.append(f"narrator: {len(script.split())} words, target {words}")
    return script, version, warnings
