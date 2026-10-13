"""URL canonicalisation: the first dedup pass."""
from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING = {"ref", "ref_src", "source", "fbclid", "gclid", "mc_cid", "mc_eid", "cmpid", "smid", "taid",
            "src", "s", "share", "cid", "sr_share", "igshid", "si"}
ARXIV_ID = re.compile(r"(\d{4}\.\d{4,5})(v\d+)?")


def canonical_url(url: str) -> str:
    url = url.strip()
    parts = urlsplit(url)
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if host.endswith(":80") or host.endswith(":443"):
        host = host.rsplit(":", 1)[0]
    path = parts.path or "/"

    # One ID per paper: arxiv abs/pdf/vN and HF paper pages all map to arxiv.org/abs/<id>.
    if host in {"arxiv.org", "export.arxiv.org"} or (host == "huggingface.co" and path.startswith("/papers/")):
        m = ARXIV_ID.search(path)
        if m:
            return f"https://arxiv.org/abs/{m.group(1)}"

    if len(path) > 1:
        path = path.rstrip("/")
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False)
             if not k.lower().startswith("utm_") and k.lower() not in TRACKING]
    return urlunsplit(("https", host, path, urlencode(sorted(query)), ""))
