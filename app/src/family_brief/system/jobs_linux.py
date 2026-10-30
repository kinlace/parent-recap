"""The Linux adapter of `jobs`: systemd user units in ~/.config/systemd/user, a timer and the
service it starts for each evening job, run with `systemctl --user`. The Household's user lingers
(`loginctl enable-linger`), so its timers run with nobody signed in. Each run is held to LIMITS,
so a runaway one can't starve the gateway the same server runs (ADR 0029).

The always-running job, the Linked Device's, has no place on a server without WhatsApp (ADR 0029):
installing, removing and stopping it only say so. A run by hand (`parent-recap run`) needs no job,
and has the evening job's permissions already, so there is no one-off background run either."""
from __future__ import annotations

import getpass
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Callable

from .. import private_files, tools
from . import jobs  # its names are read at call time: jobs imports this module first

UNITS = Path.home() / ".config" / "systemd" / "user"
PREFIX = "parent-recap-"
OURS = (f"{PREFIX}daily", f"{PREFIX}weekend-events")
# Each evening run's share of the server, which ten Households' runs at once share with the
# gateway: half a core, and a memory ceiling a full run fits under. Tuned after a real run (#583).
LIMITS = {"CPUQuota": "50%", "MemoryMax": "400M"}
NO_ALWAYS = ("The Linked Device's always-running job isn't available on Linux: a server has no "
             "WhatsApp")
NO_BACKGROUND = ("A background run isn't available on Linux: run parent-recap with the same "
                 "command directly, as the Household's user")
# systemd's own days, by the job's weekday: 0 and 7 are Sunday, as launchd has them.
DAYS = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def system_name(name: str) -> str:
    return PREFIX + name


def _name(unit: str) -> str:
    return unit.removeprefix(PREFIX)


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    # Run as the Household's user from a root shell (`sudo -u`), there is no session to say
    # where the user's systemd listens; lingering keeps it running there.
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", env=env)


def _systemctl(*args: str) -> subprocess.CompletedProcess:
    return _run(["systemctl", "--user", *args])


def _why(r: subprocess.CompletedProcess) -> str:
    return r.stderr.strip() or str(r.returncode)


# ---------------------------------------------------------------- lingering

def _lingers() -> bool:
    r = _run(["loginctl", "show-user", getpass.getuser(), "--property=Linger", "--value"])
    return r.returncode == 0 and r.stdout.strip() == "yes"


def _linger() -> None:
    if _lingers():
        return
    user = getpass.getuser()
    r = _run(["loginctl", "enable-linger", user])
    if r.returncode != 0:
        raise jobs.NotStarted(
            f"the evening jobs would run only while {user} is signed in, and lingering, which "
            f"runs them without, couldn't be turned on: {_why(r)}. "
            f"An administrator turns it on with: sudo loginctl enable-linger {user}")


# ---------------------------------------------------------------- the evening jobs

def _escape(value: str) -> str:
    """`value` as a unit file takes it literally, with systemd's specifiers off."""
    return value.replace("%", "%%")


def _command_line(command: list[str]) -> str:
    """`command` as ExecStart takes it literally: a command line also expands $VARIABLES."""
    return _escape(shlex.join(command)).replace("$", "$$")


def _service(job: jobs.Job) -> str:
    command = [sys.executable, "-m", "family_brief", job.subcommand]
    env = {"PATH": _path_env(), "HOME": str(Path.home())}
    log = job.log_dir / job.subcommand
    lines = [
        "[Unit]",
        f"Description=Parent Recap: {job.subcommand}",
        "",
        "[Service]",
        "Type=oneshot",
        f"ExecStart={_command_line(command)}",
        f"WorkingDirectory={_escape(str(Path.home()))}",
        "Environment=" + " ".join(_escape(f'"{k}={v}"') for k, v in env.items()),
        f"UMask={private_files.UMASK:04o}",  # systemd creates the log files with the job's umask
        f"StandardOutput=append:{_escape(f'{log}-stdout.log')}",
        f"StandardError=append:{_escape(f'{log}-stderr.log')}",
        *(f"{k}={v}" for k, v in LIMITS.items()),
    ]
    return "\n".join(lines) + "\n"


def _timer(job: jobs.Job) -> str:
    day = "" if job.weekday is None else f"{DAYS[job.weekday]} "
    zone = f" {job.timezone}" if job.timezone else ""
    return "\n".join([
        "[Unit]",
        f"Description=Parent Recap: {job.subcommand}, at its time",
        "",
        "[Timer]",
        f"OnCalendar={day}*-*-* {job.hour:02d}:{job.minute:02d}:00{zone}",
        f"Unit={system_name(job.name)}.service",
        "Persistent=true",  # a run missed while the server was down runs once it's back
        "",
        "[Install]",
        "WantedBy=timers.target",
    ]) + "\n"


def install(wanted: list[jobs.Job]) -> None:
    _linger()  # first, so no timer is left that waits for a sign-in
    UNITS.mkdir(parents=True, exist_ok=True)
    for job in wanted:
        unit = system_name(job.name)
        (UNITS / f"{unit}.service").write_text(_service(job), encoding="utf-8")
        (UNITS / f"{unit}.timer").write_text(_timer(job), encoding="utf-8")
    _systemctl("daemon-reload")
    for job in wanted:
        timer = f"{system_name(job.name)}.timer"
        # Restarted, so a timer running already takes its new time.
        for verb in ("enable", "restart"):
            r = _systemctl(verb, timer)
            if r.returncode != 0:
                raise jobs.NotStarted(f"systemctl couldn't {verb} {timer}: {_why(r)}")
    kept = {system_name(job.name) for job in wanted}
    for unit in OURS:
        if unit not in kept:
            _remove(unit)


def _files(unit: str) -> list[Path]:
    return [p for p in (UNITS / f"{unit}.timer", UNITS / f"{unit}.service") if p.exists()]


def _remove(unit: str) -> None:
    files = _files(unit)
    if not files:
        return
    _systemctl("disable", "--now", f"{unit}.timer")
    _systemctl("stop", f"{unit}.service")
    for p in files:
        p.unlink()
    _systemctl("daemon-reload")


def remove_all() -> None:
    for unit in OURS:
        _remove(unit)


def installed() -> set[str]:
    if not _lingers():  # the timers would wait for a sign-in
        return set()
    return {_name(unit) for unit in OURS if _systemctl("is-active", f"{unit}.timer").returncode == 0}


def waiting_for_login() -> set[str]:
    return set()  # a timer without lingering isn't installed: schedule install turns it on


def _program(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    for line in text.splitlines():
        if line.startswith("ExecStart="):
            try:
                return shlex.split(line.removeprefix("ExecStart=").replace("$$", "$")
                                   .replace("%%", "%"))
            except ValueError:
                return []
    return []


def found() -> list[jobs.Found]:
    return [jobs.Found(unit, files[-1], _program(UNITS / f"{unit}.service"))
            for unit in OURS if (files := _files(unit))]


def program(name: str) -> list[str] | None:
    return _program(UNITS / f"{system_name(name)}.service") or None


def run_now(name: str) -> None:
    service = f"{system_name(name)}.service"
    probe = _systemctl("show", "--property=LoadState,ActiveState", service)
    state = dict(line.split("=", 1) for line in probe.stdout.splitlines() if "=" in line)
    if probe.returncode != 0 or state.get("LoadState") != "loaded":
        raise jobs.NotInstalled()
    if state.get("ActiveState") in ("active", "activating"):  # a oneshot runs as activating
        raise jobs.AlreadyRunning()
    start = _systemctl("start", "--no-block", service)
    if start.returncode != 0:
        raise jobs.NotStarted(f"systemctl couldn't start {service}: {_why(start)}")


def _path_env() -> str:
    dirs: list[str] = []
    # The wilma CLI runs on Parent Recap's own Node (ADR 0011).
    node = tools.find("node")
    if node:
        dirs += [str(Path(node).resolve().parent), str(Path(node).parent)]
    dirs += [str(Path.home() / ".local" / "bin"), "/usr/local/bin", "/usr/bin", "/bin"]
    return ":".join(dict.fromkeys(dirs))


# ---------------------------------------------------------------- the always-running job

def install_always(program: list[str], log_dir: Path) -> None:
    print(NO_ALWAYS, file=sys.stderr)


def remove_always() -> None:
    print(NO_ALWAYS, file=sys.stderr)


def always_status() -> jobs.AlwaysStatus:
    return jobs.AlwaysStatus(installed=False, running=False)


def stop_always() -> None:
    pass


# ---------------------------------------------------------------- one-off runs

def run_detached(args: list[str], config: str | None, timeout: int,
                 progress: Callable[[str], None]) -> tuple[int | None, str]:
    raise RuntimeError(NO_BACKGROUND)
