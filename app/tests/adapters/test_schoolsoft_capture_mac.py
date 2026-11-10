"""The SchoolSoft capture tool's Mac edges (tools/schoolsoft-capture, #876): the Keychain item,
through a stand-in for /usr/bin/security that keeps what it is given and logs its command lines,
and the evening check's launchd job, through a recorded `launchctl`."""
from __future__ import annotations

import plistlib
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("fcntl")  # the tool runs on a Mac
from fake_schoolsoft import TOOL_DIR, tool  # noqa: E402

pytestmark = pytest.mark.posix  # shell-script stand-ins

TOKEN = "rt.fake-refresh-token"


@pytest.fixture
def security(tmp_path: Path) -> Path:
    """`security` as the Keychain answers the four commands the tool sends, with one item."""
    item, argv = tmp_path / "item", tmp_path / "argv.log"
    script = tmp_path / "security"
    script.write_text(f"""#!/bin/bash
echo "$*" >> "{argv}"
case "$1" in
  -i) read -r line; [[ "$line" =~ -w\\ \\"([^\\"]*)\\" ]] && printf %s "${{BASH_REMATCH[1]}}" > "{item}" ;;
  find-generic-password) [ -f "{item}" ] || exit 44; [ "${{@: -1}}" = -w ] && cat "{item}" && echo ;;
  delete-generic-password) [ -f "{item}" ] || exit 44; rm "{item}" ;;
esac
""", encoding="utf-8")
    script.chmod(0o755)
    return script


def test_the_token_is_typed_into_security_never_on_its_command_line(security: Path):
    keychain = tool.Keychain(security=str(security))
    assert keychain.get() is None
    keychain.put(TOKEN)
    assert keychain.get() == TOKEN
    lines = (security.parent / "argv.log").read_text(encoding="utf-8")
    assert TOKEN not in lines
    assert "-i" in lines.splitlines()
    keychain.delete()
    assert keychain.get() is None


def test_a_token_security_doesn_t_keep_is_an_error(tmp_path: Path):
    script = tmp_path / "security"
    script.write_text("#!/bin/bash\nexit 44\n", encoding="utf-8")
    script.chmod(0o755)
    with pytest.raises(OSError, match="didn't keep"):
        tool.Keychain(security=str(script)).put(TOKEN)


def test_a_token_with_characters_a_command_can_t_take_is_refused(security: Path):
    with pytest.raises(ValueError):
        tool.Keychain(security=str(security)).put('rt"; delete-keychain')


def test_the_evening_job_runs_the_check_on_this_python_every_evening(tmp_path: Path):
    calls: list[list[str]] = []

    def run(cmd: list[str], **_k) -> subprocess.CompletedProcess:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)
    agent = tool.LaunchAgent(run=run, agents=tmp_path / "LaunchAgents")
    agent.install(tmp_path / "SchoolSoftCapture")
    plist = plistlib.loads(agent.path.read_bytes())
    assert agent.path.name == "com.kinlace.schoolsoft-capture.plist"
    assert plist["ProgramArguments"] == [sys.executable, "-E", "-s",
                                         str((TOOL_DIR / "schoolsoft_capture.py").resolve()), "refresh"]
    assert plist["StartCalendarInterval"] == {"Hour": 20, "Minute": 15} and plist["RunAtLoad"] is False
    assert calls[-1][:2] == ["launchctl", "bootstrap"] and calls[-1][-1] == str(agent.path)
    agent.remove()
    assert not agent.path.exists()
    assert calls[-1][:2] == ["launchctl", "bootout"] and calls[-1][-1].endswith("/com.kinlace.schoolsoft-capture")
