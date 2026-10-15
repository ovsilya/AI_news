"""Model calls on Vertex AI: one JSON-returning call with a spending guard and cost logging.

Default provider is Gemini (Google's own models: no access request needed). Claude on Vertex is kept
as an optional provider (LLM_PROVIDER=anthropic) in case Anthropic model access is approved later.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone

from .db import now

log = logging.getLogger(__name__)

# USD per 1M tokens (input, output incl. thinking), Vertex AI global endpoint, checked 2026-10-12.
PRICES = {
    "gemini-3.1-flash-lite": (0.25, 1.50),
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "gemini-3.5-flash": (1.50, 9.00),
    "gemini-3.6-flash": (0.75, 3.75),
    "gemini-3.7-flash": (0.75, 3.75),
    "gemini-3.8-flash": (0.75, 3.75),
    "gemini-3.1-pro": (2.00, 12.00),
    "gemini-2.5-flash-tts": (0.50, 10.00),   # text in, audio out (25 audio tokens a second)
    "gemini-2.5-pro-tts": (1.00, 20.00),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-opus-5-5": (4.0, 20.0),
}
# Introductory prices with an end date: model -> (first day of the new price, new price).
PRICE_CHANGES = {m: ("2027-01-01", (1.50, 7.50)) for m in ("gemini-3.6-flash", "gemini-3.7-flash", "gemini-3.8-flash")}
UNKNOWN_PRICE = (4.0, 20.0)   # unknown model: assume an expensive one so cost is never understated
CACHE_READ, CACHE_WRITE = 0.1, 1.25   # multipliers on the input price (Gemini has no cache-write surcharge)


class LLMError(RuntimeError):
    pass


class LLMDisabled(LLMError):
    """The "button" is off: AINEWS_LLM_ENABLED is not true."""


class BudgetExceeded(LLMError):
    """A call was refused because its worst-case cost would break a spending cap."""


@dataclass
class Usage:
    input_tokens: int          # billed at the full input price (excludes cache reads)
    output_tokens: int         # response + thinking
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


def price_for(model: str, on: date | None = None) -> tuple[float, float]:
    base = model.split("@", 1)[0]
    day = (on or datetime.now(timezone.utc).date()).isoformat()
    for name in sorted(PRICES, key=len, reverse=True):   # longest match first: 3.5-flash-lite before 3.5-flash
        if base.startswith(name):
            change = PRICE_CHANGES.get(name)
            return change[1] if change and day >= change[0] else PRICES[name]
    return UNKNOWN_PRICE


def cost_usd(model: str, u: Usage) -> float:
    pin, pout = price_for(model)
    return (u.input_tokens * pin + u.cache_read_tokens * pin * CACHE_READ
            + u.cache_write_tokens * pin * CACHE_WRITE + u.output_tokens * pout) / 1_000_000


def worst_case_usd(model: str, prompt_chars: int, max_tokens: int) -> float:
    """Upper bound for one call: ~3 characters per token (conservative), the whole prompt billed as a cache
    write (1.25x), and every allowed output token used (max_tokens includes thinking)."""
    pin, pout = price_for(model)
    return (prompt_chars / 3 * pin * CACHE_WRITE + max_tokens * pout) / 1_000_000


def period_start(*, month: bool = False, now_ts: float | None = None) -> str:
    """Local midnight today (or on the 1st of this month), in the time zone the app runs in (TZ), as a UTC
    timestamp comparable with llm_calls.at. The caps follow your day, not UTC's: west of UTC, UTC's day starts
    the afternoon or evening before, which put the evening's runs on the next morning's bill."""
    t = time.localtime(now_ts)
    start = time.mktime((t.tm_year, t.tm_mon, 1 if month else t.tm_mday, 0, 0, 0, 0, 0, -1))
    return datetime.fromtimestamp(start, timezone.utc).isoformat(timespec="seconds")


VOICE = "voice"   # purpose of the brief's text-to-speech calls, which have their own caps (deliver/voice.py)


def spent_since(conn: sqlite3.Connection, since_iso: str, *, voice: bool = False) -> float:
    """Spend since since_iso on text calls (the AINEWS_MAX_USD_* caps), or with voice=True on the voice."""
    row = conn.execute("SELECT COALESCE(SUM(cost_usd), 0) AS s FROM llm_calls WHERE at >= ?"
                       f" AND purpose {'' if voice else 'NOT '}LIKE '{VOICE}%'", (since_iso,)).fetchone()
    return float(row["s"])


def log_call(conn: sqlite3.Connection, run_id: int | None, model: str, usage: Usage, purpose: str) -> float:
    """Records one billed call in llm_calls; returns its cost."""
    cost = cost_usd(model, usage)
    log.info("%s %s: in=%d out=%d cache_read=%d $%.4f", purpose, model, usage.input_tokens,
             usage.output_tokens, usage.cache_read_tokens, cost)
    conn.execute(
        "INSERT INTO llm_calls (run_id, at, purpose, model, input_tokens, output_tokens,"
        " cache_read_tokens, cache_write_tokens, cost_usd) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (run_id, now(), purpose, model, usage.input_tokens, usage.output_tokens,
         usage.cache_read_tokens, usage.cache_write_tokens, cost),
    )
    conn.commit()
    return cost


PLACEHOLDER_PROJECTS = {"", "your-personal-project-id"}


class LLM:
    """Provider-independent part: the switch, the spending guard, logging and JSON parsing."""

    def __init__(self, settings, conn: sqlite3.Connection, run_id: int | None = None, *,
                 ignore_switch: bool = False, client=None, reserve: float = 0.0):
        if not settings.llm_enabled and not ignore_switch:
            raise LLMDisabled("model calls are switched off: set AINEWS_LLM_ENABLED=true in .env to turn them on "
                              "(or use --no-llm)")
        if settings.gcp_project_id in PLACEHOLDER_PROJECTS and client is None:
            raise LLMError("GCP_PROJECT_ID is not set in .env (it still has the placeholder value)")
        self.settings = settings
        self.client = client or self._make_client(settings)
        self.conn, self.run_id = conn, run_id
        self.reserve = reserve   # left under the day and month caps for a later lane (the jobs lane keeps it for the brief)
        self.total_cost = 0.0

    def _make_client(self, settings):
        raise NotImplementedError

    def _call(self, *, model, system, user, schema, max_tokens, effort, stream) -> tuple[str | None, Usage, str | None]:
        """Provider call. Returns (JSON text, usage, problem): problem is None, or why the output is unusable
        (truncated, refused, blocked). Unusable responses are still billed, so they are logged too."""
        raise NotImplementedError

    def _guard(self, model: str, prompt_chars: int, max_tokens: int, purpose: str) -> None:
        """Refuse the call before it is made if the worst case would break any cap."""
        s = self.settings
        worst = worst_case_usd(model, prompt_chars, max_tokens)
        checks = [("run", self.total_cost, s.max_usd_per_run, 0.0),
                  ("day", spent_since(self.conn, period_start()), s.max_usd_per_day, self.reserve),
                  ("month", spent_since(self.conn, period_start(month=True)), s.max_usd_per_month, self.reserve)]
        for name, spent, cap, keep in checks:
            if spent + worst > cap - keep:
                kept = f", keeping ${keep:.2f} for the brief (AINEWS_BRIEF_RESERVE_USD)" if keep else ""
                raise BudgetExceeded(f"{purpose}: refused, {name} spend ${spent:.2f} + worst case ${worst:.2f} "
                                     f"> cap ${cap:.2f} (AINEWS_MAX_USD_PER_{name.upper()}){kept}")

    def json(self, *, model: str, system: str, user: str, schema: dict, max_tokens: int,
             effort: str = "medium", purpose: str = "", stream: bool = False) -> dict:
        self._guard(model, len(system) + len(user), max_tokens, purpose)
        text, usage, problem = self._call(model=model, system=system, user=user, schema=schema,
                                          max_tokens=max_tokens, effort=effort, stream=stream)
        self._log(model, usage, purpose if not problem else f"{purpose}:{problem}")
        if problem:
            raise LLMError(f"{purpose}: unusable output ({problem}, max_tokens={max_tokens})")
        try:
            return json.loads(text)
        except (TypeError, json.JSONDecodeError) as exc:
            raise LLMError(f"{purpose}: response is not valid JSON ({exc})") from exc

    def _log(self, model: str, usage: Usage, purpose: str) -> None:
        self.total_cost += log_call(self.conn, self.run_id, model, usage, purpose)


class GeminiLLM(LLM):
    """Gemini on Vertex AI via the Google Gen AI SDK (Application Default Credentials)."""

    def _make_client(self, settings):
        from google import genai

        return genai.Client(enterprise=True, project=settings.gcp_project_id, location=settings.vertex_region)

    def _call(self, *, model, system, user, schema, max_tokens, effort, stream):
        from google.genai import types

        config = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_json_schema=schema,
            max_output_tokens=max_tokens,
            thinking_config=types.ThinkingConfig(thinking_level=effort.upper()),
        )
        r = self.client.models.generate_content(model=model, contents=user, config=config)
        um = r.usage_metadata
        cached = um.cached_content_token_count or 0
        usage = Usage(input_tokens=(um.prompt_token_count or 0) - cached,
                      output_tokens=(um.candidates_token_count or 0) + (um.thoughts_token_count or 0),
                      cache_read_tokens=cached)
        finish = getattr(r.candidates[0].finish_reason, "value", None) if r.candidates else "NO_CANDIDATES"
        return (r.text if finish == "STOP" else None), usage, (None if finish == "STOP" else finish.lower())


class ClaudeLLM(LLM):
    """Claude on Vertex AI (needs Anthropic model access approved in Model Garden)."""

    def _make_client(self, settings):
        from anthropic import AnthropicVertex

        return AnthropicVertex(region=settings.vertex_region, project_id=settings.gcp_project_id)

    def _call(self, *, model, system, user, schema, max_tokens, effort, stream):
        params = dict(
            model=model,
            max_tokens=max_tokens,
            # The system prompt (rubric + profile) is identical across calls in a run: cache it.
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
        )
        if stream:
            with self.client.messages.stream(**params) as s:
                msg = s.get_final_message()
        else:
            msg = self.client.messages.create(**params)
        u = msg.usage
        usage = Usage(u.input_tokens, u.output_tokens, getattr(u, "cache_read_input_tokens", 0) or 0,
                      getattr(u, "cache_creation_input_tokens", 0) or 0)
        if msg.stop_reason in ("refusal", "max_tokens"):
            return None, usage, msg.stop_reason
        return next((b.text for b in msg.content if b.type == "text"), None), usage, None


PROVIDERS = {"gemini": GeminiLLM, "anthropic": ClaudeLLM}


def make_llm(settings, conn: sqlite3.Connection, run_id: int | None = None, **kw) -> LLM:
    try:
        cls = PROVIDERS[settings.llm_provider]
    except KeyError:
        raise LLMError(f"unknown LLM_PROVIDER {settings.llm_provider!r} (use: {', '.join(PROVIDERS)})") from None
    return cls(settings, conn, run_id, **kw)
