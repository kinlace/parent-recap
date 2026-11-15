"""`install.ps1`, the Windows install (ADR 0019), stays in step with `install.sh` and hands over to
the release's `app/windows_install.py` once it has the pinned Python (#990). CI's windows-install
job runs it for real on a clean Windows runner, with the real downloads."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def pins(script: str, pattern: str) -> dict[str, str]:
    return dict(re.findall(pattern, (ROOT / script).read_text(), re.M))


def test_windows_gets_the_python_and_node_versions_macos_gets():
    sh = pins("install.sh", r"^(PYTHON_VERSION|PYTHON_BUILD|NODE_VERSION)=(\S+)$")
    ps1 = pins("install.ps1", r"^\s*\$(PythonVersion|PythonBuild|PythonSha256) = '([^']*)'$")
    py = pins("app/windows_install.py", r'^(PYTHON_VERSION|PYTHON_BUILD|PYTHON_SHA256|NODE_VERSION) = "([^"]*)"$')

    assert (ps1["PythonVersion"], ps1["PythonBuild"]) == (sh["PYTHON_VERSION"], sh["PYTHON_BUILD"])
    # The Python install.ps1 downloads is the one the installer it hands over to expects.
    assert (py["PYTHON_VERSION"], py["PYTHON_BUILD"], py["PYTHON_SHA256"]) == (
        ps1["PythonVersion"], ps1["PythonBuild"], ps1["PythonSha256"])
    assert py["NODE_VERSION"] == sh["NODE_VERSION"]


def test_it_hands_over_to_the_release_s_installer_once_it_has_the_pinned_python():
    script = (ROOT / "install.ps1").read_text()
    body = script.split("function Install-ParentRecap {", 1)[1]

    assert body.index("Install-Runtime 'Python'") < body.index("'app\\windows_install.py'")
    # By path and with -I, on the pinned Python, appending to the same dated log.
    assert "$installer = @('-I', (Join-Path $plugin 'app\\windows_install.py'), '--log', $Log)" in body
    assert "if ($fromDownload) { $installer += '--downloaded' }" in body
    handover = script.split("$installer = @(Install-ParentRecap)", 1)[1]
    assert "& $Python @installer\n      $failed = $LASTEXITCODE -ne 0" in handover
    # The Node and everything after the Python are the installer's.
    assert "Node" not in script_without_comments(script)
    assert "family_brief" not in script_without_comments(script.split("$failed = $false", 1)[0])


def test_the_pasted_line_ends_on_the_setup_page_and_a_file_run_only_installs():
    script = (ROOT / "install.ps1").read_text()
    end = script.split("$installer = @(Install-ParentRecap)\n  } catch", 1)[1]

    # The hand-over comes before the exit and the page, so either waits for its result.
    assert end.index("& $Python @installer") < end.index("if ($PSCommandPath) { exit [int]$failed }")
    # As get.sh on a Mac: the family pastes one line and lands on the page, served from the window.
    opened = re.search(r"if \(-not \$failed -and -not \$PSCommandPath\) \{(.*?)\n  \}", end, re.S)
    assert opened, "the page opens only after an install that worked, pasted"
    # On the venv's Python, not pip's launcher, which Defender can block (#591).
    assert "& (Join-Path $App '.venv\\Scripts\\python.exe') -m family_brief setup page" in opened.group(1)
    assert "Keep this PowerShell window open" in opened.group(1)
    # Run as a file, it exits with the install's result, not the last program it ran: CI runs it
    # with `&` in a PowerShell with PSReadLine loaded, as a family's window has (#374).
    assert "if ($PSCommandPath) { exit [int]$failed }" in end


def test_run_as_administrator_it_stops_before_it_makes_anything():
    # What it would make there, the evening task couldn't open (#464).
    body = (ROOT / "install.ps1").read_text().split("function Install-ParentRecap {", 1)[1]

    assert body.index("if (Test-Elevated) {") < body.index("New-Item")
    assert "Run as administrator" in body.split("if (Test-Elevated) {", 1)[1].split("}", 1)[0]


def script_without_comments(script: str) -> str:
    return "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("#"))


def test_install_ps1_and_its_installer_are_ascii_only():
    # Windows PowerShell 5.1 reads a script file without a byte order mark in the PC's own code
    # page, so anything else would come out garbled; the installer's text goes to the same window.
    (ROOT / "install.ps1").read_bytes().decode("ascii")
    (ROOT / "app" / "windows_install.py").read_bytes().decode("ascii")
