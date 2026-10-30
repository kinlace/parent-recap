"""The Linux adapter of `jobs`, until systemd user timers run the evening jobs on a server (#581):
nothing is installed, so `doctor` says the evening job isn't, and installing one says it can't be
yet. A run by hand (`parent-recap run`) needs no job. The always-running job, the Linked Device's,
has no place on a server without WhatsApp (ADR 0029)."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from . import jobs  # its names are read at call time: jobs imports this module first

NOT_YET = "Parent Recap can't schedule its evening jobs on Linux yet"


def install(wanted: list[jobs.Job]) -> None:
    raise jobs.NotStarted(NOT_YET)


def remove_all() -> None:
    pass


def installed() -> set[str]:
    return set()


def waiting_for_login() -> set[str]:
    return set()


def found() -> list[jobs.Found]:
    return []


def program(name: str) -> list[str] | None:
    return None


def system_name(name: str) -> str:
    return f"parent-recap-{name}"


def run_now(name: str) -> None:
    raise jobs.NotInstalled()


def install_always(program: list[str], log_dir: Path) -> None:
    raise jobs.NotStarted("the Linked Device doesn't run on a Linux server")


def remove_always() -> None:
    pass


def always_status() -> jobs.AlwaysStatus:
    return jobs.AlwaysStatus(installed=False, running=False)


def stop_always() -> None:
    pass


def run_detached(args: list[str], config: str | None, timeout: int,
                 progress: Callable[[str], None]) -> tuple[int | None, str]:
    raise RuntimeError(NOT_YET)
