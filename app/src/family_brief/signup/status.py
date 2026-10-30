"""`parent-recap signup status`: every Hosted Household's status in one place, for the maintainers
on the server (ops/gateway/status.sh runs it as the sign-up service's user). For each Household in
the store it runs `signup health` as that Household's user, through the household host, and prints
one line: its user, the time its Brief comes, when its last run ended and how, how long it took
and its peak memory, each of doctor's checks by its key with its status, and a mark when the last
run failed or is overdue.

`signup health` is the Household's side: it reads its own last-run.json and state.json, runs
doctor's checks without the model's test call (which spends the Household's budget), and prints
one line of JSON with only times, exit codes, durations, peak memory, check keys and statuses.
Doctor's words name the Recipients and the Kids, so none of them is sent; and the maintainers'
side takes from that line only what has those shapes, so no message text, subject, address or
Kid's name can reach its output whatever a Household's files hold.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .. import last_run, ops
from ..config import Config
from ..state import State
from .host import HostError, HouseholdHost
from .invites import HouseholdRecord, Store
from .server import _last_json

HOMES = Path("/home")  # where household-user makes each Household's home
HEALTH_SECONDS = 300  # doctor signs in to Wilma and downloads each MyClub calendar
# A run that started at its time is given this long to end before it counts as overdue.
GRACE = timedelta(hours=1)
LEVELS = {ops.OK: "ok", ops.WARN: "warn", ops.FAIL: "fail"}
MARKS = {"ok": "✅", "warn": "⚠️", "fail": "❌"}
KEY = re.compile(r"[a-z][a-z0-9-]{0,39}")  # a doctor check's key


# ---------------------------------------------------------------- the Household's side

def cmd_health(args: argparse.Namespace) -> int:
    config = Path(os.path.expanduser(args.config or "~/.family/config.yaml"))
    print(json.dumps(health(config, datetime.now(timezone.utc))))
    return 0


def health(config: Path, now: datetime) -> dict[str, Any]:
    checks = [[c.key, LEVELS[c.status]] for c in ops.health_checks(str(config), skip_llm=True)]
    try:
        cfg = Config.load(config)
    except Exception:  # noqa: BLE001 - doctor's config check says it failed
        return {"checks": checks}
    last = last_run.read(cfg)
    state = State(cfg.resolved_state_path())
    # Before this version noted how a run ended, only when it last got to its end.
    since = last.at if last else state.last_run_at or state.scheduled_at
    return {"last_run": last.at.isoformat() if last else None,
            "exit": last.exit if last else None,
            "seconds": last.seconds if last else None,
            "peak_mb": last.peak_mb if last else None,
            "overdue": overdue(since, cfg.schedule.daily_hour, cfg.schedule.daily_minute,
                               cfg.timezone, now),
            "checks": checks}


def overdue(since: datetime | None, hour: int, minute: int, zone: str, now: datetime) -> bool:
    """Whether no run has ended since the latest evening run that should have by `now`: the
    latest time the evening job started, in the Household's timezone, more than GRACE ago."""
    local = now.astimezone(ZoneInfo(zone))
    due = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if due + GRACE > local:
        due -= timedelta(days=1)
    return since is None or since < due


# ---------------------------------------------------------------- the maintainers' side

@dataclass(frozen=True)
class Status:
    user: str
    evening: str
    last_run: datetime | None = None
    exit: int | None = None
    overdue: bool = False
    checks: dict[str, str] = field(default_factory=dict)  # each key's worst status, in order
    seconds: int | None = None  # how long the last run took
    peak_mb: int | None = None  # and the most memory it used
    unknown: str | None = None  # why the Household's status couldn't be read

    @property
    def failed(self) -> bool:
        return self.exit not in (None, 0)


def status(host: HouseholdHost, household: HouseholdRecord, homes: Path) -> Status:
    program = homes / household.user / "ParentRecap" / "app" / ".venv" / "bin" / "parent-recap"
    try:
        code, out = host.run_as(household.user, [str(program), "signup", "health"], "",
                                HEALTH_SECONDS)
    except HostError as e:
        return Status(household.user, household.evening, unknown=str(e))
    # Its output isn't quoted: doctor's log lines can name a Recipient or a Kid.
    said = _last_json(out)
    if code != 0 or "checks" not in said:
        return Status(household.user, household.evening,
                      unknown=f"signup health ended with {code} and said no status (a program "
                              "older than this command?)")
    return Status(household.user, household.evening, _time(said.get("last_run")),
                  last_run.number(said.get("exit")), said.get("overdue") is True,
                  _checks(said["checks"]), last_run.number(said.get("seconds")),
                  last_run.number(said.get("peak_mb")))


def _time(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(value).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _checks(given: Any) -> dict[str, str]:
    order = list(MARKS)
    worst: dict[str, str] = {}
    for item in given if isinstance(given, list) else []:
        if not (isinstance(item, list) and len(item) == 2):
            continue
        key, level = item
        if isinstance(key, str) and KEY.fullmatch(key) and level in MARKS:
            if order.index(level) >= order.index(worst.get(key, "ok")):
                worst[key] = level
    return worst


def line(s: Status) -> str:
    if s.unknown:
        return f"❓ {s.user}  {s.evening}  status unknown: {s.unknown}"
    mark = MARKS["fail" if s.failed else "warn" if s.overdue else "ok"]
    if s.last_run is None:
        run = "no run yet"
    else:
        run = f"last run {s.last_run:%Y-%m-%d %H:%M} UTC, " + (
            f"failed (exit {s.exit})" if s.failed else "ok" if s.exit == 0 else "outcome unknown")
        # A run noted before these were, or on a platform without its peak, says less.
        run += "".join(f", {n} {unit}" for n, unit in ((s.seconds, "s"), (s.peak_mb, "MB"))
                       if n is not None)
    doctor = "  ".join(f"{MARKS[level]} {key}" for key, level in s.checks.items())
    return (f"{mark} {s.user}  {s.evening}  {run}" + (", overdue" if s.overdue else "")
            + f"\n   doctor: {doctor or '(no checks)'}")


def report(store: Store, host: HouseholdHost, homes: Path = HOMES) -> int:
    """Prints each Household's status as it's read; 1 when any is marked or unknown."""
    households = store.households()
    if not households:
        print("No Hosted Households yet.")
        return 0
    found = []
    for household in households:
        s = status(host, household, homes)
        found.append(s)
        print(line(s), flush=True)
    failed = sum(s.failed for s in found if not s.unknown)
    late = sum(s.overdue for s in found if not s.unknown)
    unknown = sum(bool(s.unknown) for s in found)
    print(f"\n{len(found)} Households: {failed} failed, {late} overdue, {unknown} unknown")
    return 1 if failed or late or unknown else 0


def cmd_status(args: argparse.Namespace) -> int:
    from . import store
    from .host_linux import LinuxHost
    # Only commands run as the Households: the gateway's admin login isn't needed.
    return report(store(), LinuxHost("", ""))
