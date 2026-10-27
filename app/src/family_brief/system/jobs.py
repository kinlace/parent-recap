"""The evening jobs, the always-running job, and one-off runs with the evening job's permissions
(ADR 0020). The program describes a job; each system's adapter turns it into its own scheduler's:
launchd on macOS, Task Scheduler on Windows.

The evening jobs run at their time. The always-running job, the Linked Device's (ADR 0026), runs
from the family's sign-in on and is started again whenever it exits, unless it exits with
LINK_ENDED."""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

if sys.platform == "win32":
    from . import jobs_windows as _adapter
else:
    from . import jobs_macos as _adapter

DAILY = "daily"
WEEKEND = "weekend-events"
BG_ENV = "PARENT_RECAP_BG"  # set inside run_detached's runs so doctor doesn't recurse
TIMED_OUT = 124  # run_detached's exit code for a run it stopped, as timeout(1) gives
ALWAYS = "linked-device"  # the always-running job's name
# The always-running job's exit code once its link has ended, after which nothing starts it
# again until it's installed anew. launchd's KeepAlive tells only a successful exit from the
# rest, so it is 0, and the program must end any other way, a crash or an empty event loop
# included, with another code.
LINK_ENDED = 0


@dataclass(frozen=True)
class Job:
    name: str              # DAILY or WEEKEND
    subcommand: str        # python -m family_brief <subcommand>
    weekday: int | None    # None = every day; 0 and 7 are Sunday
    hour: int
    minute: int
    log_dir: Path          # <subcommand>-stdout.log and -stderr.log go here


@dataclass(frozen=True)
class AlwaysStatus:
    """The always-running job's: installed, and running right now."""
    installed: bool
    running: bool


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
    """Removes our jobs, the always-running one too, including what older versions left under
    their names or in the record."""
    _adapter.remove_all()


def installed() -> set[str]:
    """The names of our evening jobs the system will run at their time."""
    return _adapter.installed()


def install_always(program: list[str], log_dir: Path) -> None:
    """Installs the always-running job to run `program` (a command and its arguments) at every
    sign-in of the family's user, in place of the one installed, and starts it now. Its output
    goes to ALWAYS-stdout.log and -stderr.log in `log_dir`."""
    _adapter.install_always(program, log_dir)


def remove_always() -> None:
    """Stops and removes the always-running job, if it's installed."""
    _adapter.remove_always()


def always_status() -> AlwaysStatus:
    """Whether the always-running job is installed, and running."""
    return _adapter.always_status()


def stop_always() -> None:
    """Stops the always-running job without removing it: it starts again at the next sign-in, or
    once installed anew."""
    _adapter.stop_always()


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
