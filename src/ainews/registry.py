"""Source registry: config/sources.yaml as typed objects."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

KINDS = {"rss", "github_releases", "changelog", "hf_papers", "hn", "openrouter"}
LAYERS = {"big_picture", "models", "tools", "research", "systems", "community"}


@dataclass(frozen=True)
class Source:
    id: str
    name: str
    kind: str
    layer: str
    url: str = ""
    weight: float = 1.0
    role: str = "primary"
    expect_every: float = 7
    keywords: tuple[str, ...] = ()
    enabled: bool = True
    repos: tuple[str, ...] = ()
    min_points: int = 100
    min_upvotes: int = 10
    repo_url: str = ""
    _patterns: tuple[re.Pattern, ...] = field(default=(), repr=False, compare=False)

    def matches(self, text: str) -> bool:
        """True if there is no keyword filter or any keyword occurs in text."""
        if not self.keywords:
            return True
        return any(p.search(text) for p in self._patterns)


def _compile(keywords: tuple[str, ...]) -> tuple[re.Pattern, ...]:
    out = []
    for kw in keywords:
        k = kw.strip()
        if k.isupper() and len(k) <= 4:  # acronyms such as AI, LLM, GPU: whole word, case-sensitive
            out.append(re.compile(rf"\b{re.escape(k)}\b"))
        else:
            out.append(re.compile(re.escape(k), re.IGNORECASE))
    return tuple(out)


def load_sources(path: Path, include_disabled: bool = False) -> list[Source]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    sources, seen = [], set()
    for row in raw["sources"]:
        row = dict(row)
        for key in ("keywords", "repos"):
            row[key] = tuple(dict.fromkeys(k.strip() for k in row.get(key, ())))
        src = Source(**row)
        if src.kind not in KINDS:
            raise ValueError(f"{src.id}: unknown kind {src.kind!r}")
        if src.layer not in LAYERS:
            raise ValueError(f"{src.id}: unknown layer {src.layer!r}")
        if src.id in seen:
            raise ValueError(f"duplicate source id {src.id!r}")
        seen.add(src.id)
        object.__setattr__(src, "_patterns", _compile(src.keywords))
        if src.enabled or include_disabled:
            sources.append(src)
    return sources
