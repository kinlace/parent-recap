"""`python -m family_brief.schedule_update`, which install.sh runs on a Mac after every install or
update. A Household whose evening job is scheduled gets this release's jobs and wake: the jobs are
installed again, so they start as this release has them, and the wake an older version set 5
minutes before the job moves to the job's own time (#832), through macOS's administrator dialog.

It says what it couldn't do and how to do it by hand, and never fails the install: what isn't
updated keeps working as it did. A dialog that's closed keeps the older version's wake."""
from __future__ import annotations

import os
import sys

from . import ops, private_files, run_lock
from .config import Config
from .system import jobs, wake

SCHEDULE_INSTALL = "parent-recap schedule install"  # what the family runs to do it by hand
# Set by `parent-recap update` (update.py) for the release's installer it runs: nothing may ask
# for a password then.
UPDATING = "PARENT_RECAP_UPDATING"


def update(config: str | None = None) -> None:
    if jobs.program(jobs.DAILY) is None:
        return  # not scheduled yet: setup's Finish schedules it
    try:
        cfg = Config.load(config)
    except Exception:  # noqa: BLE001 - doctor says what's wrong with it
        print(f"{ops.WARN}The evening Brief's job wasn't updated: the config can't be read. Run "
              f"parent-recap doctor, fix what it names, then run {SCHEDULE_INSTALL}.")
        return
    if run_lock.is_busy(cfg):  # installing the job again would stop the run
        if not os.environ.get(UPDATING):
            print(f"{ops.WARN}The evening Brief's job wasn't updated, since a Brief is being made "
                  f"right now. Run {SCHEDULE_INSTALL} once it has gone out.")
            return
        # As the evening run's own update (update.evening) is, inside the job: the jobs stay as
        # they are and start the program from the same path, which now points at this version.
        # The wake below still says what it would ask for.
        print("The evening Brief's job was left as it is, since a Brief run is going: it starts "
              f"this version from the same path. {SCHEDULE_INSTALL} installs it again.")
    else:
        try:
            ops.install_jobs(cfg)
        except Exception:  # noqa: BLE001 - the job installed before keeps running
            print(f"{ops.WARN}The evening Brief's job couldn't be updated, so it runs as it did. "
                  f"Run {SCHEDULE_INSTALL} to update it.")
            return
    hour, minute = cfg.schedule.daily_hour, cfg.schedule.daily_minute
    if not (wake.sleeps() and wake.has_own_schedule()):
        return
    status = wake.status(hour, minute)
    if status.ours or not status.old:
        return  # already moved, or none of ours to move: doctor says if one is missing
    if os.environ.get(UPDATING):
        # `parent-recap update` asks for no password (ADR 0047): the older wake keeps working.
        print(f"{ops.WARN}The Mac's wake wasn't moved to {hour:02d}:{minute:02d}, since that asks "
              f"for your Mac password. Run {SCHEDULE_INSTALL} to move it.")
        return
    print(f"\nParent Recap now wakes the Mac at {hour:02d}:{minute:02d}, when the Brief starts, "
          "instead of 5 minutes before, so the Brief starts as the Mac wakes, before a MacBook "
          "with its lid closed can go back to sleep. "
          "macOS asks for your Mac password to move the wake.")
    ops.set_wake(hour, minute, False)


def main(argv: list[str]) -> int:
    if argv:
        print("Usage: python -m family_brief.schedule_update", file=sys.stderr)
        return 2
    private_files.restrict_new_files()  # install.sh runs this with the shell's umask
    try:
        update()
    except Exception:  # noqa: BLE001 - never fail the install over the schedule
        print(f"{ops.WARN}The evening Brief's schedule wasn't updated. Run {SCHEDULE_INSTALL} to update it.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
