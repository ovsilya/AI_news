"""Command line: `ainews collect | brief | run | health | sources | check`."""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone

from . import db
from .config import Settings
from .registry import load_sources

log = logging.getLogger("ainews")


def _sources(settings, only: str | None):
    sources = load_sources(settings.sources_path)
    if only:
        wanted = set(only.split(","))
        sources = [s for s in sources if s.id in wanted]
        missing = wanted - {s.id for s in sources}
        if missing:
            sys.exit(f"unknown or disabled source ids: {', '.join(sorted(missing))}")
    return sources


def cmd_collect(args, settings) -> dict:
    from .http import Fetcher
    from .pipeline.ingest import collect_all

    conn = db.connect(settings.db_path)
    run_id = db.start_run(conn, "collect")
    fetcher = Fetcher(conn)
    try:
        stats = collect_all(conn, _sources(settings, args.only), fetcher, settings)
    finally:
        fetcher.close()
    db.finish_run(conn, run_id, "ok", stats)
    print(f"collected {stats['new_items']} new items from {stats['sources']} sources ({stats['errors']} errors)")
    return stats


def cmd_brief(args, settings) -> None:
    from .pipeline.brief import build_brief, mark_sent

    conn = db.connect(settings.db_path)
    sources = load_sources(settings.sources_path)
    llm = None
    if not args.no_llm:
        from .llm import LLM

        llm = LLM(settings, conn)
    result = build_brief(conn, settings, sources, llm=llm, edition=args.edition)

    settings.out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d")
    path = settings.out_dir / f"brief-{stamp}-{args.edition}.md"
    path.write_text(result["markdown"], encoding="utf-8")
    print(f"brief #{result['brief_id']}: {result['stats']}")
    print(f"wrote {path}")
    if result["warnings"]:
        print(f"{len(result['warnings'])} verification warnings (see log with -v)")

    if args.send:
        from .deliver.telegram import Telegram

        ids = Telegram(settings.telegram_bot_token, settings.telegram_chat_id).send_messages(result["telegram"])
        mark_sent(conn, result["brief_id"], ids)
        print(f"sent {len(ids)} Telegram messages")


def cmd_run(args, settings) -> None:
    args.only = None
    cmd_collect(args, settings)
    cmd_brief(args, settings)


def cmd_health(args, settings) -> None:
    conn = db.connect(settings.db_path)
    rows = {r["source_id"]: dict(r) for r in conn.execute("SELECT * FROM source_health")}
    now = datetime.now(timezone.utc)
    problems = 0
    print(f"{'source':<18} {'status':<10} {'last new item':<22} note")
    for s in load_sources(settings.sources_path):
        h = rows.get(s.id)
        if not h:
            print(f"{s.id:<18} {'never run':<10}")
            continue
        note = ""
        if h["last_status"].startswith("error"):
            note = h["last_status"][:70]
        else:
            # Measure silence from the last new item, or from the first run if nothing has arrived yet.
            last = datetime.fromisoformat(h["last_new_at"] or h["first_run_at"])
            if last < now - timedelta(days=3 * s.expect_every):
                note = f"quiet: nothing new for over {3 * s.expect_every:g} days"
        problems += bool(note)
        status = "error" if h["last_status"].startswith("error") else "ok"
        print(f"{s.id:<18} {status:<10} {(h['last_new_at'] or '-'):<22} {note}")
    print(f"\n{problems} sources need attention")


def cmd_sources(args, settings) -> None:
    for s in load_sources(settings.sources_path, include_disabled=True):
        flag = "" if s.enabled else "  (disabled)"
        print(f"{s.id:<18} {s.kind:<16} {s.layer:<12} {s.name}{flag}")


def cmd_check(args, settings) -> None:
    """Checks the configuration with the cheapest possible calls."""
    ok = True
    print(f"profile: {settings.profile_path if settings.profile_path.exists() else 'config/profile.example.md (no private profile)'}")
    try:
        from .llm import LLM

        llm = LLM(settings)
        schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"],
                  "additionalProperties": False}
        for model in (settings.model_judge, settings.model_editor):
            out = llm.json(model=model, system="Reply with ok=true.", user="ping", schema=schema,
                           max_tokens=2000, effort="low", purpose="check")
            print(f"vertex {model}: ok={out.get('ok')}")
        print(f"vertex check cost: ${llm.total_cost:.4f}")
    except Exception as exc:
        ok = False
        print(f"vertex: FAILED: {type(exc).__name__}: {exc}")
    try:
        from .deliver.telegram import Telegram

        me = Telegram(settings.telegram_bot_token, settings.telegram_chat_id).get_me()
        print(f"telegram: ok, bot @{me.get('username')}")
    except Exception as exc:
        ok = False
        print(f"telegram: FAILED: {exc}")
    sys.exit(0 if ok else 1)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="ainews", description=__doc__)
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("collect", help="fetch all sources and store new items")
    c.add_argument("--only", help="comma-separated source ids")
    for name, help_ in (("brief", "build a brief from stored items"), ("run", "collect, then build a brief")):
        b = sub.add_parser(name, help=help_)
        b.add_argument("--edition", choices=["am", "pm"], default="am")
        b.add_argument("--no-llm", action="store_true", help="rank by heat only; no Vertex calls, no cost")
        b.add_argument("--send", action="store_true", help="post to the Telegram channel (default: local file only)")
    sub.add_parser("health", help="source health report")
    sub.add_parser("sources", help="list the source registry")
    sub.add_parser("check", help="verify Vertex AI and Telegram configuration")

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = Settings.from_env()
    {"collect": cmd_collect, "brief": cmd_brief, "run": cmd_run, "health": cmd_health,
     "sources": cmd_sources, "check": cmd_check}[args.cmd](args, settings)


if __name__ == "__main__":
    main()
