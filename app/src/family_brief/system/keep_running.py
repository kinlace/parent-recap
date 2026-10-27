"""What a Service's job runs: `python -m family_brief.system.keep_running <stdout log> <stderr log>
<command...>` (ADR 0026).

It runs the command as `task_run` does, its output appended to the two log files, and starts it
again a little after each exit, except an exit with LINK_ENDED, after which it exits with 0. Neither
launchd nor Task Scheduler can tell one exit code from the others: launchd's KeepAlive restarts
any exit but 0, and Task Scheduler's restart on failure covers a task that fails to start, not a
program's exit code. So the job's own restarting is only for this runner itself ending."""
from __future__ import annotations

import ctypes
import sys
import time
from datetime import datetime

from . import task_run

# The Service's program exits with it when it must not be started again until it's installed
# anew, such as the Linked Device after WhatsApp ended its link. None of Node's own exit codes
# (1-14), Python's (1, 2) or a signal's (128 and up).
LINK_ENDED = 20
PAUSE = 10  # seconds before a start again, so a program that can't start doesn't spin


def main(argv: list[str]) -> int:
    stdout, stderr, *command = argv
    _ends_with_this_process()
    while True:
        code = task_run.main([stdout, stderr, *command])
        if code == LINK_ENDED:
            return 0
        with open(stderr, "a", encoding="utf-8") as err:
            print(f"{datetime.now():%Y-%m-%d %H:%M:%S} keep_running: exited with {code}, starting "
                  f"again in {PAUSE} seconds", file=err)
        time.sleep(PAUSE)


def _ends_with_this_process() -> None:
    """On Windows, puts this process in a job object that ends the program with it, as ending
    the task ends only this process. launchd ends a job's whole process group itself."""
    if sys.platform != "win32":
        return
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]

    class Limits(ctypes.Structure):  # JOBOBJECT_BASIC_LIMIT_INFORMATION
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                    ("PerJobUserTimeLimit", ctypes.c_int64), ("LimitFlags", ctypes.c_uint32),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", ctypes.c_uint32),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", ctypes.c_uint32),
                    ("SchedulingClass", ctypes.c_uint32)]

    class Counters(ctypes.Structure):  # IO_COUNTERS
        _fields_ = [(n, ctypes.c_uint64) for n in ("ReadOps", "WriteOps", "OtherOps", "ReadBytes",
                                                   "WriteBytes", "OtherBytes")]

    class ExtendedLimits(ctypes.Structure):  # JOBOBJECT_EXTENDED_LIMIT_INFORMATION
        _fields_ = [("Basic", Limits), ("Io", Counters), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    kernel32.CreateJobObjectW.restype = ctypes.c_void_p
    kernel32.SetInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p,
                                                 ctypes.c_uint32]
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    kernel32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    job = kernel32.CreateJobObjectW(None, None)
    limits = ExtendedLimits()
    limits.Basic.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    # 9: JobObjectExtendedLimitInformation. The handle stays open, and isn't inherited, until this
    # process ends; then Windows ends every process in the job. Best effort: without it the
    # program outlives the task, as it would anyway.
    if job and kernel32.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
        kernel32.AssignProcessToJobObject(job, kernel32.GetCurrentProcess())


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
