"""The Windows adapter of `jobs`: Task Scheduler tasks in its root folder, registered, read and
started through schtasks.exe, none of which needs administrator rights. PowerShell's ScheduledTasks
commands do the same through CIM, which takes seconds a call where schtasks takes milliseconds
(#468).

A task runs only while the family is signed in ("run only when user is logged on"), locked or
asleep, wakes the PC to run ("wake the computer to run this task"), and runs as soon as it can
after a start it missed (ADR 0021). A start missed because nobody was signed in isn't caught up
that way, so the evening job has a second task, at sign-in, that runs it if it was missed. Each
starts pythonw.exe with `task_run`, which gives the job its log files without opening a window on
the family's desktop. The always-running job's task starts at sign-in, and its `task_run` starts
the job again whenever it exits (`always_xml`)."""
from __future__ import annotations

import codecs
import csv
import ctypes
import os
import re
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from . import jobs, task_run  # jobs' names are read at call time: jobs imports this module first

PREFIX = "Parent Recap "
CATCH_UP = "daily catch-up"  # the evening job's sign-in task (`catch_up_xml`)
TASK_NS = "http://schemas.microsoft.com/windows/2004/02/mit/task"
DAYS = ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")
RUNNER = "family_brief.system.task_run"
# A task's Last Result while it runs (SCHED_S_TASK_RUNNING), in schtasks' list of it; its Status
# column says "Running" in the PC's own language.
RUNNING = "267009"
# Where Last Result is in that list, `/Query /FO CSV /V`, whose columns don't move.
LAST_RESULT = 6
STOP_WAIT = 15  # seconds the always-running job's task gets to end once stopped


def system_name(name: str) -> str:
    return PREFIX + name


def _where(task: str) -> Path:
    """Where Task Scheduler keeps a task of its root folder."""
    return Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "Tasks" / task


def _schtasks(*args: str) -> subprocess.CompletedProcess:
    """Runs schtasks.exe with `args`, its output as text. A failure's message goes to stderr."""
    r = subprocess.run(["schtasks.exe", *args], capture_output=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return subprocess.CompletedProcess(r.args, r.returncode, _text(r.stdout), _text(r.stderr))


def _text(out: bytes) -> str:
    """schtasks' output, which is in the console's code page: UTF-8 on a PC set to it, else the
    PC's OEM one; or UTF-16 for some of its XML."""
    if out.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)) or b"\x00" in out[:64]:
        return out.decode("utf-16", errors="replace").lstrip("\ufeff")
    try:
        return out.decode("utf-8")
    except UnicodeDecodeError:
        return out.decode("oem", errors="replace")


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


def _add(parent: ET.Element, tag: str, text: str | None = None) -> ET.Element:
    e = ET.SubElement(parent, f"{{{TASK_NS}}}{tag}")
    e.text = text
    return e


def _user() -> str | None:
    """The signed-in Windows account, as Task Scheduler names it."""
    user, domain = os.environ.get("USERNAME"), os.environ.get("USERDOMAIN")
    return (f"{domain}\\{user}" if domain else user) if user else None


def _task(job: jobs.Job, description: str, trigger: Callable[[ET.Element], None], *,
          wake: bool, more_args: tuple[str, ...] = ()) -> str:
    """A task that runs `job`'s command and `more_args` when `trigger` (handed the Triggers
    element) says, in Task Scheduler's XML; `wake` wakes the PC for it and catches up a missed
    start."""
    return _xml(description, trigger, [
        str(job.log_dir / f"{job.subcommand}-stdout.log"),
        str(job.log_dir / f"{job.subcommand}-stderr.log"),
        sys.executable, "-m", "family_brief", job.subcommand, *more_args], wake=wake)


def _at_sign_in(delay: str | None) -> Callable[[ET.Element], None]:
    """A trigger at the family's sign-in, `delay` (as PT1M) after it."""
    def trigger(triggers: ET.Element) -> None:
        trigger = _add(triggers, "LogonTrigger")
        _add(trigger, "Enabled", "true")
        # Without one it starts at anyone's sign-in:
        # https://learn.microsoft.com/en-us/windows/win32/taskschd/logontrigger-userid
        if user := _user():
            _add(trigger, "UserId", user)
        if delay:
            _add(trigger, "Delay", delay)
    return trigger


def _xml(description: str, trigger: Callable[[ET.Element], None], runner_args: list[str], *,
         wake: bool, always: bool = False) -> str:
    """A task that runs `task_run` with `runner_args` when `trigger` says. `always`: for the
    always-running job, which has no time limit and is started again when it fails to start."""
    ET.register_namespace("", TASK_NS)
    task = ET.Element(f"{{{TASK_NS}}}Task", version="1.2")
    _add(_add(task, "RegistrationInfo"), "Description", description)
    trigger(_add(task, "Triggers"))

    principal = _add(_add(task, "Principals"), "Principal")
    principal.set("id", "Author")
    if user := _user():
        _add(principal, "UserId", user)
    # Only while the family is signed in: the passwordless way of running signed out has no
    # network, and the other one would need the family's Windows password (ADR 0021).
    _add(principal, "LogonType", "InteractiveToken")
    _add(principal, "RunLevel", "LeastPrivilege")

    settings = _add(task, "Settings")
    _add(settings, "MultipleInstancesPolicy", "IgnoreNew")
    # Task Scheduler's defaults would skip a laptop's evening run on battery.
    _add(settings, "DisallowStartIfOnBatteries", "false")
    _add(settings, "StopIfGoingOnBatteries", "false")
    _add(settings, "StartWhenAvailable", str(wake).lower())
    _add(settings, "WakeToRun", str(wake).lower())
    if always:
        # Task Scheduler stops a task after 72 hours unless told otherwise.
        _add(settings, "ExecutionTimeLimit", "PT0S")
        restart = _add(settings, "RestartOnFailure")
        _add(restart, "Interval", "PT1M")
        _add(restart, "Count", "10")
    _add(settings, "Enabled", "true")

    actions = _add(task, "Actions")
    actions.set("Context", "Author")
    run = _add(actions, "Exec")
    _add(run, "Command", _pythonw())
    _add(run, "Arguments", subprocess.list2cmdline(["-m", RUNNER, *runner_args]))
    _add(run, "WorkingDirectory", str(Path.home()))
    return ET.tostring(task, encoding="unicode")


def task_xml(job: jobs.Job, now: datetime | None = None) -> str:
    """The task for `job`, in Task Scheduler's XML. Its times are the PC's own local time."""
    def at_its_time(triggers: ET.Element) -> None:
        trigger = _add(triggers, "CalendarTrigger")
        start = _next(job.hour, job.minute, now or datetime.now())
        _add(trigger, "StartBoundary", start.strftime("%Y-%m-%dT%H:%M:%S"))
        _add(trigger, "Enabled", "true")
        if job.weekday is None:
            _add(_add(trigger, "ScheduleByDay"), "DaysInterval", "1")
        else:
            weekly = _add(trigger, "ScheduleByWeek")
            _add(_add(weekly, "DaysOfWeek"), DAYS[job.weekday % 7])
            _add(weekly, "WeeksInterval", "1")

    what = "the evening Brief" if job.name == jobs.DAILY else "Weekend Picks"
    return _task(job, f"Parent Recap: {what}", at_its_time, wake=True)


def catch_up_xml(job: jobs.Job) -> str:
    """The evening job's second task, which runs it at the family's sign-in if its evening run
    was missed. StartWhenAvailable doesn't catch up a start missed because nobody was signed in,
    and the run itself tells whether one was (`run --if-missed`, #386)."""
    # The run waits for the network too, but Wi-Fi can take a while after signing in.
    return _task(job, "Parent Recap: the evening Brief, if it was missed", _at_sign_in("PT1M"),
                 wake=False, more_args=("--if-missed",))


def always_xml(program: list[str], log_dir: Path) -> str:
    """The always-running job's task: `program`, from the family's sign-in on, started again by
    `task_run` whenever it exits other than with jobs.LINK_ENDED."""
    return _xml("Parent Recap: the Linked Device, kept connected to WhatsApp", _at_sign_in(None),
                ["--restart-unless", str(jobs.LINK_ENDED),
                 str(log_dir / f"{jobs.ALWAYS}-stdout.log"),
                 str(log_dir / f"{jobs.ALWAYS}-stderr.log"), *program], wake=False, always=True)


def _names() -> list[str]:
    """Our tasks in Task Scheduler's root folder."""
    r = _schtasks("/Query", "/FO", "CSV", "/NH")
    # A folder the family may not read makes it fail after listing the others.
    if r.returncode != 0 and not r.stdout.strip():
        raise RuntimeError(f"Task Scheduler's tasks couldn't be read: {_error(r)}")
    names = (row[0].removeprefix("\\") for row in csv.reader(r.stdout.splitlines()) if row)
    return list(dict.fromkeys(n for n in names if n.startswith(PREFIX) and "\\" not in n))


def _remove(kept: set[str]) -> None:
    """Removes our tasks that `kept` doesn't name."""
    for name in _names():
        if name not in kept:
            r = _schtasks("/Delete", "/TN", name, "/F")
            if r.returncode != 0:
                raise RuntimeError(f"Task Scheduler didn't remove {name}: {_error(r)}")


def _register(tasks: dict[str, str]) -> None:
    """Registers each task by its name from its XML, in place of one by that name."""
    with tempfile.TemporaryDirectory(prefix="parent-recap-task-") as work:
        file = Path(work) / "task.xml"
        for name, xml in tasks.items():
            # schtasks refuses a file whose bytes don't match its declared encoding.
            file.write_text('<?xml version="1.0" encoding="UTF-16"?>\n' + xml, encoding="utf-16")
            r = _schtasks("/Create", "/TN", name, "/XML", str(file), "/F")
            if r.returncode != 0:
                raise RuntimeError(f"Task Scheduler didn't register {name}: {_error(r)}")


def install(wanted: list[jobs.Job]) -> None:
    """Registers `wanted` and removes our other tasks but the always-running job's."""
    tasks = {system_name(job.name): task_xml(job) for job in wanted}
    tasks |= {system_name(CATCH_UP): catch_up_xml(job) for job in wanted
              if job.name == jobs.DAILY}
    _register(tasks)
    _remove(kept={*tasks, system_name(jobs.ALWAYS)})


def _tasks() -> list[dict[str, Any]]:
    """Our tasks, each with its name, whether it's enabled, and the program and arguments it
    starts, from its XML."""
    tasks = []
    for name in _names():
        r = _schtasks("/Query", "/TN", name, "/XML")
        if r.returncode != 0:
            raise RuntimeError(f"Task Scheduler's task {name} couldn't be read: {_error(r)}")
        # Its declared encoding is the one schtasks didn't necessarily write it in.
        task = ET.fromstring(re.sub(r"^\s*<\?xml[^>]*\?>", "", r.stdout))
        ns = {"t": TASK_NS}
        tasks.append({"name": name,
                      "enabled": task.findtext("t:Settings/t:Enabled", "true", ns) == "true",
                      "execute": task.findtext("t:Actions/t:Exec/t:Command", "", ns),
                      "arguments": task.findtext("t:Actions/t:Exec/t:Arguments", "", ns)})
    return tasks


def remove_all() -> None:
    remove_always()
    _remove(kept=set())


def installed() -> set[str]:
    names = {task["name"].removeprefix(PREFIX) for task in _tasks() if task["enabled"]}
    # The evening job is both its tasks, so doctor asks for `schedule install` when one is gone,
    # such as after an upgrade from a version without the sign-in task.
    if CATCH_UP not in names:
        names.discard(jobs.DAILY)
    return names - {CATCH_UP, jobs.ALWAYS}


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
        runner_args = argv[3:]
        if runner_args[:1] == ["--restart-unless"]:
            runner_args = runner_args[2:]
        return runner_args[2:]  # after the two log files
    return argv


def found() -> list[jobs.Found]:
    return [jobs.Found(t["name"], _where(t["name"]), _program(t)) for t in _tasks()]


def program(name: str) -> list[str] | None:
    for task in _tasks():
        if task["name"] == system_name(name):
            return _program(task) or None
    return None


def _running(task: str) -> bool:
    r = _schtasks("/Query", "/TN", task, "/FO", "CSV", "/NH", "/V")
    rows = [row for row in csv.reader(r.stdout.splitlines()) if len(row) > LAST_RESULT]
    return r.returncode == 0 and bool(rows) and rows[0][LAST_RESULT] == RUNNING


def run_now(name: str) -> None:
    task = system_name(name)
    if task not in _names():
        raise jobs.NotInstalled()
    if _running(task):
        raise jobs.AlreadyRunning()
    r = _schtasks("/Run", "/TN", task)
    if r.returncode != 0:
        raise jobs.NotStarted(f"Task Scheduler couldn't start {task}: {_error(r)}")


# ---------------------------------------------------------------- the always-running job

def install_always(program: list[str], log_dir: Path) -> None:
    task = system_name(jobs.ALWAYS)
    stop_always()  # a running one keeps its old command, and a second start would be ignored
    _register({task: always_xml(program, log_dir)})
    # Its trigger is the next sign-in; the family is signed in now.
    r = _schtasks("/Run", "/TN", task)
    if r.returncode != 0:
        raise RuntimeError(f"Task Scheduler couldn't start {task}: {_error(r)}")


def remove_always() -> None:
    task = system_name(jobs.ALWAYS)
    if task in _names():
        stop_always()
        r = _schtasks("/Delete", "/TN", task, "/F")
        if r.returncode != 0:
            raise RuntimeError(f"Task Scheduler didn't remove {task}: {_error(r)}")


def always_status() -> jobs.AlwaysStatus:
    task = system_name(jobs.ALWAYS)
    installed = task in _names()
    return jobs.AlwaysStatus(installed=installed, running=installed and _running(task))


def stop_always() -> None:
    task = system_name(jobs.ALWAYS)
    if task in _names() and _running(task):
        r = _schtasks("/End", "/TN", task)
        if r.returncode != 0:
            raise RuntimeError(f"Task Scheduler couldn't stop {task}: {_error(r)}")
        # The task may still be ending, and a start while it is would be ignored (IgnoreNew).
        deadline = time.time() + STOP_WAIT
        while _running(task) and time.time() < deadline:
            time.sleep(0.5)


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
                env={**os.environ, **task_run.UTF8, "PYTHONUNBUFFERED": "1"})
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
