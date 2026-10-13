"""OpenRouter /models diff: new models and price changes become items."""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from ..db import get_snapshot, set_snapshot
from . import RawItem, iso_from_epoch

SNAPSHOT = "openrouter:models"
NEW_WITHIN_DAYS = 3        # on the first run, report only models created this recently
PRICE_CHANGE = 0.10        # report price moves of 10% or more


def _per_million(value) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return None if v < 0 else round(v * 1_000_000, 4)


def _fmt_price(p: float | None) -> str:
    return "n/a" if p is None else f"${p:g}"


def summarise(models: list[dict]) -> dict:
    out = {}
    for m in models:
        pricing = m.get("pricing") or {}
        out[m["id"]] = {
            "name": m.get("name") or m["id"],
            "created": m.get("created"),
            "context": m.get("context_length"),
            "in": _per_million(pricing.get("prompt")),
            "out": _per_million(pricing.get("completion")),
        }
    return out


def diff(old: dict | None, new: dict, now_ts: float | None = None) -> list[RawItem]:
    now_ts = now_ts or time.time()
    today = datetime.fromtimestamp(now_ts, tz=timezone.utc).date().isoformat()
    items = []
    for mid, m in new.items():
        url = f"https://openrouter.ai/{mid}"
        ctx = f"{m['context'] // 1000}K context" if m.get("context") else "context n/a"
        price = f"{_fmt_price(m['in'])} in / {_fmt_price(m['out'])} out per 1M tokens"
        if old is None or mid not in old:
            created = m.get("created") or 0
            if old is None and now_ts - created > NEW_WITHIN_DAYS * 86400:
                continue
            items.append(RawItem(
                url=url, title=f"New on OpenRouter: {m['name']}",
                summary=f"{m['name']} ({mid}) is now listed on OpenRouter: {ctx}, {price}.",
                published_at=iso_from_epoch(created) if created else None,
            ))
            continue
        before = old[mid]
        changes = []
        for key, label in (("in", "input"), ("out", "output")):
            a, b = before.get(key), m.get(key)
            if a and b and abs(b - a) / a >= PRICE_CHANGE:
                changes.append(f"{label} {_fmt_price(a)} → {_fmt_price(b)}")
        if changes:
            items.append(RawItem(
                url=f"{url}?price-change={today}", title=f"OpenRouter price change: {m['name']}",
                summary=f"{m['name']} ({mid}) price per 1M tokens changed: {', '.join(changes)}.",
            ))
    return items


def collect(source, fetcher, conn, settings) -> list[RawItem]:
    r = fetcher.get(source.url, conditional=False)
    new = summarise(json.loads(r.text).get("data", []))
    items = diff(get_snapshot(conn, SNAPSHOT), new)
    set_snapshot(conn, SNAPSHOT, new)
    return items
