"""setup status — whether setup is done: the outcomes that decide it, each true or false with a
one-line reason.

1. installed — the program is installed
2. doctor    — the health check has no ❌ (its ⚠️ warnings are named, but don't hold setup up)
3. brief     — the first Brief reached the setup parent, the first Recipient; the
               others' first Brief is the first evening one
4. nightly   — the nightly job is loaded
5. wake      — the wake schedule is set, or the Mac never sleeps

The sixth, knowing to log in after a restart, is the setup skill's to confirm with the family, and
the setup page shows it under its checklist of these five.
It prints one line of JSON for the setup skill, or with --text a short line per outcome. Neither
has a secret or message text in it: doctor's own details stay out, only the names of its checks
that aren't OK are given.
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import install_record, ops
from .config import Config
from .state import State


TITLES = {
    "installed": "Program installed",
    "doctor": "Health check has nothing to fix",
    "brief": "First Brief reached the setup parent",
    "nightly": "Nightly job loaded",
    "wake": "Wake schedule set, or the Mac never sleeps",
}


@dataclass
class Outcome:
    outcome: str
    ok: bool
    reason: str
    # Doctor's checks that aren't OK, each (status, name), for the setup page to name: on a true
    # outcome, its warnings.
    checks: list[tuple[str, str]] = field(default_factory=list)


def register(steps) -> None:
    p = steps.add_parser("status", help="Report whether setup is done: each outcome that decides "
                         "it, true or false, with a reason")
    p.add_argument("--text", action="store_true", help="A short line per outcome instead of JSON")
    p.set_defaults(func=cmd_status)


def cmd_status(args: argparse.Namespace) -> int:
    outcomes = check(args.config)
    done = all(o.ok for o in outcomes)
    if args.text:
        for o in outcomes:
            print(f"{ops.OK if o.ok else ops.FAIL} {TITLES[o.outcome]}: {o.reason}")
    else:
        print(json.dumps({"result": "done" if done else "not-done",
                          "outcomes": [{"outcome": o.outcome, "ok": o.ok, "reason": o.reason}
                                       for o in outcomes]}, ensure_ascii=False))
    return 0 if done else 1


def check(config: str | None) -> list[Outcome]:
    """Each outcome, in order, for the config at `config`. The setup page's Finish checks these too."""
    try:
        cfg: Config | None = Config.load(config)
    except Exception:  # doctor names what's wrong with it
        cfg = None
    schedule = (cfg or Config(kids=[])).schedule
    hour, minute = schedule.daily_hour, schedule.daily_minute
    return [_installed(), _doctor(config), _brief(cfg), _nightly(hour, minute), _wake(hour, minute)]


def _installed() -> Outcome:
    programs = [Path(p) for p in install_record.entries("program")] or \
        [Path.home() / "ParentRecap" / "app"]
    for program in programs:
        try:
            ours = re.search(r'^name\s*=\s*"family-brief"', (program / "pyproject.toml").read_text(),
                             re.M)
        except OSError:
            continue
        if ours and (program / ".venv" / "bin" / "family-brief").exists():
            version = _read(program / "VERSION") or "unknown version"
            return Outcome("installed", True, f"{version} in {_show(program)}")
    return Outcome("installed", False, f"Parent Recap isn't installed in "
                   f"{', '.join(_show(p) for p in programs)}: run the plugin's install.sh")


def _doctor(config: str | None) -> Outcome:
    # Only the checks' names and statuses go into the reason: their details can quote an error.
    results = ops.health_checks(config)
    checks = list(dict.fromkeys((status, item) for status, item, _ in results if status != ops.OK))
    if not checks:
        return Outcome("doctor", True, f"all {len(results)} checks OK")
    not_ok = [f"{item} {status.strip()}" for status, item in checks]
    # Only a ❌ holds setup up; a ⚠️ is named, with what to do, but the Brief works without it.
    if all(status == ops.WARN for status, _ in checks):
        return Outcome("doctor", True, f"no check failed, {len(not_ok)} of {len(results)} have a "
                       f"warning: {', '.join(not_ok)} (run parent-recap doctor to see why)", checks)
    return Outcome("doctor", False, f"{len(not_ok)} of {len(results)} checks aren't OK: "
                   f"{', '.join(not_ok)} (run parent-recap doctor to see why)", checks)


def _brief(cfg: Config | None) -> Outcome:
    if cfg is None:
        return Outcome("brief", False, "the config can't be read, so its Recipients aren't known")
    to = [*(r.address for r in cfg.email.to if cfg.email.enabled),
          *(cfg.imessage.recipients if cfg.imessage.enabled else [])]
    if not to:
        return Outcome("brief", False, "the Brief goes to nobody: email.to is empty or email is off")
    parent, others = to[0], to[1:]
    if State(cfg.resolved_state_path()).delivered_at(parent) is None:
        return Outcome("brief", False, f"no Brief has gone out to {parent} yet (send the first one "
                       "from the setup page, or with parent-recap bg run --lookback-hours 72)")
    later = f", and {', '.join(others)} get theirs with the evening Brief" if others else ""
    return Outcome("brief", True, f"sent to {parent}{later}")


def _nightly(hour: int, minute: int) -> Outcome:
    if ops.JOB_DAILY in ops.launchctl_loaded():
        return Outcome("nightly", True, f"{ops.JOB_DAILY} runs every day at {hour:02d}:{minute:02d}")
    return Outcome("nightly", False, f"{ops.JOB_DAILY} isn't loaded: run parent-recap schedule "
                   "install")


def _wake(hour: int, minute: int) -> Outcome:
    if ops.never_sleeps():
        return Outcome("wake", True, "this Mac never sleeps, so it needs no wake schedule")
    wake_h, wake_m = ops.wake_time(hour, minute)
    repeating = ops.repeating_wakes()
    if any(ops.is_our_wake(line, hour, minute) for line in repeating):
        return Outcome("wake", True, f"the Mac wakes at {wake_h:02d}:{wake_m:02d} every day")
    install = "parent-recap schedule install"
    if repeating:  # replaced only once the family agrees
        install = (f"{install} --replace-wake once the family agrees to replace this Mac's other "
                   f"repeating wake schedule ({', '.join(repeating)})")
    return Outcome("wake", False, f"the Mac sleeps and doesn't wake at {wake_h:02d}:{wake_m:02d} "
                   f"every day, so the Brief only comes on nights it's awake. Run {install}, "
                   "which sets it through macOS's administrator dialog, or the family runs this "
                   f"in Terminal: {ops.wake_command(hour, minute)}")


def _read(path: Path) -> str:
    try:
        return path.read_text().strip()
    except OSError:
        return ""


def _show(p: Path) -> str:
    home = Path.home()
    return f"~/{p.relative_to(home)}" if p.is_relative_to(home) else str(p)
