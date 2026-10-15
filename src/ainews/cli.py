"""Command line: `ainews collect | brief | run | send | jobs | jobs-probe | openings | weekly | health | sources | check |
budget | tg-chats | tg-test | tg-clean | state`."""
from __future__ import annotations

import argparse
import html
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
    if stats["baseline"]:
        print(f"new sources, first fetch remembered as a baseline (their posts come in from the next run):"
              f" {', '.join(stats['baseline'])}")
    return stats


def cmd_brief(args, settings) -> None:
    from .pipeline.brief import build_brief, drop_latest_brief, mark_sent

    conn = db.connect(settings.db_path)
    sources = load_sources(settings.sources_path)
    if args.redo:
        dropped = drop_latest_brief(conn)
        print(f"redo: replacing brief #{dropped}" if dropped else "redo: no earlier brief to replace")
    llm = None
    if not args.no_llm:
        from .llm import LLMDisabled, make_llm

        try:
            llm = make_llm(settings, conn)
        except LLMDisabled as exc:
            sys.exit(f"ainews: {exc}")
    fetcher = None
    if llm is not None:
        from .http import Fetcher

        fetcher = Fetcher(conn)  # article text for thin stories
    try:
        result = build_brief(conn, settings, sources, llm=llm, edition=args.edition, fetcher=fetcher)
    finally:
        if fetcher is not None:
            fetcher.close()

    settings.out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d")
    path = settings.out_dir / f"brief-{stamp}-{args.edition}.md"
    path.write_text(result["markdown"], encoding="utf-8")
    print(f"brief #{result['brief_id']}: {result['stats']}")
    print(f"wrote {path}")
    if result["warnings"]:
        print(f"{len(result['warnings'])} verification warning(s):")
        for w in result["warnings"]:
            print(f"  - {w}")

    if args.send:
        from .deliver.telegram import Telegram

        tg = Telegram(settings.telegram_bot_token, settings.telegram_chat_id)
        speech = (None if args.no_voice or args.no_llm or settings.voice != "brief" or not result["speech"]
                  else _read_aloud(result, settings, conn, path.with_suffix(".ogg")))
        ids = []
        if speech is not None:   # first, so it is what you see when you open the channel
            minutes = max(1, round(speech.seconds / 60))
            try:
                ids.append(tg.send_voice(speech.ogg, caption=f"🎧 <b>{html.escape(result['title'])}</b> · {minutes} min",
                                         seconds=speech.seconds))
            except Exception as exc:
                print(f"voice message not sent: {exc}")
        voiced = len(ids)
        ids += tg.send_messages(result["telegram"], notify=not voiced)
        mark_sent(conn, result["brief_id"], ids)
        print(f"sent {len(ids) - voiced} Telegram messages" + (" and the voice message" if voiced else ""))


def _read_aloud(result: dict, settings, conn, path):
    """The brief's audio overview (deliver/voice.py), also written to path; None, with the reason printed, if
    it can't be made: the text brief goes out regardless."""
    from .deliver.voice import Speaker

    try:
        speech = Speaker(settings, conn).read(result["speech"])
    except Exception as exc:
        print(f"no voice message: {exc}")
        return None
    path.write_bytes(speech.ogg)
    print(f"voice: {speech.seconds / 60:.1f} min, {len(speech.ogg) / 1e6:.1f} MB, ${speech.cost_usd:.3f}; wrote {path}")
    return speech


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
        from .llm import make_llm

        llm = make_llm(settings, db.connect(settings.db_path), ignore_switch=True)
        print(f"provider: {settings.llm_provider} · project: {settings.gcp_project_id} · region: {settings.vertex_region}")
        schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"],
                  "additionalProperties": False}
        # Same thinking levels as production (judge: low, editor: medium), so a run won't fail on them later.
        for role, model, effort in (("judge", settings.model_judge, "low"), ("editor", settings.model_editor, "medium")):
            out = llm.json(model=model, system="Reply with ok=true.", user="ping", schema=schema,
                           max_tokens=2000, effort=effort, purpose="check")
            print(f"vertex {role} {model} (thinking {effort}): ok={out.get('ok')}")
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
    from .llm import period_start, spent_since

    conn = db.connect(settings.db_path)
    month = period_start(month=True)
    print(f"model calls: {'ON' if settings.llm_enabled else 'OFF'} (AINEWS_LLM_ENABLED)")
    print(f"today:      ${spent_since(conn, period_start()):.4f} of ${settings.max_usd_per_day:.2f}"
          f" (since local midnight; the jobs lane leaves ${settings.brief_reserve_usd:.2f} for the brief)")
    print(f"this month: ${spent_since(conn, month):.4f} of ${settings.max_usd_per_month:.2f}")
    print(f"per run cap: ${settings.max_usd_per_run:.2f} · judge cap: {settings.max_judge_stories} stories")
    print(f"voice:      ${spent_since(conn, month, voice=True):.4f} of ${settings.voice_max_usd_per_month:.2f} this month,"
          f" at most ${settings.voice_max_usd_per_brief:.2f} a brief (AINEWS_VOICE={settings.voice}; not in the caps above)")
    for r in conn.execute("SELECT purpose, model, COUNT(*) n, SUM(cost_usd) c FROM llm_calls"
                          " WHERE at >= ? GROUP BY 1, 2 ORDER BY 4 DESC", (month,)):
        print(f"  {r['purpose']:<8} {r['model']:<22} {r['n']:>4} calls  ${r['c']:.4f}")


def cmd_jobs(args, settings) -> None:
    """Lane ②: poll the employers' job boards, then let the model read new and changed postings."""
    from .http import Fetcher
    from .jobs.boards import load_companies
    from .jobs.poll import poll_all

    conn = db.connect(settings.db_path)
    companies = load_companies(settings.companies_path)
    if args.only:
        wanted = set(args.only.split(","))
        companies = [c for c in companies if c.id in wanted]
    run_id = db.start_run(conn, "jobs")
    fetcher = Fetcher(conn, timeout=60)   # some boards return 10+ MB
    try:
        stats = poll_all(conn, companies, fetcher)
    finally:
        fetcher.close()
    print(f"polled {stats['boards']} boards ({stats['errors']} errors): {stats['listed']} postings listed, "
          f"{stats['matching']} in your target roles and markets; {stats['new']} new, {stats['changed']} changed, "
          f"{stats['closed']} closed" + (f"; {stats['waiting']} Workday postings wait for the next run"
                                         if stats.get("waiting") else ""))
    if not args.no_llm:
        from .jobs.extract import Skills, extract_postings
        from .llm import LLMDisabled, make_llm

        try:
            llm = make_llm(settings, conn, run_id, reserve=settings.brief_reserve_usd)   # runs before the brief
        except LLMDisabled as exc:
            print(f"extraction skipped: {exc}")
        else:
            limit = args.max_extract or settings.max_jobs_extract
            stats["extract"] = extract_postings(conn, llm, model=settings.model_judge, prompts_dir=settings.prompts_dir,
                                                skills=Skills(settings.skills_path), limit=limit)
            left = conn.execute("SELECT COUNT(*) FROM job_postings p LEFT JOIN job_extractions e"
                                " ON e.posting_id = p.id WHERE p.closed_at IS NULL AND e.posting_id IS NULL").fetchone()[0]
            ex = stats["extract"]
            print(f"extracted {ex['extracted']} postings ({ex['failed']} failed), {left} still to read;"
                  f" cost ${llm.total_cost:.4f}" + (f"; stopped: {ex['budget_stop']}" if ex.get("budget_stop") else ""))
    db.finish_run(conn, run_id, "ok", stats)
    if args.send_openings:   # the deployed daily run: the day's openings go out once the new postings are read
        cmd_openings(argparse.Namespace(send=True, redo=False, tier=[]), settings)


def cmd_jobs_probe(args, settings) -> None:
    """Find which job board an employer uses: prints a companies.yaml line for each hit, with a tier to check."""
    from .http import Fetcher
    from .jobs.boards import probe

    fetcher = Fetcher(None, timeout=60, retries=1)
    if args.workday:
        from .jobs import workday

        try:
            for tenant in args.slugs:
                sites = workday.probe(tenant, fetcher)
                if not sites:
                    print(f"# {tenant}: no Workday career site that robots.txt allows")
                name = tenant.split(".")[0]
                for board, n in sites:
                    site = board.split("/", 1)[1]
                    cid = name if len(sites) == 1 else f"{name}-{site.lower()}"
                    print(f'  - {{id: {cid}, name: {name}, ats: workday, board: "{board}", ai_native: false,'
                          f' tier: established}}   # {n} open; check the tier')
        finally:
            fetcher.close()
        return
    try:
        for slug in args.slugs:
            hits = probe(slug, fetcher)
            if not hits:
                print(f"# {slug}: no Greenhouse, Lever or Ashby board")
            for ats, n in hits:
                region = ", region: eu" if ats == "lever-eu" else ""
                print(f"  - {{id: {slug}, name: {slug}, ats: {ats.split('-')[0]}, board: {slug}{region}, tier: startup}}"
                      f"   # {n} open; check the tier")
    finally:
        fetcher.close()


def cmd_weekly(args, settings) -> None:
    """Lane ③: the weekly jobs report (skills demand, pay, who's hiring, your fit)."""
    import json

    from .jobs.report import build_weekly

    conn = db.connect(settings.db_path)
    llm = None
    if not args.no_llm:
        from .llm import LLMDisabled, make_llm

        try:
            llm = make_llm(settings, conn)
        except LLMDisabled as exc:
            sys.exit(f"ainews: {exc}")
    result = build_weekly(conn, settings, llm=llm)
    settings.out_dir.mkdir(parents=True, exist_ok=True)
    path = settings.out_dir / f"jobs-{datetime.now():%Y-%m-%d}.md"
    path.write_text(result["markdown"], encoding="utf-8")
    print(f"report #{result['report_id']}: {result['stats']['target_open']} open target postings · wrote {path}")
    for w in result["warnings"]:
        print(f"  - {w}")
    if args.send:
        from .deliver.telegram import Telegram

        ids = Telegram(settings.telegram_bot_token, settings.telegram_chat_id).send_messages(result["telegram"])
        conn.execute("UPDATE reports SET telegram_message_ids = ? WHERE id = ?", (json.dumps(ids), result["report_id"]))
        conn.commit()
        print(f"sent {len(ids)} Telegram messages")


def cmd_openings(args, settings) -> None:
    """Lane ②: this week's new postings that fit you best (local file; --send posts them, each once)."""
    from .jobs.boards import TIERS
    from .jobs.openings import TIER_LABELS, build_openings, mark_sent

    per_tier = {}
    for item in args.tier:
        name, _, n = item.partition("=")
        if name not in TIERS or not n.isdigit():
            sys.exit(f"ainews: --tier wants GROUP=N, GROUP one of {', '.join(TIERS)} (e.g. --tier top=0), not {item!r}")
        per_tier[name] = int(n)
    conn = db.connect(settings.db_path)
    result = build_openings(conn, settings, per_tier=per_tier, redo=args.redo)
    if args.redo:
        print(f"redo: the postings of openings #{result['redone']} are candidates again" if result["redone"]
              else "redo: no sent openings to rebuild")
    wait = f"; {result['unread']} more new postings wait for the model to read them" if result["unread"] else ""
    if not result["report_id"]:
        print(f"no new openings above the fit bar: {result['considered']} new postings in your roles and markets{wait}")
        return
    settings.out_dir.mkdir(parents=True, exist_ok=True)
    path = settings.out_dir / f"openings-{datetime.now():%Y-%m-%d}.md"
    path.write_text(result["markdown"], encoding="utf-8")
    print(f"openings #{result['report_id']}: {len(result['picked'])} of {result['considered']} new postings{wait}"
          f" · wrote {path}")
    for g in result["picked"]:
        print(f"  {g['fit']:>3}  {TIER_LABELS[g['tier']]:<14} {g['title']} · {g['company']}")
    if not result["has_profile"]:
        print("  (no private/skills.yaml: ranked by seniority only)")
    if args.send:
        from .deliver.telegram import Telegram

        ids = Telegram(settings.telegram_bot_token, settings.telegram_chat_id).send_messages(result["telegram"])
        n = mark_sent(conn, result["report_id"], ids)
        print(f"sent {len(ids)} Telegram messages; {n} postings won't be sent again")


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


def cmd_send(args, settings) -> None:
    """Post the latest stored brief, e.g. after reading it in out/ first."""
    import json

    from .deliver.telegram import Telegram
    from .pipeline.brief import mark_sent

    conn = db.connect(settings.db_path)
    kind, what, cmd = (("jobs_weekly", "jobs report", "weekly") if args.report else
                       ("jobs_openings", "openings", "openings") if args.openings else (None, "brief", "run"))
    if kind:
        row = conn.execute("SELECT id, telegram_html, telegram_message_ids FROM reports WHERE kind = ?"
                           " ORDER BY id DESC LIMIT 1", (kind,)).fetchone()
    else:
        row = conn.execute("SELECT id, telegram_html, telegram_message_ids FROM briefs ORDER BY id DESC LIMIT 1").fetchone()
    if row is None:
        sys.exit(f"ainews: no {what} yet; run `ainews {cmd}` first")
    if row["telegram_message_ids"] and not args.force:
        sys.exit(f"ainews: {what} #{row['id']} was already sent; use --force to send it again")
    ids = Telegram(settings.telegram_bot_token, settings.telegram_chat_id).send_messages(json.loads(row["telegram_html"]))
    if args.openings:
        from .jobs.openings import mark_sent as mark_openings_sent

        mark_openings_sent(conn, row["id"], ids)
    elif args.report:
        conn.execute("UPDATE reports SET telegram_message_ids = ? WHERE id = ?", (json.dumps(ids), row["id"]))
        conn.commit()
    else:
        mark_sent(conn, row["id"], ids)
    print(f"sent {what} #{row['id']} as {len(ids)} Telegram messages")


def cmd_tg_clean(args, settings) -> None:
    """Delete the channel's earlier posts (test runs, old tries), keeping the latest sent brief, jobs report and
    openings: every message older than the oldest kept post, and any post we sent after it that a newer one
    replaced (say, openings sent again with --redo)."""
    import json

    from .deliver.telegram import Telegram

    conn = db.connect(settings.db_path)
    kept, kept_ids, sent_ids = [], set(), set()
    for table, kind in (("briefs", None), ("reports", "jobs_weekly"), ("reports", "jobs_openings")):
        where = " AND kind = ?" if kind else ""
        row = conn.execute(f"SELECT telegram_message_ids FROM {table} WHERE telegram_message_ids IS NOT NULL{where}"
                           " ORDER BY id DESC LIMIT 1", (kind,) if kind else ()).fetchone()
        if row and json.loads(row["telegram_message_ids"]):
            kept.append(min(json.loads(row["telegram_message_ids"])))
            kept_ids |= set(json.loads(row["telegram_message_ids"]))
    if not kept:
        sys.exit("ainews: no sent brief to keep; nothing deleted")
    for table in ("briefs", "reports"):
        for (ids,) in conn.execute(f"SELECT telegram_message_ids FROM {table} WHERE telegram_message_ids IS NOT NULL"):
            sent_ids |= set(json.loads(ids))
    sent_ids |= {r[0] for r in conn.execute("SELECT message_id FROM replaced_posts")}   # briefs a redo replaced
    first = min(kept)
    # Channel message ids count up from 1, so everything below the kept posts' first message is older. Above
    # it, only posts we sent and have since replaced go: never a message we have no record of sending.
    replaced = sorted(i for i in sent_ids - kept_ids if i >= first)
    if first <= 1 and not replaced:
        print("nothing older than the latest posts")
        return
    older = [f"1-{first - 1}"] if first > 1 else []
    if not args.yes:
        what = " and ".join(older + ([f"{', '.join(map(str, replaced))} (replaced by newer posts)"] if replaced else []))
        prompt = (f"Delete channel messages {what}, and keep the latest brief, jobs report and openings"
                  f" ({', '.join(str(i) for i in sorted(kept_ids))})?"
                  " This can't be undone. [y/N] ")
        try:
            answer = input(prompt)
        except EOFError:
            answer = ""
        if answer.strip().lower() not in ("y", "yes"):
            sys.exit("cancelled; nothing deleted")
    result = Telegram(settings.telegram_bot_token, settings.telegram_chat_id).delete_messages(
        list(range(1, first)) + replaced)
    print(f"deleted {len(result['deleted'])} messages ({len(result['missing'])} ids were already gone)")
    if result["refused"]:
        print(f"{len(result['refused'])} could not be deleted (ids {', '.join(map(str, result['refused']))}):"
              " the channel's own service messages, posts you wrote yourself, or posts older than 48 hours."
              " Remove any you don't want by hand in Telegram.")


def cmd_state(args, settings) -> None:
    """The cloud copy of the database and private files (see cloud.py): pull it to look at, or push yours."""
    from .cloud import bucket_for, copy_db, current_generation, pull, push, push_private

    name = args.bucket or settings.state_bucket or f"{settings.gcp_project_id}-ainews-state"
    bucket = bucket_for(settings, name)
    if args.action == "pull":
        if settings.db_path.exists():
            keep = settings.db_path.with_name(f"ainews.local-{datetime.now():%Y%m%d-%H%M%S}.sqlite")
            copy_db(settings.db_path, keep)   # with its WAL, which the pull removes
            print(f"kept your local database as {keep.name}")
        generation = pull(settings, bucket, private=args.private)
        print(f"pulled gs://{name}/ainews.sqlite (generation {generation})" if generation
              else f"gs://{name} holds no database yet")
        return
    if args.private:
        print(f"pushed private files: {', '.join(push_private(settings, bucket)) or 'none found'}")
    if not settings.db_path.exists():
        sys.exit(f"ainews: no local database at {settings.db_path}")
    held = current_generation(bucket)
    if held and not args.force:
        sys.exit(f"ainews: gs://{name} already holds a database, newer runs may be in it; pull it, or push with"
                 " --force to replace it (the bucket keeps the replaced version for 30 days)")
    print(f"pushed the database to gs://{name} (generation {push(settings, bucket, held)})")


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
        b.add_argument("--redo", action="store_true",
                       help="replace the latest brief: its stories can be used again (judge scores are reused)")
        b.add_argument("--no-voice", action="store_true", help="with --send: no voice message (AINEWS_VOICE)")
    sn = sub.add_parser("send", help="post the latest stored brief to Telegram")
    sn.add_argument("--force", action="store_true", help="send even if it was already sent")
    what = sn.add_mutually_exclusive_group()
    what.add_argument("--report", action="store_true", help="send the latest jobs report instead of the brief")
    what.add_argument("--openings", action="store_true", help="send the latest openings instead of the brief")
    j = sub.add_parser("jobs", help="poll the employers' job boards and extract new postings")
    j.add_argument("--only", help="comma-separated company ids")
    j.add_argument("--no-llm", action="store_true", help="poll and diff only; no model calls")
    j.add_argument("--max-extract", type=int, help="postings the model reads this run (default 200)")
    j.add_argument("--send-openings", action="store_true",
                   help="then build the day's openings and post them (what the deployed daily run does)")
    op = sub.add_parser("openings", help="this week's new postings that fit you best (local file; --send to post)")
    op.add_argument("--send", action="store_true", help="post to the Telegram channel; each posting is sent once")
    op.add_argument("--redo", action="store_true",
                    help="rebuild the latest sent openings (e.g. after a layout change); send them again with --send")
    op.add_argument("--tier", action="append", default=[], metavar="GROUP=N",
                    help="places for one employer group this run, e.g. --tier top=0 --tier startup=5"
                         " (groups: top, established, startup; defaults 2, 3, 3 or per_tier in private/openings.yaml)")
    jp = sub.add_parser("jobs-probe", help="check which job board (Greenhouse, Lever, Ashby, Workday) an employer uses")
    jp.add_argument("slugs", nargs="+", help="board slugs, or Workday tenants with --workday (e.g. nvidia or nvidia.wd5)")
    jp.add_argument("--workday", action="store_true", help="find the tenants' Workday career sites")
    w = sub.add_parser("weekly", help="build the weekly jobs report (local file; --send to post)")
    w.add_argument("--no-llm", action="store_true", help="tables only, no model commentary")
    w.add_argument("--send", action="store_true", help="post to the Telegram channel")
    sub.add_parser("health", help="source health report")
    sub.add_parser("sources", help="list the source registry")
    sub.add_parser("check", help="verify Vertex AI and Telegram configuration (works even when the switch is off)")
    sub.add_parser("budget", help="show the model switch, spend so far and the caps")
    sub.add_parser("tg-chats", help="list chats the bot has seen (to find TELEGRAM_CHAT_ID)")
    sub.add_parser("tg-test", help="send a test message to TELEGRAM_CHAT_ID")
    tc = sub.add_parser("tg-clean", help="delete the channel's earlier and replaced posts, keeping the latest brief,"
                                         " jobs report and openings")
    tc.add_argument("--yes", action="store_true", help="don't ask for confirmation")
    st = sub.add_parser("state", help="the cloud copy of the database: pull it here, or push yours (first deploy)")
    st.add_argument("action", choices=["pull", "push"])
    st.add_argument("--private", action="store_true", help="also your private files (profile, skills, openings)")
    st.add_argument("--force", action="store_true", help="push: replace the cloud database")
    st.add_argument("--bucket", help="default: AINEWS_STATE_BUCKET, else <project>-ainews-state")

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = Settings.from_env()
    from .deliver.telegram import TelegramError
    from .llm import LLMError

    synced = bool(settings.state_bucket) and args.cmd not in NO_STATE
    if synced:   # deployed: the database lives in the bucket between runs
        from .cloud import bucket_for, pull, push

        bucket = bucket_for(settings)
        generation = pull(settings, bucket)
    try:
        _dispatch(args, settings)
    except (TelegramError, LLMError) as exc:  # configuration problems: one clear line, no traceback
        sys.exit(f"ainews: {exc}")
    finally:
        if synced:   # also after a failure: what was read and paid for so far is kept
            push(settings, bucket, generation)


NO_STATE = {"state", "jobs-probe", "sources", "tg-chats", "tg-test"}   # commands that never touch the database


def _dispatch(args, settings) -> None:
    {"collect": cmd_collect, "brief": cmd_brief, "run": cmd_run, "health": cmd_health,
     "sources": cmd_sources, "check": cmd_check, "budget": cmd_budget, "tg-chats": cmd_tg_chats,
     "tg-test": cmd_tg_test, "tg-clean": cmd_tg_clean, "send": cmd_send, "jobs": cmd_jobs,
     "jobs-probe": cmd_jobs_probe, "weekly": cmd_weekly, "openings": cmd_openings,
     "state": cmd_state}[args.cmd](args, settings)


if __name__ == "__main__":
    main()
