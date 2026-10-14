"""The Windows adapter of `jobs`: Task Scheduler tasks in its root folder, registered, read and
started through PowerShell's ScheduledTasks commands, none of which needs administrator rights.

A task runs only while the family is signed in ("run only when user is logged on"), locked or
asleep, wakes the PC to run ("wake the computer to run this task"), and runs as soon as it can
after a start it missed (ADR 0021). It starts pythonw.exe with `task_run`, which gives the job its
log files without opening a window on the family's desktop."""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from . import jobs  # its names are read at call time: jobs imports this module first

PREFIX = "Parent Recap "
TASK_NS = "http://schemas.microsoft.com/windows/2004/02/mit/task"
DAYS = ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")
RUNNER = "family_brief.system.task_run"
# run_now's script exits with these when there's no task to start or it is already going.
NO_TASK, RUNNING = 3, 4


def system_name(name: str) -> str:
    return PREFIX + name


def _where(task: str) -> Path:
    """Where Task Scheduler keeps a task of its root folder."""
    return Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "Tasks" / task


def _powershell(script: str, **env: str) -> subprocess.CompletedProcess:
    """Runs `script` in Windows PowerShell, its values handed over as PARENT_RECAP_<NAME>
    variables so none is ever quoted into the script. A failure's message goes to stderr."""
    script = ("$ErrorActionPreference = 'Stop'; "
              "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
              f"try {{ {script} }} catch {{ [Console]::Error.WriteLine($_.Exception.Message); exit 1 }}")
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, encoding="utf-8", errors="replace",
        env={**os.environ, **{f"PARENT_RECAP_{k.upper()}": v for k, v in env.items()}})


def _error(r: subprocess.CompletedProcess) -> str:
    return r.stderr.strip() or f"exit code {r.returncode}"


# ---------------------------------------------------------------- the evening jobs

def _pythonw() -> str:
    """This Python's windowless twin, which a venv on Windows has beside python.exe."""
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    return str(pythonw) if pythonw.exists() else sys.executable


def _next(hour: int, minute: int, now: datetime) -> datetime:
    """The first hour:minute from `now` on, so a new task has no start before it to catch up on."""
    at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return at if at > now else at + timedelta(days=1)


def task_xml(job: jobs.Job, now: datetime | None = None) -> str:
    """The task for `job`, in Task Scheduler's XML. Its times are the PC's own local time."""
    ET.register_namespace("", TASK_NS)

    def add(parent: ET.Element, tag: str, text: str | None = None) -> ET.Element:
        e = ET.SubElement(parent, f"{{{TASK_NS}}}{tag}")
        e.text = text
        return e

    task = ET.Element(f"{{{TASK_NS}}}Task", version="1.2")
    what = "the evening Brief" if job.name == jobs.DAILY else "Weekend Picks"
    add(add(task, "RegistrationInfo"), "Description", f"Parent Recap: {what}")

    trigger = add(add(task, "Triggers"), "CalendarTrigger")
    start = _next(job.hour, job.minute, now or datetime.now())
    add(trigger, "StartBoundary", start.strftime("%Y-%m-%dT%H:%M:%S"))
    add(trigger, "Enabled", "true")
    if job.weekday is None:
        add(add(trigger, "ScheduleByDay"), "DaysInterval", "1")
    else:
        weekly = add(trigger, "ScheduleByWeek")
        add(add(weekly, "DaysOfWeek"), DAYS[job.weekday % 7])
        add(weekly, "WeeksInterval", "1")

    principal = add(add(task, "Principals"), "Principal")
    principal.set("id", "Author")
    user, domain = os.environ.get("USERNAME"), os.environ.get("USERDOMAIN")
    if user:
        add(principal, "UserId", f"{domain}\\{user}" if domain else user)
    # Only while the family is signed in: the passwordless way of running signed out has no
    # network, and the other one would need the family's Windows password (ADR 0021).
    add(principal, "LogonType", "InteractiveToken")
    add(principal, "RunLevel", "LeastPrivilege")

    settings = add(task, "Settings")
    add(settings, "MultipleInstancesPolicy", "IgnoreNew")
    # Task Scheduler's defaults would skip a laptop's evening run on battery.
    add(settings, "DisallowStartIfOnBatteries", "false")
    add(settings, "StopIfGoingOnBatteries", "false")
    add(settings, "StartWhenAvailable", "true")
    add(settings, "WakeToRun", "true")
    add(settings, "Enabled", "true")

    actions = add(task, "Actions")
    actions.set("Context", "Author")
    run = add(actions, "Exec")
    add(run, "Command", _pythonw())
    add(run, "Arguments", subprocess.list2cmdline([
        "-m", RUNNER,
        str(job.log_dir / f"{job.subcommand}-stdout.log"),
        str(job.log_dir / f"{job.subcommand}-stderr.log"),
        sys.executable, "-m", "family_brief", job.subcommand]))
    add(run, "WorkingDirectory", str(Path.home()))
    return ET.tostring(task, encoding="unicode")


def install(wanted: list[jobs.Job]) -> None:
    for job in wanted:
        r = _powershell("Register-ScheduledTask -TaskName $env:PARENT_RECAP_TASK -TaskPath '\\' "
                        "-Xml $env:PARENT_RECAP_XML -Force | Out-Null",
                        task=system_name(job.name), xml=task_xml(job))
        if r.returncode != 0:
            raise RuntimeError(f"Task Scheduler didn't register {system_name(job.name)}: "
                               f"{_error(r)}")
    kept = {system_name(job.name) for job in wanted}
    for task in _tasks():
        if task["name"] not in kept:
            _unregister(task["name"])


def _unregister(task: str) -> None:
    r = _powershell("Unregister-ScheduledTask -TaskName $env:PARENT_RECAP_TASK -TaskPath '\\' "
                    "-Confirm:$false", task=task)
    if r.returncode != 0:
        raise RuntimeError(f"Task Scheduler didn't remove {task}: {_error(r)}")


def _tasks() -> list[dict[str, Any]]:
    """Our tasks, each with its name, state ("Ready", "Running", "Disabled"...), and the program
    and arguments it starts."""
    r = _powershell(
        "$ours = @(Get-ScheduledTask -TaskPath '\\' -ErrorAction SilentlyContinue | "
        "Where-Object { $_.TaskName.StartsWith($env:PARENT_RECAP_PREFIX) }); "
        "ConvertTo-Json -Compress -InputObject @($ours | ForEach-Object { @{ "
        "name = $_.TaskName; state = [string]$_.State; "
        "execute = [string]$_.Actions[0].Execute; arguments = [string]$_.Actions[0].Arguments } })",
        prefix=PREFIX)
    if r.returncode != 0:
        raise RuntimeError(f"Task Scheduler's tasks couldn't be read: {_error(r)}")
    return json.loads(r.stdout or "[]")


def remove_all() -> None:
    for task in _tasks():
        _unregister(task["name"])


def installed() -> set[str]:
    return {task["name"].removeprefix(PREFIX) for task in _tasks()
            if task["state"] != "Disabled"}


def waiting_for_login() -> set[str]:
    # A task is registered whether or not anyone is signed in, and runs only while the family is;
    # doctor checks instead that Windows signs them back in after an update restart.
    return set()


def _split(command_line: str) -> list[str]:
    """A command line split as Windows splits it for a program."""
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)  # type: ignore[attr-defined]
    shell32.CommandLineToArgvW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
    shell32.CommandLineToArgvW.restype = ctypes.POINTER(ctypes.c_wchar_p)
    kernel32 = ctypes.WinDLL("kernel32")  # type: ignore[attr-defined]
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    n = ctypes.c_int()
    # The first word is the program's name, which Windows splits by other rules.
    argv = shell32.CommandLineToArgvW(f"x {command_line}", ctypes.byref(n))
    if not argv:
        return []
    try:
        return [argv[i] for i in range(1, n.value)]
    finally:
        kernel32.LocalFree(argv)


def _program(task: dict[str, Any]) -> list[str]:
    """The command a task runs: the job's own, for one started through `task_run`."""
    argv = [task["execute"], *_split(task["arguments"])] if task["execute"] else []
    if argv[1:3] == ["-m", RUNNER]:
        return argv[5:]
    return argv


def found() -> list[jobs.Found]:
    return [jobs.Found(t["name"], _where(t["name"]), _program(t)) for t in _tasks()]


def program(name: str) -> list[str] | None:
    for task in _tasks():
        if task["name"] == system_name(name):
            return _program(task) or None
    return None


def run_now(name: str) -> None:
    r = _powershell("$t = Get-ScheduledTask -TaskName $env:PARENT_RECAP_TASK -TaskPath '\\' "
                    f"-ErrorAction SilentlyContinue; if (-not $t) {{ exit {NO_TASK} }}; "
                    f"if ([string]$t.State -eq 'Running') {{ exit {RUNNING} }}; "
                    "Start-ScheduledTask -InputObject $t", task=system_name(name))
    if r.returncode == NO_TASK:
        raise jobs.NotInstalled()
    if r.returncode == RUNNING:
        raise jobs.AlreadyRunning()
    if r.returncode != 0:
        raise jobs.NotStarted(f"Task Scheduler couldn't start {system_name(name)}: {_error(r)}")


# ---------------------------------------------------------------- one-off runs

def run_detached(args: list[str], config: str | None, timeout: int,
                 progress: Callable[[str], None]) -> tuple[int | None, str]:
    """`jobs.run_detached` as a hidden child process. Windows gives no program permissions of its
    own, as macOS does for WhatsApp, so one started from here can do what the evening job can.
    Its exit code is None when it was stopped."""
    prog = [sys.executable, "-m", "family_brief"]
    if config:
        prog += ["-c", str(Path(config).expanduser())]
    code: int | None = None
    with tempfile.TemporaryDirectory(prefix="parent-recap-bg-") as work:
        out = Path(work) / "output.log"
        with out.open("wb") as log:
            proc = subprocess.Popen(
                prog + args, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                cwd=Path.home(), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                env={**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONUTF8": "1", jobs.BG_ENV: "1"})
            try:
                deadline = time.time() + timeout
                while code is None and time.time() < deadline:
                    time.sleep(1)
                    code = proc.poll()
                    progress(out.read_text(encoding="utf-8", errors="replace"))
            finally:
                if code is None:  # timed out, or this process is being stopped
                    proc.kill()
                    proc.wait()
        text = out.read_text(encoding="utf-8", errors="replace")
    return code, text
