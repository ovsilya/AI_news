"""Claude on Vertex AI: one JSON-returning call with usage and cost logging."""
from __future__ import annotations

import json
import logging
import sqlite3

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

    def __init__(self, settings, conn: sqlite3.Connection | None = None, run_id: int | None = None):
        if not settings.gcp_project_id:
            raise LLMError("GCP_PROJECT_ID is not set (see .env.example)")
        from anthropic import AnthropicVertex

        self.client = AnthropicVertex(region=settings.vertex_region, project_id=settings.gcp_project_id)
        self.conn, self.run_id = conn, run_id
        self.total_cost = 0.0

    def json(self, *, model: str, system: str, user: str, schema: dict, max_tokens: int,
             effort: str = "medium", purpose: str = "", stream: bool = False) -> dict:
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
        if self.conn is not None:
            self.conn.execute(
                "INSERT INTO llm_calls (run_id, at, purpose, model, input_tokens, output_tokens,"
                " cache_read_tokens, cache_write_tokens, cost_usd) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (self.run_id, now(), purpose, model, usage.input_tokens, usage.output_tokens,
                 getattr(usage, "cache_read_input_tokens", 0) or 0,
                 getattr(usage, "cache_creation_input_tokens", 0) or 0, cost),
            )
            self.conn.commit()
