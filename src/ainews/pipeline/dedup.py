"""Group items into stories: same canonical URL, or near-duplicate text (MinHash + LSH).

Embedding-based dedup (paraphrased headlines about the same event) comes in Phase 2.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
from array import array
from collections import defaultdict
from datetime import datetime, timedelta, timezone

NUM_PERM = 64
BANDS, ROWS = 16, 4            # 16 x 4 = 64; candidate threshold ≈ (1/16)^(1/4) ≈ 0.5
THRESHOLD = 0.6                # estimated Jaccard needed to call two items the same story
WINDOW_HOURS = 72
_PRIME = (1 << 61) - 1
_rng = [int.from_bytes(hashlib.sha256(f"perm{i}".encode()).digest()[:16], "big") for i in range(NUM_PERM)]
_PERMS = [((x >> 64) % (_PRIME - 1) + 1, (x & ((1 << 64) - 1)) % _PRIME) for x in _rng]
_NON_WORD = re.compile(r"[^a-z0-9 ]+")


def shingles(text: str, k: int = 5) -> set[str]:
    t = " ".join(_NON_WORD.sub(" ", text.lower()).split())
    if len(t) <= k:
        return {t} if t else set()
    return {t[i:i + k] for i in range(len(t) - k + 1)}


def minhash(text: str) -> bytes:
    hashes = [int.from_bytes(hashlib.blake2b(s.encode(), digest_size=8).digest(), "big") for s in shingles(text)]
    if not hashes:
        return array("Q", [0] * NUM_PERM).tobytes()
    sig = array("Q", ((min((a * h + b) % _PRIME for h in hashes)) for a, b in _PERMS))
    return sig.tobytes()


def similarity(a: bytes, b: bytes) -> float:
    x, y = array("Q"), array("Q")
    x.frombytes(a)
    y.frombytes(b)
    return sum(1 for i, j in zip(x, y) if i == j) / NUM_PERM


def _bands(sig: bytes) -> list[bytes]:
    step = ROWS * 8
    return [bytes([i]) + sig[i * step:(i + 1) * step] for i in range(BANDS)]


def dedup_text(title: str, summary: str) -> str:
    return f"{title} {summary[:200]}"


def assign_stories(conn: sqlite3.Connection, layers: dict[str, str], now: datetime | None = None) -> dict:
    """Give every item without a story a story_id. layers maps source_id -> layer."""
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(hours=WINDOW_HOURS)).isoformat(timespec="seconds")

    by_url: dict[str, int] = {}
    buckets: dict[bytes, list[tuple[int, bytes]]] = defaultdict(list)
    for row in conn.execute(
        "SELECT canonical_url, story_id, minhash FROM items WHERE story_id IS NOT NULL AND fetched_at >= ?", (since,)
    ):
        by_url.setdefault(row["canonical_url"], row["story_id"])
        if row["minhash"]:
            for band in _bands(row["minhash"]):
                buckets[band].append((row["story_id"], row["minhash"]))

    stats = {"new_stories": 0, "by_url": 0, "by_text": 0}
    pending = conn.execute(
        "SELECT id, source_id, canonical_url, title, summary, fetched_at FROM items WHERE story_id IS NULL ORDER BY id"
    ).fetchall()
    for it in pending:
        sig = minhash(dedup_text(it["title"], it["summary"] or ""))
        story = by_url.get(it["canonical_url"])
        if story:
            stats["by_url"] += 1
        else:
            best, best_sim = None, 0.0
            for band in _bands(sig):
                for sid, other in buckets.get(band, ()):
                    s = similarity(sig, other)
                    if s > best_sim:
                        best, best_sim = sid, s
            if best is not None and best_sim >= THRESHOLD:
                story = best
                stats["by_text"] += 1
        if story is None:
            story = conn.execute(
                "INSERT INTO stories (title, layer, first_seen, last_seen) VALUES (?, ?, ?, ?)",
                (it["title"], layers.get(it["source_id"]), it["fetched_at"], it["fetched_at"]),
            ).lastrowid
            stats["new_stories"] += 1
        else:
            conn.execute("UPDATE stories SET last_seen = MAX(last_seen, ?) WHERE id = ?", (it["fetched_at"], story))
        conn.execute("UPDATE items SET story_id = ?, minhash = ? WHERE id = ?", (story, sig, it["id"]))
        by_url.setdefault(it["canonical_url"], story)
        for band in _bands(sig):
            buckets[band].append((story, sig))
    conn.commit()
    return stats
