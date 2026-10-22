from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .actions import calendar as calendar_action
from . import languages, llm, private_files, run_lock
from . import run as evening
from . import sources as source_registry
from .collectors.base import Message
from .config import Config
from .state import State
from .brief import to_archive
from .summarize import for_the_ai, summarize


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("family_brief")


def cmd_run(args: argparse.Namespace) -> int:
    cfg = Config.load(args.config)
    options = evening.Options(dry_run=args.dry_run or args.preview, preview=args.preview,
                              sources=args.sources.split(",") if args.sources else None,
                              lookback_hours=args.lookback_hours, if_missed=args.if_missed)
    io = evening.IO.real()
    if options.dry_run:  # writes nothing, so it can't clash with a run that is going
        return evening.run_unless_caught_up(cfg, options, io)
    return run_lock.run_alone(cfg, "Brief", lambda: evening.run_unless_caught_up(cfg, options, io))


def cmd_collect(args: argparse.Namespace) -> int:
    """Print collected messages + events as JSON (debug; no state write)."""
    cfg = Config.load(args.config)
    state = State(cfg.resolved_state_path())
    messages, events = evening.collect(cfg, state, args.source.split(","))
    print(json.dumps({
        "messages": [m.to_dict() for m in messages],
        "events": [e.to_dict() for e in events],
    }, ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_sync_attendees(args: argparse.Namespace) -> int:
    cfg = Config.load(args.config)
    result = calendar_action.sync_attendees(cfg)
    log.info("Sync complete: scanned=%d patched=%d", result["scanned"], result["patched"])
    return 0


def cmd_weekend_events(args: argparse.Namespace) -> int:
    from . import weekend_pipeline
    cfg = Config.load(args.config)
    private_files.tighten(cfg)

    def run() -> int:
        evening.wait_for_network(probe_host=llm.backend(cfg).probe_host)
        return weekend_pipeline.run(cfg, dry_run=args.dry_run)
    return run() if args.dry_run else run_lock.run_alone(cfg, "Weekend Picks", run)


def cmd_language(args: argparse.Namespace) -> int:
    """Get a language's program text ready ahead of the first night that needs it."""
    cfg = Config.load(args.config)
    try:
        for table in languages.TABLES:  # the Brief's and Weekend Picks' text
            languages.prepare(cfg, args.language, table)
    except Exception as e:
        log.error("Couldn't translate the program's Brief text into %s: %s", args.language, e)
        return 1
    print(f"{args.language}: {languages.describe(cfg, args.language)[1]}")
    return 0


def cmd_summarize(args: argparse.Namespace) -> int:
    """Summarize messages from an archive dump."""
    cfg = Config.load(args.config)
    raw = json.loads(Path(args.input).read_text(encoding="utf-8"))
    msgs = [Message(
        source=m["source"], external_id=m["external_id"],
        timestamp=__import__("datetime").datetime.fromisoformat(m["timestamp"]),
        sender=m.get("sender"), subject=m.get("subject"), body=m.get("body", ""),
        chat_name=m.get("chat_name"), kid_hint=m.get("kid_hint"), url=m.get("url"),
        metadata=m.get("metadata", {}),
    ) for m in raw.get("messages", [])]
    brief, notes = summarize(cfg, for_the_ai(cfg, msgs)[0], [])
    print(json.dumps(to_archive(brief, notes), ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="parent-recap")
    p.add_argument("-c", "--config", default=None, help="Path to config.yaml")
    sub = p.add_subparsers(dest="cmd", required=True)

    prun = sub.add_parser("run", help="Full pipeline")
    prun.add_argument("--dry-run", action="store_true")
    prun.add_argument("--preview", action="store_true",
                      help="A dry run that prints, as JSON lines, how far it is and then the "
                           "first Recipient's email, even on a quiet night (the setup page's)")
    prun.add_argument("--sources", default=None,
                      help=f"Comma-separated: {','.join(s.name for s in source_registry.ALL)} "
                           "(default: the connected ones)")
    prun.add_argument("--lookback-hours", type=int, default=None,
                      help=f"Override the {' / '.join(s.label() for s in source_registry.ALL if s.lookback)} "
                           "lookback window (default: per-source config)")
    prun.add_argument("--if-missed", action="store_true",
                      help="Run only if the evening run's latest time went by with no run since "
                           "(the Windows sign-in task's)")
    prun.set_defaults(func=cmd_run)

    pcol = sub.add_parser("collect", help="Debug only: print raw collected messages (includes bodies)")
    pcol.add_argument("--source", default="gmail")
    pcol.set_defaults(func=cmd_collect)

    psum = sub.add_parser("summarize", help="Summarize from a raw JSON dump")
    psum.add_argument("--input", required=True)
    psum.set_defaults(func=cmd_summarize)

    psync = sub.add_parser("sync-attendees",
                           help="Backfill cfg.google_calendar.invite_attendees onto existing events")
    psync.set_defaults(func=cmd_sync_attendees)

    plang = sub.add_parser("language", help="Get the program's Brief text ready in a language "
                           "(translated once when it has no reviewed text)")
    plang.add_argument("language", type=languages.code, help="A language code, such as sv")
    plang.set_defaults(func=cmd_language)

    pwe = sub.add_parser("weekend-events",
                         help="Friday-afternoon: fetch weekend events, rank via LLM, email")
    pwe.add_argument("--dry-run", action="store_true")
    pwe.set_defaults(func=cmd_weekend_events)

    from . import ai_filter_report, ops, setup_steps, uninstall
    ops.register(sub)
    setup_steps.register(sub)
    uninstall.register(sub)
    ai_filter_report.register(sub)

    # Piped by an agent, without the PYTHONUTF8 the program's own tasks get (task_run.UTF8),
    # Windows' stdout writes the code page (cp1252), which has no ✅: one status line would end
    # the command, as #380's uninstall did after its first item.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = p.parse_args()
    private_files.restrict_new_files()  # archives, logs and diagnostics hold the Household's messages
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
