"""`install.ps1`, the Windows install (ADR 0019), stays in step with `install.sh`. CI's
windows-install job runs it for real on a clean Windows runner, with the real downloads."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def pins(script: str, pattern: str) -> dict[str, str]:
    return dict(re.findall(pattern, (ROOT / script).read_text(), re.M))


def test_windows_gets_the_python_and_node_versions_macos_gets():
    sh = pins("install.sh", r"^(PYTHON_VERSION|PYTHON_BUILD|NODE_VERSION)=(\S+)$")
    ps1 = pins("install.ps1", r"^\s*\$(PythonVersion|PythonBuild|NodeVersion) = '([^']*)'$")

    assert ps1 == {"PythonVersion": sh["PYTHON_VERSION"], "PythonBuild": sh["PYTHON_BUILD"],
                   "NodeVersion": sh["NODE_VERSION"]}


def test_the_pasted_line_ends_on_the_setup_page_and_a_file_run_only_installs():
    script = (ROOT / "install.ps1").read_text()
    end = script.split("Install-ParentRecap\n  } catch", 1)[1]

    # As get.sh on a Mac: the family pastes one line and lands on the page, served from the window.
    opened = re.search(r"if \(-not \$failed -and -not \$PSCommandPath\) \{(.*?)\n  \}", end, re.S)
    assert opened, "the page opens only after an install that worked, pasted"
    assert "& (Join-Path $App '.venv\\Scripts\\parent-recap.exe') setup page" in opened.group(1)
    assert "Keep this PowerShell window open" in opened.group(1)


def test_install_ps1_is_ascii_only():
    # Windows PowerShell 5.1 reads a script file without a byte order mark in the PC's own code
    # page, so anything else would come out garbled.
    (ROOT / "install.ps1").read_bytes().decode("ascii")
