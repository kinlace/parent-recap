"""Opening pages, files and terminal windows, and which macOS-only steps and checks are offered
(ADR 0020).

The outside edges are faked: `open`, which the harness records, and the system the program runs on.
A terminal window is its script, run by `/bin/sh` the way Terminal runs it."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from family_brief.system import desktop


def test_a_terminal_window_runs_its_command_in_the_home_folder_and_leaves_nothing(harness):
    ran = harness.home / "ran"

    assert desktop.open_terminal(
        [sys.executable, "-c", "import os, sys; open(sys.argv[1], 'w').write("
                               "os.getcwd() + '|' + os.environ['GREETING'])", str(ran)],
        {"GREETING": "hello 'there'"}, "Parent Recap test")

    script = Path(harness.opened[-1])
    assert script.name == "Parent Recap test.command" and os.access(script, os.X_OK)
    subprocess.Popen(["/bin/sh", str(script)], env={**os.environ, "HOME": str(harness.home)},
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).wait(10)
    assert ran.read_text() == f"{harness.home}|hello 'there'"
    assert not script.parent.exists()  # the script and its folder go once Terminal has run it
