"""What an evening job's scheduled task runs on Windows: `pythonw -m family_brief.system.task_run
<stdout log> <stderr log> <command...>`.

Task Scheduler gives a program no log files, and opens a console window on the family's desktop
for python.exe. pythonw.exe opens none, so the task starts it with this module, which runs the
job's own command hidden, its output appended to the job's two log files as launchd does on macOS,
and exits with its exit code, which Task Scheduler shows as the task's last result.

The always-running job's task starts it with `--restart-unless <code>` first: the command is run
again, after RESTART_AFTER seconds, each time it exits with another code. Task Scheduler's own
restart on failure is for a task that couldn't start, not one whose program exits, so this does
what launchd's KeepAlive does on macOS. Ending its task ends the command too."""
from __future__ import annotations

import os
import subprocess
import sys
import time

# A Windows Python writes files in the PC's own code page unless told otherwise; the program
# writes its files as UTF-8 without saying so.
UTF8 = {"PYTHONUTF8": "1"}
RESTART_AFTER = 10  # seconds, as launchd waits before starting a job that exited again


def main(argv: list[str]) -> int:
    unless: int | None = None
    if argv[:1] == ["--restart-unless"]:
        unless, argv = int(argv[1]), argv[2:]
    stdout, stderr, *command = argv
    ends_with_this = _ends_with_this_process() \
        if unless is not None and sys.platform == "win32" else None
    with open(stdout, "ab") as out, open(stderr, "ab") as err:
        while True:
            proc = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                    env={**os.environ, **UTF8},
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if ends_with_this:
                ends_with_this(proc)
            code = proc.wait()
            if unless is None or code == unless:
                return code
            time.sleep(RESTART_AFTER)


def _ends_with_this_process():
    """A function that ties a process to this one, so it ends when this one does. Task
    Scheduler's End, and stopping the always-running job, end only the process the task started:
    this one."""
    import ctypes
    from ctypes import wintypes

    class Basic(ctypes.Structure):  # JOBOBJECT_BASIC_LIMIT_INFORMATION
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                    ("PerJobUserTimeLimit", ctypes.c_int64), ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD), ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

    class Extended(ctypes.Structure):  # JOBOBJECT_EXTENDED_LIMIT_INFORMATION
        _fields_ = [("BasicLimitInformation", Basic),
                    ("IoInfo", ctypes.c_ulonglong * 6), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                                 wintypes.DWORD]
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    # The job object closes when this process ends, however it ends, which kills what's in it.
    job = kernel32.CreateJobObjectW(None, None)
    info = Extended()
    info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))  # Extended

    def tie(proc: subprocess.Popen) -> None:
        kernel32.AssignProcessToJobObject(job, int(proc._handle))  # type: ignore[attr-defined]
    return tie


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
