"""In-memory fakes of the OS services in `family_brief.system` (ADR 0020): secrets, the evening
jobs, the daily wake and the desktop. The harness puts one in place of each interface's adapter,
so the program reaches it through the same interface it uses on any system, and the tests of
its callers fake none of these services' commands. Only the adapters' own tests (test_secrets.py,
test_jobs.py, test_wake.py, test_desktop.py and their _windows twins) meet the system instead.

Each fake keeps what the system would keep, for a test to set up and check, and has its own
names for what the family reads (where a secret is, a job's name, the wake's line), so a test
asks the interface for them rather than spelling out macOS's. Only macOS-only features, which
have no interface (ADR 0020), still meet their commands in callers' tests: the secret dialog,
iMessage, and Finder and the Passwords app beside them."""
from __future__ import annotations

import contextlib
import io
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, ContextManager

import pytest

from family_brief.system import jobs, wake
from family_brief.system.secrets_error import SecretsError


class FakeSecrets(dict):
    """The secrets store: name -> secret."""

    DELETING_ASKS = "the store may ask you to allow deleting it"  # "": as on Windows

    def __init__(self) -> None:
        super().__init__()
        # Names whose secret the store asks the family about before it's read, as an item an
        # earlier version stored in the Keychain does. Nobody answers, so reading one fails;
        # storing it again replaces it with one that doesn't ask.
        self.asks: set[str] = set()
        self.refuses: SecretsError | None = None  # raised by every write and delete
        self.reachable_here = True                # False: as in tmux or over SSH on macOS
        self.typed: str | None = None             # what the family types at put_typed's prompt

    def get(self, name: str, default: str | None = None) -> str | None:  # type: ignore[override]
        if name in self.asks:
            raise SecretsError(f"the store asks before {name} can be read", "needs-prompt")
        return super().get(name, default)

    def put(self, name: str, value: str) -> None:
        if "\n" in value or "\r" in value:
            raise ValueError("a secret on one line only")
        if self.refuses:
            raise self.refuses
        self[name] = value
        self.asks.discard(name)

    def put_typed(self, name: str) -> None:
        if self.typed is None:
            raise SecretsError(f"{name} wasn't stored")
        self.put(name, self.typed)

    def has(self, name: str) -> bool:
        return name in self

    def delete(self, name: str) -> None:
        if self.refuses:
            raise self.refuses
        self.pop(name, None)
        self.asks.discard(name)

    def where(self, name: str) -> str:
        return f"Parent Recap / {name}"

    def reachable(self) -> bool:
        return self.reachable_here


class FakeJobs:
    """The system's scheduler: our jobs installed, which it will run at their time, and the one-off
    runs, which run `parent-recap` in this process with the evening job's environment."""

    def __init__(self) -> None:
        self.programs: dict[str, list[str]] = {}  # what each installed job runs, by its name
        self.loaded: set[str] = set()   # installed ones the system runs at their time; the rest
                                        # wait for the family to log in
        self.older: list[jobs.Found] = []  # jobs an older version left, found and removed with ours
        self.running: set[str] = set()     # run_now finds these already going
        self.started: list[str] = []       # each job run_now started
        self.start_fails: str | None = None  # run_now's NotStarted reason
        self.detached: list[list[str]] = []  # each one-off run's arguments, -c config first
        self.detached_fails: str | None = None  # the system didn't start a one-off run
        self.hangs = False                    # a one-off run doesn't finish in time
        # Around each one-off run, as the system gives the job its permissions.
        self.inside: Callable[[], ContextManager[None]] = contextlib.nullcontext

    def add(self, *names: str, loaded: bool = True) -> None:
        """Our jobs `names` as `schedule install` leaves them, loaded or waiting for a login."""
        for name in names:
            subcommand = {jobs.DAILY: "run", jobs.WEEKEND: "weekend-events"}[name]
            self.programs[name] = [sys.executable, "-m", "family_brief", subcommand]
            if loaded:
                self.loaded.add(name)

    def install(self, wanted: list[jobs.Job]) -> None:
        self.programs = {job.name: [sys.executable, "-m", "family_brief", job.subcommand]
                         for job in wanted}
        self.loaded = set(self.programs)

    def remove_all(self) -> None:
        self.programs, self.loaded, self.older = {}, set(), []

    def installed(self) -> set[str]:
        return self.loaded & set(self.programs)

    def waiting_for_login(self) -> set[str]:
        return set(self.programs) - self.loaded

    def found(self) -> list[jobs.Found]:
        return [jobs.Found(self.system_name(name), self.where(name), program)
                for name, program in self.programs.items()] + self.older

    def program(self, name: str) -> list[str] | None:
        return self.programs.get(name)

    def system_name(self, name: str) -> str:
        return f"Parent Recap {name}"

    def where(self, name: str) -> Path:
        return Path.home() / "Scheduler" / self.system_name(name)

    def run_now(self, name: str) -> None:
        if name not in self.loaded:
            raise jobs.NotInstalled()
        if name in self.running:
            raise jobs.AlreadyRunning()
        if self.start_fails is not None:
            raise jobs.NotStarted(self.start_fails)
        self.started.append(name)

    def run_detached(self, args: list[str], config: str | None, timeout: int,
                     progress: Callable[[str], None]) -> tuple[int | None, str]:
        argv = [*(["-c", str(Path(config).expanduser())] if config else []), *args]
        self.detached.append(argv)
        if self.detached_fails is not None:
            raise RuntimeError(self.detached_fails)
        if self.hangs:
            return None, ""
        from family_brief import __main__ as cli
        out = io.StringIO()
        with self.inside(), pytest.MonkeyPatch.context() as mp, \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            mp.setenv(jobs.BG_ENV, "1")
            mp.setattr(sys, "argv", ["family-brief", *argv])
            try:
                code = cli.main()
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) else 1
        progress(out.getvalue())
        return code, out.getvalue()


class FakeWake:
    """The computer's repeating wakes, and the administrator dialog that sets and cancels them."""

    MANUAL_CANCEL = "cancel-wakes"

    def __init__(self) -> None:
        self.wakes: list[str] = []   # the repeating wakes, as the system lists them
        self.never_sleeps = False
        # The family's answer in the dialog: "allow", "cancel" or "none" (no dialog can open).
        self.dialog = "allow"
        self.asked: list[str] = []   # what each dialog was for: "set HH:MM" or "cancel"
        # False: as on Windows, where only the family's own setting turns the wake on, so `set`
        # asks nobody and says not-set until it's in `wakes`.
        self.settable = True

    @property
    def OWN_SCHEDULE(self) -> bool:  # noqa: N802  the adapters' constant
        # As on Windows, the wake rides on the job's task, with no schedule of its own.
        return self.settable

    @staticmethod
    def line(hour: int, minute: int) -> str:
        """The wake setup gives for a job at hour:minute, as this system lists it."""
        wake_h, wake_m = wake.time(hour, minute)
        return f"wake every day at {wake_h:02d}:{wake_m:02d}"

    def status(self, hour: int, minute: int) -> wake.WakeStatus:
        ours = self.line(hour, minute)
        return wake.WakeStatus(ours if ours in self.wakes else None,
                               [w for w in self.wakes if w != ours], self.never_sleeps)

    def set(self, hour: int, minute: int, replace: bool) -> str:
        if self.never_sleeps:
            return "never-sleeps"
        status = self.status(hour, minute)
        if status.ours:
            return "already-set"
        if status.others and not replace:
            return "other-schedule"
        if not self.settable:
            return "not-set"
        wake_h, wake_m = wake.time(hour, minute)
        self.asked.append(f"set {wake_h:02d}:{wake_m:02d}")
        if self.dialog == "cancel":
            return "cancelled"
        if self.dialog == "none":
            return "no-dialog"
        self.wakes = [self.line(hour, minute)]  # a new schedule replaces the others
        return "set"

    def cancel(self) -> None:
        self.asked.append("cancel")
        if self.dialog in ("cancel", "none"):
            raise wake.NotCancelled(f"the dialog was cancelled. Run this instead: "
                                    f"{self.MANUAL_CANCEL}")
        self.wakes = []

    def manual_step(self, hour: int, minute: int) -> str:
        wake_h, wake_m = wake.time(hour, minute)
        return f"set-wake {wake_h:02d}:{wake_m:02d}"


@dataclass
class Window:
    """A terminal window: the command it runs, the variables it's given and its title."""
    argv: list[str]
    env: dict[str, str]
    title: str


class FakeDesktop:
    """The browser, the text editor and terminal windows, and the computer's languages."""

    def __init__(self) -> None:
        self.opened: list[str] = []      # each page and file opened, in order
        self.windows: list[Window] = []  # each terminal window opened, in order
        self.forgotten: list[str] = []   # each window's title, once what it ran with is removed
        self.opens = True                # False: nothing opens, as in a sandbox or over SSH
        self.languages: list[str] = ["en-FI"]  # preferred, first first
        self.terminal = False            # True: someone types at stdin's terminal
        # Runs in each window as it opens, as the family would there; a test sets it.
        self.in_window: Callable[[Window], None] | None = None

    def open_url(self, url: str) -> bool:
        if self.opens:
            self.opened.append(url)
        return self.opens

    def open_file(self, path: Path) -> bool:
        return self.open_url(str(path))

    def open_terminal(self, argv: list[str], env: dict[str, str], title: str) -> bool:
        if not self.opens:
            return False
        window = Window(list(argv), dict(env), title)
        self.windows.append(window)
        if self.in_window:
            self.in_window(window)
        return True

    def forget_terminal(self, title: str) -> None:
        self.forgotten.append(title)

    def preferred_languages(self) -> list[str]:
        return list(self.languages)

    def typing_at_terminal(self) -> bool:
        return self.terminal
