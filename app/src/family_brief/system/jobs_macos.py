"""The macOS adapter of `jobs`: LaunchAgents in ~/Library/LaunchAgents, loaded with `launchctl`.

A one-off run goes through launchd too. macOS grants WhatsApp access per responsible process.
Started from Terminal, Claude Code or Codex, that is the parent app, which usually has no access.
A launchd job is its own responsible process, so it gets exactly the permission the evening job's
Python has (ADR 0012)."""
from __future__ import annotations

import os
import plistlib
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import FrameType
from typing import TYPE_CHECKING, Callable

from .. import install_record, private_files, tools

if TYPE_CHECKING:
    from .jobs import Found, Job

LAUNCH_AGENTS = Path.home() / "Library" / "LaunchAgents"
PREFIX = "com.parentrecap."
DAILY = f"{PREFIX}daily"
WEEKEND = f"{PREFIX}weekend-events"
OURS = (DAILY, WEEKEND)


def system_name(name: str) -> str:
    return PREFIX + name


def _name(label: str) -> str:
    return label.removeprefix(PREFIX)


def _path(label: str) -> Path:
    return LAUNCH_AGENTS / f"{label}.plist"


def _domain() -> str:
    return f"gui/{os.getuid()}"


# ---------------------------------------------------------------- the evening jobs

def _plist(job: Job) -> dict:
    interval = {"Hour": job.hour, "Minute": job.minute}
    if job.weekday is not None:
        interval = {"Weekday": job.weekday, **interval}
    return {
        "Label": system_name(job.name),
        "ProgramArguments": [sys.executable, "-m", "family_brief", job.subcommand],
        "StartCalendarInterval": interval,
        "StandardOutPath": str(job.log_dir / f"{job.subcommand}-stdout.log"),
        "StandardErrorPath": str(job.log_dir / f"{job.subcommand}-stderr.log"),
        "EnvironmentVariables": {"PATH": _path_env(), "HOME": str(Path.home())},
        "Umask": private_files.UMASK,  # launchd creates the log files with the job's umask
        "RunAtLoad": False,
    }


def install(jobs: list[Job]) -> None:
    for job in jobs:
        label = system_name(job.name)
        path = _path(label)
        LAUNCH_AGENTS.mkdir(parents=True, exist_ok=True)
        subprocess.run(["launchctl", "unload", str(path)], capture_output=True)
        with path.open("wb") as f:
            plistlib.dump(_plist(job), f)
        subprocess.run(["launchctl", "load", str(path)], check=True)
        install_record.add("launchd", str(path))
    kept = {system_name(job.name) for job in jobs}
    for label in OURS:
        if label not in kept:
            _remove(_path(label))


def _remove(path: Path) -> None:
    if path.exists():
        subprocess.run(["launchctl", "unload", str(path)], capture_output=True)
        path.unlink()


def _plists() -> list[Path]:
    """Our jobs' plists, by our labels and from setup's record, which has older versions' too."""
    usual = [_path(label) for label in OURS]
    return [p for p in dict.fromkeys([*usual, *(Path(p) for p in install_record.entries("launchd"))])
            if p.exists()]


def remove_all() -> None:
    for path in _plists():
        _remove(path)


def _loaded() -> set[str]:
    out = subprocess.run(["launchctl", "list"], capture_output=True, text=True).stdout
    return {line.split()[-1] for line in out.splitlines() if PREFIX in line}


def installed() -> set[str]:
    return {_name(label) for label in _loaded()}


def waiting_for_login() -> set[str]:
    # launchd loads a LaunchAgent only for a desktop login, so after a restart, or seen from SSH,
    # an installed job isn't loaded.
    loaded = _loaded()
    return {_name(label) for label in OURS if label not in loaded and _path(label).exists()}


def _program(path: Path) -> list[str]:
    try:
        return [str(a) for a in plistlib.loads(path.read_bytes()).get("ProgramArguments", [])]
    except (OSError, plistlib.InvalidFileException, AttributeError):
        return []


def found() -> list[Found]:
    from .jobs import Found
    return [Found(p.stem, p, _program(p)) for p in _plists()]


def program(name: str) -> list[str] | None:
    path = _path(system_name(name))
    return _program(path) or None if path.exists() else None


def run_now(name: str) -> None:
    from .jobs import AlreadyRunning, NotInstalled, NotStarted
    target = f"{_domain()}/{system_name(name)}"
    probe = subprocess.run(["launchctl", "print", target], capture_output=True, text=True)
    if probe.returncode != 0:
        raise NotInstalled()
    # Plain kickstart (no -k) leaves a running job alone, so say so instead of looking like a start.
    if re.search(r"^\tstate = running$", probe.stdout, re.M):
        raise AlreadyRunning()
    kick = subprocess.run(["launchctl", "kickstart", target], capture_output=True, text=True)
    if kick.returncode != 0:
        raise NotStarted(f"launchctl couldn't start {system_name(name)}: "
                         f"{kick.stderr.strip() or kick.returncode}")


def _path_env() -> str:
    from ..summarize import native_claude_dir
    dirs: list[str] = []
    # Node for a claude installed with npm; the wilma CLI runs on Parent Recap's own (ADR 0011).
    for tool in ("claude", "node"):
        found = tools.find(tool)
        if found:
            dirs.append(str(Path(found).resolve().parent))
            dirs.append(str(Path(found).parent))
    # Claude Code's native installer puts claude there, and the shell running this may not have it.
    dirs += [str(native_claude_dir()), "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"]
    seen: list[str] = []
    for d in dirs:
        if d not in seen:
            seen.append(d)
    return ":".join(seen)


# ---------------------------------------------------------------- one-off runs

def _exit_on_signal(signum: int, _frame: FrameType | None) -> None:
    raise SystemExit(128 + signum)


def run_detached(args: list[str], config: str | None, timeout: int,
                 progress: Callable[[str], None]) -> tuple[int | None, str]:
    """`jobs.run_detached` as a one-off launchd job. Its exit code is None when it was stopped."""
    from .jobs import BG_ENV
    work = Path(tempfile.mkdtemp(prefix="parent-recap-bg-"))
    label = f"{PREFIX}bg.{os.getpid()}"
    domain = _domain()
    out = work / "output.log"
    prog = [sys.executable, "-m", "family_brief"]
    if config:
        prog += ["-c", str(Path(config).expanduser())]
    plist_path = work / f"{label}.plist"
    plist_path.write_bytes(plistlib.dumps({
        "Label": label,
        "ProgramArguments": prog + args,
        "RunAtLoad": True,
        "StandardOutPath": str(out),
        "StandardErrorPath": str(out),
        "WorkingDirectory": str(Path.home()),
        "Umask": private_files.UMASK,
        "EnvironmentVariables": {"PATH": _path_env(), "HOME": str(Path.home()),
                                 "PYTHONUNBUFFERED": "1", BG_ENV: "1"},
    }))
    code: int | None = None
    # Python skips `finally` on SIGTERM, and the job belongs to launchd, so an agent's command
    # timeout would leave it running. Turning the signal into an exit lets the cleanup run.
    # Only the main thread can, so the setup page's reads, from its own threads, leave them be.
    main = threading.current_thread() is threading.main_thread()
    stops = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP) if main else ()
    before = {s: signal.signal(s, _exit_on_signal) for s in stops}
    try:
        r = subprocess.run(["launchctl", "bootstrap", domain, str(plist_path)], capture_output=True,
                           text=True)
        if r.returncode != 0:
            raise RuntimeError(f"launchctl bootstrap failed: {r.stderr.strip() or r.returncode}")
        deadline = time.time() + timeout
        while code is None and time.time() < deadline:
            time.sleep(1)
            code = _job_exit_code(domain, label)
            if out.exists():
                progress(out.read_text(errors="replace"))
    finally:
        for s in stops:  # a second signal mustn't cut the cleanup short
            signal.signal(s, signal.SIG_IGN)
        subprocess.run(["launchctl", "bootout", f"{domain}/{label}"], capture_output=True)
        text = out.read_text(errors="replace") if out.exists() else ""
        shutil.rmtree(work, ignore_errors=True)
        for s, handler in before.items():
            signal.signal(s, signal.SIG_DFL if handler is None else handler)  # None: set outside Python
    return code, text


def _job_exit_code(domain: str, label: str) -> int | None:
    """The job's exit code once it has finished, None while it is starting or running."""
    r = subprocess.run(["launchctl", "print", f"{domain}/{label}"], capture_output=True, text=True)
    if r.returncode != 0 or re.search(r"^\tstate = running$", r.stdout, re.M):
        return None
    m = re.search(r"^\tlast exit code = (-?\d+)$", r.stdout, re.M)
    return int(m.group(1)) if m else None
