"""The brief's audio overview: Gemini text-to-speech on Vertex AI, sent to Telegram as one voice message.

The script is the narrator's few-minute overview (pipeline/narrate.py), or the headlines when it couldn't
be written (render.to_speech). Gemini TTS returns 24 kHz 16-bit mono PCM
and reads about 16.5 characters a second; one request may produce about 11 minutes, so the script is read in
parts of at most PART_CHARS, a few at a time, then joined with a short pause and encoded as OGG/Opus
(opusenc, from opus-tools), the format Telegram plays as a voice message.

The voice has caps of its own (AINEWS_VOICE_MAX_USD_PER_BRIEF and _PER_MONTH): its calls are logged with
purpose "voice", and the text caps leave them out, so the voice can never starve the text."""
from __future__ import annotations

import logging
import re
import shutil
import sqlite3
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from ..llm import VOICE, BudgetExceeded, LLMError, Usage, log_call, period_start, price_for, spent_since

log = logging.getLogger(__name__)

RATE = 24_000            # Hz, 16-bit mono: what Gemini TTS returns
PART_CHARS = 3_500       # about 4.5 minutes read aloud, well under one request's limit
PARALLEL = 4             # parts read at the same time
TRIES = 3
PAUSE = b"\0" * (RATE * 2 * 7 // 10)   # 0.7 s of silence between parts
AUDIO_TOKENS_PER_SECOND = 25
SLOWEST_CHARS_PER_SECOND = 9    # for the worst case; measured 16 to 17 with STYLE
# Not read out. A "calm" host read anywhere from 10 to 15 characters a second from one call to the next; this one
# keeps to about 16.5, the pace of a normal podcast.
STYLE = "Read this aloud like a friendly tech podcast host, at a brisk, lively conversational pace:"
SENTENCE = re.compile(r"(?<=[.!?…])\s+")


class VoiceError(RuntimeError):
    pass


@dataclass
class Speech:
    ogg: bytes
    seconds: float
    cost_usd: float


def parts(script: str, limit: int = PART_CHARS) -> list[str]:
    """The script in order, in parts of at most limit characters, split between paragraphs where possible,
    else between sentences."""
    pieces = []
    for paragraph in script.split("\n\n"):
        if len(paragraph) <= limit:
            pieces.append(paragraph)
            continue
        current = ""
        for sentence in SENTENCE.split(paragraph):
            if current and len(current) + 1 + len(sentence) > limit:
                pieces.append(current)
                current = sentence
            else:
                current = f"{current} {sentence}" if current else sentence
        pieces.append(current)
    out, current = [], ""
    for piece in pieces:
        if current and len(current) + 2 + len(piece) > limit:
            out.append(current)
            current = piece
        else:
            current = f"{current}\n\n{piece}" if current else piece
    return out + [current] if current else out


def worst_case_usd(model: str, chars: int) -> float:
    """Upper bound for reading chars characters: read slowly, prompt at ~3 characters a token."""
    pin, pout = price_for(model)
    audio_tokens = chars / SLOWEST_CHARS_PER_SECOND * AUDIO_TOKENS_PER_SECOND
    prompt_chars = chars + len(STYLE) * (chars // PART_CHARS + 1)
    return (prompt_chars / 3 * pin + audio_tokens * pout) / 1_000_000


def encode_ogg(pcm: bytes) -> bytes:
    """24 kHz 16-bit mono PCM to OGG/Opus at 32 kbit/s, tuned for speech: about 4 KB a second."""
    if shutil.which("opusenc") is None:
        raise VoiceError("opusenc not found: install opus-tools (brew install opus-tools, or apt-get install opus-tools)")
    done = subprocess.run(["opusenc", "--quiet", "--raw", "--raw-rate", str(RATE), "--raw-chan", "1", "--raw-bits", "16",
                           "--bitrate", "32", "--speech", "-", "-"], input=pcm, capture_output=True, timeout=300)
    if done.returncode != 0:
        raise VoiceError(f"opusenc failed: {done.stderr.decode(errors='replace').strip()[:200]}")
    return done.stdout


class Speaker:
    """Reads a script aloud with Gemini TTS, within the voice caps, and logs each part's cost."""

    def __init__(self, settings, conn: sqlite3.Connection, run_id: int | None = None, *, client=None,
                 encode=encode_ogg, sleep=time.sleep):
        if not settings.llm_enabled:
            raise LLMError("model calls are switched off (AINEWS_LLM_ENABLED), so is the voice")
        self.settings, self.conn, self.run_id = settings, conn, run_id
        self.client = client or self._make_client(settings)
        self.encode, self.sleep = encode, sleep

    @staticmethod
    def _make_client(settings):
        from google import genai

        return genai.Client(enterprise=True, project=settings.gcp_project_id, location=settings.vertex_region)

    def _guard(self, chars: int) -> None:
        s = self.settings
        worst = worst_case_usd(s.model_voice, chars)
        spent = spent_since(self.conn, period_start(month=True), voice=True)
        if worst > s.voice_max_usd_per_brief:
            raise BudgetExceeded(f"{VOICE}: refused, worst case ${worst:.2f} for {chars:,} characters"
                                 f" > cap ${s.voice_max_usd_per_brief:.2f} (AINEWS_VOICE_MAX_USD_PER_BRIEF)")
        if spent + worst > s.voice_max_usd_per_month:
            raise BudgetExceeded(f"{VOICE}: refused, month spend ${spent:.2f} + worst case ${worst:.2f}"
                                 f" > cap ${s.voice_max_usd_per_month:.2f} (AINEWS_VOICE_MAX_USD_PER_MONTH)")

    def _say(self, text: str) -> tuple[bytes | None, Usage, str | None]:
        """One request: (PCM, usage, problem); problem is None or why there is no usable audio."""
        from google.genai import types

        voice = types.VoiceConfig(prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=self.settings.voice_name))
        config = types.GenerateContentConfig(response_modalities=["AUDIO"],
                                             speech_config=types.SpeechConfig(voice_config=voice))
        for attempt in range(1, TRIES + 1):
            try:
                r = self.client.models.generate_content(model=self.settings.model_voice, contents=f"{STYLE}\n\n{text}",
                                                        config=config)
                break
            except Exception as exc:   # rate limits and timeouts: wait, then try again
                if attempt == TRIES:
                    raise VoiceError(f"{VOICE}: {type(exc).__name__}: {str(exc)[:200]}") from exc
                log.warning("voice part failed (%s), retrying", exc)
                self.sleep(10 * attempt)
        um = r.usage_metadata
        usage = Usage(input_tokens=um.prompt_token_count or 0, output_tokens=um.candidates_token_count or 0)
        finish = getattr(r.candidates[0].finish_reason, "value", None) if r.candidates else "NO_CANDIDATES"
        audio = b"".join(p.inline_data.data for p in (r.candidates[0].content.parts if r.candidates else [])
                         if getattr(p, "inline_data", None) and p.inline_data.data)
        if finish != "STOP" or not audio:
            return None, usage, (finish or "no_audio").lower()
        return audio, usage, None

    def read(self, script: str) -> Speech:
        self._guard(len(script))
        texts = parts(script)
        with ThreadPoolExecutor(max_workers=PARALLEL) as pool:
            futures = [pool.submit(self._say, text) for text in texts]
        done = []
        for f in futures:
            try:
                done.append(f.result())
            except VoiceError as exc:   # no response, so nothing billed for this part
                done.append((None, None, str(exc)))
        cost, problems = 0.0, []
        for n, (_, usage, problem) in enumerate(done, 1):   # every response is billed, usable or not
            if usage is not None:
                cost += log_call(self.conn, self.run_id, self.settings.model_voice, usage,
                                 VOICE if not problem else f"{VOICE}:{problem}")
            if problem:
                problems.append(f"part {n}: {problem}")
        if problems:
            raise VoiceError(f"{VOICE}: no usable audio ({', '.join(problems)})")
        pcm = PAUSE.join(audio for audio, _, _ in done)
        return Speech(ogg=self.encode(pcm), seconds=len(pcm) / (2 * RATE), cost_usd=cost)
