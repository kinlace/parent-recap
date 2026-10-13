"""The evening jobs, and one-off runs with the evening job's permissions (ADR 0020). The program
describes a job; each system's adapter turns it into its own scheduler's. Only macOS's, launchd,
exists so far."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import jobs_macos as _adapter

DAILY = "daily"
WEEKEND = "weekend-events"
BG_ENV = "PARENT_RECAP_BG"  # set inside run_detached's runs so doctor doesn't recurse
TIMED_OUT = 124  # run_detached's exit code for a run it stopped, as timeout(1) gives


@dataclass(frozen=True)
class Job:
    name: str              # DAILY or WEEKEND
    subcommand: str        # python -m family_brief <subcommand>
    weekday: int | None    # None = every day; 0 and 7 are Sunday
    hour: int
    minute: int
    log_dir: Path          # <subcommand>-stdout.log and -stderr.log go here


@dataclass(frozen=True)
class Found:
    """A job of ours the system keeps, loaded or not, by our name or setup's record of it."""
    system_name: str
    where: Path            # the file the system keeps it in
    program: list[str]     # the command it runs; empty when it can't be read


class NotInstalled(Exception):
    """run_now's job isn't installed."""


class AlreadyRunning(Exception):
    """run_now's job is running, so no second run was started."""


class NotStarted(Exception):
    """The system didn't start run_now's job; the message is its reason."""


def install(jobs: list[Job]) -> None:
    """Installs `jobs` in place of ours, removing ours that aren't among them, and adds each to
    setup's record."""
    _adapter.install(jobs)


def remove_all() -> None:
    """Removes our jobs, including what older versions left under their names or in the record."""
    _adapter.remove_all()


def installed() -> set[str]:
    """The names of our jobs the system will run at their time."""
    return _adapter.installed()


def waiting_for_login() -> set[str]:
    """The names of our jobs that are installed but run only once the family logs in to the
    desktop: after a restart, or seen over SSH."""
    return _adapter.waiting_for_login()


def found() -> list[Found]:
    """Every job of ours the system keeps, for uninstall to check what each runs."""
    return _adapter.found()


def program(name: str) -> list[str] | None:
    """The command the installed job `name` runs, or None when it isn't installed."""
    return _adapter.program(name)


def system_name(name: str) -> str:
    """What the system calls the job `name`, for messages."""
    return _adapter.system_name(name)


def run_now(name: str) -> None:
    """Asks the system to start the installed job `name` now, with its own Python, environment
    and log files, without waiting for it. Raises NotInstalled, AlreadyRunning or NotStarted."""
    _adapter.run_now(name)


def run_detached(args: list[str], timeout: int, *, config: str | None = None, echo: bool = True,
                 output: Callable[[str], None] | None = None) -> tuple[int, str]:
    """Runs `parent-recap <args>` with `config` as a one-off job with the evening job's Python
    and permissions, waits up to `timeout` seconds for it, and returns its exit code, TIMED_OUT
    once stopped, and its output. `output`, if given, is handed all of the output so far each time
    the run is checked on; `echo` prints it as it comes."""
    shown = 0

    def progress(text: str) -> None:
        nonlocal shown
        if output:
            output(text)
        if echo:
            print(text[shown:], end="", flush=True)
            shown = len(text)
    code, text = _adapter.run_detached(args, config, timeout, progress)
    if echo:
        print(text[shown:], end="", flush=True)
        if code is None:
            print(f"❌ the background job didn't finish within {timeout} seconds and was stopped",
                  flush=True)
    return (TIMED_OUT if code is None else code), text
