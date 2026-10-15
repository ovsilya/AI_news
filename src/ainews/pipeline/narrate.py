"""Narrator: turns the finished brief into the script of a short audio overview (deliver/voice.py reads it).

The overview is for listening first: a few minutes on what happened and which stories are worth reading,
each story named by its number so it can be found in the text. It is written from the verified brief only,
and checked the same way: a sentence with a number the brief doesn't contain is dropped."""
from __future__ import annotations

import json
from pathlib import Path

from ..deliver.render import EXPLAINER, LABELS, PRO_TAKE, _note, _numbered
from .score import load_prompt
from .verify import SENTENCE, _numbers

WORDS_PER_MINUTE = 125   # Gemini TTS, measured: 115 to 135
SCHEMA = {
    "type": "object",
    "properties": {"paragraphs": {"type": "array", "items": {"type": "string"}}},
    "required": ["paragraphs"],
    "additionalProperties": False,
}


def _flat(value) -> str:
    intro, points = _note(value)
    return " ".join([intro, *points]).strip()


def narrator_payload(brief: dict) -> dict:
    return {"headline": brief["headline"], "stories": [
        {"number": n, "section": LABELS[key].split(" ", 1)[1], "title": it["title"], "summary": it.get("summary", ""),
         EXPLAINER: _flat(it.get("explainer")), PRO_TAKE: _flat(it.get("pro_take"))}
        for key, _, n, it in _numbered(brief)]}


def narrate(llm, brief: dict, *, model: str, prompts_dir: Path, profile: str, spoken_date: str,
            minutes: int) -> tuple[str, str, list[str]]:
    """Returns (script, prompt version, warnings): paragraphs separated by blank lines."""
    system, version = load_prompt(prompts_dir, "narrator", profile)
    payload = narrator_payload(brief)
    words = minutes * WORDS_PER_MINUTE
    per_story = round((words - 100) / max(1, len(payload["stories"])))   # ~100 words for the opening and closing
    user = (f"Today is {spoken_date}. Target length: {round(words * 0.9)} to {round(words * 1.1)} words in total, about"
            f" {minutes} minutes read aloud: about {per_story} words per story on average, more for the top stories,"
            f" less for minor ones. The brief:\n" + json.dumps(payload, ensure_ascii=False))
    out = llm.json(model=model, system=system, user=user, schema=SCHEMA, max_tokens=8000, effort="low",
                   purpose="narrator")
    # Numbers it may say: the brief's, the story numbers and the date.
    allowed = _numbers(json.dumps(payload, ensure_ascii=False) + " " + spoken_date)
    allowed |= {str(s["number"]) for s in payload["stories"]}
    warnings, paragraphs = [], []
    for para in out.get("paragraphs") or []:
        kept = []
        for sentence in SENTENCE.split(" ".join(para.split())):
            bad = _numbers(sentence) - allowed
            if bad:
                warnings.append(f"narrator: dropped sentence with unsupported {sorted(bad)}: {sentence[:80]!r}")
            elif sentence:
                kept.append(sentence)
        if kept:
            paragraphs.append(" ".join(kept))
    script = "\n\n".join(paragraphs)
    said = len(script.split())
    if not said:
        raise ValueError("narrator: empty script")
    if said > 1.5 * words:
        warnings.append(f"narrator: {said} words, target {words}")
    return script, version, warnings
