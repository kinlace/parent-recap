"""What an evening job's scheduled task runs on Windows: `pythonw -m family_brief.system.task_run
<stdout log> <stderr log> <command...>`.

Task Scheduler gives a program no log files, and opens a console window on the family's desktop
for python.exe. pythonw.exe opens none, so the task starts it with this module, which runs the
job's own command hidden, its output appended to the job's two log files as launchd does on macOS,
and exits with its exit code, which Task Scheduler shows as the task's last result."""
from __future__ import annotations

import os
import subprocess
import sys

# A Windows Python writes files in the PC's own code page unless told otherwise; the program
# writes its files as UTF-8 without saying so.
UTF8 = {"PYTHONUTF8": "1"}


def main(argv: list[str]) -> int:
    stdout, stderr, *command = argv
    with open(stdout, "ab") as out, open(stderr, "ab") as err:
        return subprocess.run(command, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                              env={**os.environ, **UTF8},
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
