"""Command line: `ainews collect | brief | run | health | sources | check | budget | tg-chats | tg-test`."""
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
        from .llm import LLM, LLMDisabled

        try:
            llm = LLM(settings, conn)
        except LLMDisabled as exc:
            sys.exit(f"ainews: {exc}")
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

        llm = LLM(settings, db.connect(settings.db_path), ignore_switch=True)
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


def cmd_budget(args, settings) -> None:
    from .llm import spent_since

    conn = db.connect(settings.db_path)
    now = datetime.now(timezone.utc)
    day = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")
    month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")
    print(f"model calls: {'ON' if settings.llm_enabled else 'OFF'} (AINEWS_LLM_ENABLED)")
    print(f"today:      ${spent_since(conn, day):.4f} of ${settings.max_usd_per_day:.2f}")
    print(f"this month: ${spent_since(conn, month):.4f} of ${settings.max_usd_per_month:.2f}")
    print(f"per run cap: ${settings.max_usd_per_run:.2f} · judge cap: {settings.max_judge_stories} stories")
    for r in conn.execute("SELECT purpose, model, COUNT(*) n, SUM(cost_usd) c FROM llm_calls"
                          " WHERE at >= ? GROUP BY 1, 2 ORDER BY 4 DESC", (month,)):
        print(f"  {r['purpose']:<8} {r['model']:<22} {r['n']:>4} calls  ${r['c']:.4f}")


def cmd_tg_chats(args, settings) -> None:
    """Lists chats the bot has seen recently, to find the channel's chat ID."""
    from .deliver.telegram import Telegram

    tg = Telegram(settings.telegram_bot_token, settings.telegram_chat_id or "unset")
    chats = {}
    for u in tg.call("getUpdates", {"allowed_updates": ["channel_post", "message", "my_chat_member"]}):
        for key in ("channel_post", "message", "my_chat_member"):
            chat = (u.get(key) or {}).get("chat")
            if chat:
                chats[chat["id"]] = chat
    if not chats:
        print("No chats seen yet. Add the bot to the channel as an admin, post any message there, then rerun.")
    for c in chats.values():
        print(f"{c['id']:>16}  {c.get('type', ''):<10} {c.get('title') or c.get('username') or ''}")


def cmd_tg_test(args, settings) -> None:
    from .deliver.telegram import Telegram

    tg = Telegram(settings.telegram_bot_token, settings.telegram_chat_id)
    ids = tg.send_messages(["<b>AI_news test</b>\nThe bot can post to this channel. ✅"])
    print(f"sent test message {ids[0]} to {settings.telegram_chat_id}")


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
    sub.add_parser("check", help="verify Vertex AI and Telegram configuration (works even when the switch is off)")
    sub.add_parser("budget", help="show the model switch, spend so far and the caps")
    sub.add_parser("tg-chats", help="list chats the bot has seen (to find TELEGRAM_CHAT_ID)")
    sub.add_parser("tg-test", help="send a test message to TELEGRAM_CHAT_ID")

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = Settings.from_env()
    {"collect": cmd_collect, "brief": cmd_brief, "run": cmd_run, "health": cmd_health,
     "sources": cmd_sources, "check": cmd_check, "budget": cmd_budget, "tg-chats": cmd_tg_chats,
     "tg-test": cmd_tg_test}[args.cmd](args, settings)


if __name__ == "__main__":
    main()
