"""Claude on Vertex AI: one JSON-returning call with usage and cost logging."""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone

from .db import now

log = logging.getLogger(__name__)

# USD per 1M tokens (input, output). Vertex global endpoint = Anthropic list prices.
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-opus-5-5": (4.0, 20.0),
}
CACHE_READ, CACHE_WRITE = 0.1, 1.25   # multipliers on the input price


class LLMError(RuntimeError):
    pass


class LLMDisabled(LLMError):
    """The "button" is off: AINEWS_LLM_ENABLED is not true."""


class BudgetExceeded(LLMError):
    """A call was refused because its worst-case cost would break a spending cap."""


def worst_case_usd(model: str, prompt_chars: int, max_tokens: int) -> float:
    """Upper bound for one call: ~3 characters per token (conservative), the whole prompt billed as a cache
    write (1.25x), and every allowed output token used (max_tokens includes thinking)."""
    pin, pout = price_for(model)
    return (prompt_chars / 3 * pin * CACHE_WRITE + max_tokens * pout) / 1_000_000


def spent_since(conn: sqlite3.Connection, since_iso: str) -> float:
    row = conn.execute("SELECT COALESCE(SUM(cost_usd), 0) AS s FROM llm_calls WHERE at >= ?", (since_iso,)).fetchone()
    return float(row["s"])


def price_for(model: str) -> tuple[float, float]:
    base = model.split("@", 1)[0]
    for name, p in PRICES.items():
        if base.startswith(name):
            return p
    return PRICES["claude-opus-5-5"]  # unknown model: assume the expensive one so cost is never understated


def cost_usd(model: str, usage) -> float:
    pin, pout = price_for(model)
    read = getattr(usage, "cache_read_input_tokens", 0) or 0
    write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    return (usage.input_tokens * pin + read * pin * CACHE_READ + write * pin * CACHE_WRITE
            + usage.output_tokens * pout) / 1_000_000


class LLM:
    """Thin wrapper so the pipeline can be tested with a fake that has the same .json() method."""

    def __init__(self, settings, conn: sqlite3.Connection, run_id: int | None = None, *,
                 ignore_switch: bool = False, client=None):
        if not settings.llm_enabled and not ignore_switch:
            raise LLMDisabled("model calls are switched off: set AINEWS_LLM_ENABLED=true in .env to turn them on "
                              "(or use --no-llm)")
        if not settings.gcp_project_id and client is None:
            raise LLMError("GCP_PROJECT_ID is not set (see .env.example)")
        if client is None:
            from anthropic import AnthropicVertex

            client = AnthropicVertex(region=settings.vertex_region, project_id=settings.gcp_project_id)
        self.client = client
        self.settings = settings
        self.conn, self.run_id = conn, run_id
        self.total_cost = 0.0

    def _guard(self, model: str, prompt_chars: int, max_tokens: int, purpose: str) -> None:
        """Refuse the call before it is made if the worst case would break any cap."""
        s = self.settings
        worst = worst_case_usd(model, prompt_chars, max_tokens)
        now_dt = datetime.now(timezone.utc)
        day = now_dt.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")
        month = now_dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")
        checks = [("run", self.total_cost, s.max_usd_per_run),
                  ("day", spent_since(self.conn, day), s.max_usd_per_day),
                  ("month", spent_since(self.conn, month), s.max_usd_per_month)]
        for name, spent, cap in checks:
            if spent + worst > cap:
                raise BudgetExceeded(f"{purpose}: refused, {name} spend ${spent:.2f} + worst case ${worst:.2f} "
                                     f"> cap ${cap:.2f} (AINEWS_MAX_USD_PER_{name.upper()})")

    def json(self, *, model: str, system: str, user: str, schema: dict, max_tokens: int,
             effort: str = "medium", purpose: str = "", stream: bool = False) -> dict:
        self._guard(model, len(system) + len(user), max_tokens, purpose)
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
        self._log(model, msg.usage, purpose)
        if msg.stop_reason == "refusal":
            raise LLMError(f"{purpose}: model refused")
        if msg.stop_reason == "max_tokens":
            raise LLMError(f"{purpose}: output hit max_tokens={max_tokens}")
        text = next((b.text for b in msg.content if b.type == "text"), None)
        if text is None:
            raise LLMError(f"{purpose}: no text block in response")
        return json.loads(text)

    def _log(self, model: str, usage, purpose: str) -> None:
        cost = cost_usd(model, usage)
        self.total_cost += cost
        log.info("%s %s: in=%d out=%d cache_read=%d $%.4f", purpose, model, usage.input_tokens,
                 usage.output_tokens, getattr(usage, "cache_read_input_tokens", 0) or 0, cost)
        self.conn.execute(
            "INSERT INTO llm_calls (run_id, at, purpose, model, input_tokens, output_tokens,"
            " cache_read_tokens, cache_write_tokens, cost_usd) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (self.run_id, now(), purpose, model, usage.input_tokens, usage.output_tokens,
             getattr(usage, "cache_read_input_tokens", 0) or 0,
             getattr(usage, "cache_creation_input_tokens", 0) or 0, cost),
        )
        self.conn.commit()
