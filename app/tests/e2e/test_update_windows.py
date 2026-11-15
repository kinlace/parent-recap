"""`parent-recap update` on a Windows PC (ADR 0052, #993): everything test_update.py shows of the
update on a Mac holds, and what differs on a PC is here. It runs the verified release's
`app/windows_install.py` with the installed version's Python, never PowerShell or a `.ps1` file;
`app` is a junction, so the switch and the switch back happen under the run lock with the Linked
Device's task stopped; a PC without `ssh-keygen` doesn't update itself; and a step the system
stops is named in the update log with the system's error word for word, the installed version
still running and the scheduled tasks as they were.

On real folders in a temporary home, with `app` a junction on Windows and a symlink elsewhere,
and the switch run as a link that isn't replaced in one step on every system, as on Windows.
Signatures are checked for real, by the system's own `ssh-keygen`, against a test key made for
each test in place of the shipped ones. The network edge is test_update.py's fake GitHub; the
release's installer is a process fake that installs the version in a folder of its own and
switches with the new version's `versions switch`, as windows_install.py does (its own steps are
in tests/scripts/test_windows_install.py and CI's windows-install job). Runs on macOS, Linux and
on Windows' CI (`windows_too`)."""
from __future__ import annotations

import contextlib
import dataclasses
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable
from unittest import mock

import pytest
import requests

from conftest import REAL_RUN
from family_brief import release_signature, run_lock, update, versions
from family_brief.config import Config
from family_brief.schedule_update import HOLDS_RUN_LOCK, UPDATING
from family_brief.system import links, profile_linux, profile_macos, profile_windows
from fake_system import WINDOWS
from test_update import INSTALLED, Releases, release_zip

pytestmark = pytest.mark.windows_too

# The system's own ssh-keygen on the computer the tests run on.
HOST_SSH_KEYGEN = {"win32": profile_windows, "darwin": profile_macos}.get(
    sys.platform, profile_linux).PROFILE.ssh_keygen
# How Defender's attack surface reduction rules stop a step: the system's own words.
BLOCKED = OSError(22, "Operation did not complete successfully because the file contains a virus "
                      "or potentially unwanted software", None, 225)


# ---------------------------------------------------------------- a test key in place of ours

class Keys:
    """A key made for the test, which the program trusts in place of the shipped ones, and
    another it doesn't."""

    def __init__(self, folder: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.folder = folder
        folder.mkdir()
        self.ours, self.other = self.make("workflow"), self.make("other")
        signers = folder / "release_signers"
        public = self.ours.with_suffix(".pub").read_text(encoding="utf-8").strip()
        signers.write_text(f'workflow namespaces="{release_signature.NAMESPACE}" {public}\n',
                           encoding="utf-8", newline="\n")
        verified = release_signature.verified
        monkeypatch.setattr(release_signature, "verified",
                            lambda zip_path, signature: verified(zip_path, signature, signers))

    def make(self, name: str) -> Path:
        key = self.folder / name
        REAL_RUN([str(HOST_SSH_KEYGEN), "-q", "-t", "ed25519", "-N", "", "-C", "", "-f", str(key)],
                 check=True, capture_output=True)
        return key

    def sign(self, data: bytes, key: Path | None = None) -> bytes:
        file = self.folder / "to-sign.zip"
        file.write_bytes(data)
        file.with_name(file.name + ".sig").unlink(missing_ok=True)
        REAL_RUN([str(HOST_SSH_KEYGEN), "-Y", "sign", "-f", str(key or self.ours), "-n",
                  release_signature.NAMESPACE, str(file)], check=True, capture_output=True)
        return file.with_name(file.name + ".sig").read_bytes()


def windows_zip(version: str) -> bytes:
    """A release zip as scripts/release.py builds it, with its Windows installer."""
    return release_zip(version, extra={"parent-recap/app/windows_install.py": "raise SystemExit(1)\n"})


# ---------------------------------------------------------------- the installed program

class PC:
    """Parent Recap installed in %LOCALAPPDATA%\\ParentRecap by the Windows install, running from
    `app`, with its Linked Device's task running and its evening tasks scheduled. The release's
    programs are process fakes: its `windows_install.py` and each version's `doctor`, whose
    failing checks are `fails[version]`; `ssh-keygen` is the system's own."""

    def __init__(self, harness, monkeypatch: pytest.MonkeyPatch) -> None:
        self.harness = harness
        self.folder = harness.home / "AppData" / "Local" / "ParentRecap"
        (self.folder / "runtime" / "python-1").mkdir(parents=True)
        (self.folder / "logs").mkdir()
        self.fails: dict[str, list[str]] = {}
        self.doctor_raises: dict[str, OSError] = {}
        self.installer: Callable[[list[str], dict[str, str]], subprocess.CompletedProcess] = \
            self.installs_and_switches
        self.installer_raises: BaseException | None = None
        # How the installer ends once it has switched: its exit status, or "time limit".
        self.ends_after_switch: int | str | None = None
        self.installs: list[list[str]] = []
        self.install_envs: list[dict[str, str]] = []
        self.at_install: Callable[[], None] | None = None
        # Whether the Linked Device's task ran each time `app` or `previous` was pointed.
        self.linked_device_at_points: list[bool] = []
        # As on Windows: a junction isn't replaced in one step, so the switch happens under the
        # run lock with the Linked Device's task stopped (versions._still).
        monkeypatch.setattr(links, "one_step", lambda: False)
        point = links.point

        def pointing(link: Path, to: Path) -> None:
            self.linked_device_at_points.append(harness.jobs.always_running)
            point(link, to)
        monkeypatch.setattr(links, "point", pointing)
        harness.system = dataclasses.replace(WINDOWS, ssh_keygen=HOST_SSH_KEYGEN)
        running = self.built(INSTALLED, "aaa")
        versions.switch(self.folder, running)
        self.linked_device_at_points.clear()
        harness.jobs.add("daily")
        harness.jobs.install_always([str(self.folder / "app" / ".venv" / "Scripts" / "python.exe"),
                                     "-m", "family_brief.linked_device", "run"], self.folder / "logs")
        # The program runs from its venv, as the tasks and `parent-recap` start it.
        monkeypatch.setattr(sys, "prefix", str(running / ".venv"))
        harness.processes.answers.insert(0, self.answer)

    def built(self, version: str, suffix: str) -> Path:
        v = self.folder / "versions" / f"{version}.{suffix}"
        (v / ".venv" / "Scripts").mkdir(parents=True)
        (v / "VERSION").write_text(f"{version}\n", encoding="utf-8")
        (v / "RUNTIMES").write_text("python-1\n", encoding="utf-8")
        (v / "pyproject.toml").write_text('[project]\nname = "family-brief"\n', encoding="utf-8")
        return v

    def version(self) -> str:
        return versions.version(versions.current(self.folder))

    def python(self) -> str:
        return str(self.folder / "app" / ".venv" / "Scripts" / "python.exe")

    def tasks(self) -> tuple:
        """The scheduled tasks as Task Scheduler has them, and whether the Linked Device's runs."""
        jobs = self.harness.jobs
        return dict(jobs.programs), jobs.always, jobs.always_running

    def answer(self, cmd: list[str], prog: str, **k: Any) -> subprocess.CompletedProcess | None:
        if prog == "ssh-keygen":
            return REAL_RUN(cmd, **k)
        if len(cmd) > 2 and cmd[1] == "-I" and cmd[2].endswith("windows_install.py"):
            self.installs.append(cmd)
            self.install_envs.append(k["env"])
            if self.at_install is not None:
                self.at_install()
            if self.installer_raises is not None:
                raise self.installer_raises
            return self.installer(cmd, k["env"])
        if cmd[:3] == [self.python(), "-m", "family_brief"] and cmd[-1] == "doctor":
            version = self.version()
            if version in self.doctor_raises:
                raise self.doctor_raises[version]
            out = "✅ Config file: fine\n" + "".join(f"❌ {item}: broken in {version}\n"
                                                   for item in self.fails.get(version, []))
            return subprocess.CompletedProcess(cmd, 1 if self.fails.get(version) else 0, out, "")
        if cmd[0] == self.python():
            return subprocess.CompletedProcess(cmd, 0, "", "")  # a switch back's steps
        return None

    def switch_as_the_installer_does(self, version: str, env: dict[str, str]) -> tuple[int, str]:
        """The new version's `versions switch`, as windows_install.py runs it, in the
        environment the update gave the installer: its exit status and what it printed."""
        new = self.built(version, f"x{len(self.installs)}")
        out = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(out):
            code = versions.main(["switch", str(self.folder), str(new)])
        return code, out.getvalue()

    def installs_and_switches(self, cmd: list[str], env: dict[str, str]) -> subprocess.CompletedProcess:
        """The release's windows_install.py: the version in a folder of its own, then the switch,
        then its steps after it, the Linked Device's restart among them."""
        plugin = Path(cmd[2]).parent.parent
        version = json.loads((plugin / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"]
        assert env["PARENT_RECAP_HOME"] == str(self.folder)
        code, said = self.switch_as_the_installer_does(version, env)
        if code != 0:  # as windows_install.py's `stopped` says it
            return subprocess.CompletedProcess(cmd, 1, f"Parent Recap wasn't installed. {said}", "")
        self.harness.jobs.restart_always()
        if self.ends_after_switch == "time limit":
            raise subprocess.TimeoutExpired(cmd, update.INSTALL_TIMEOUT, output="")
        if self.ends_after_switch is not None:
            return subprocess.CompletedProcess(cmd, self.ends_after_switch, "",
                                               "Parent Recap wasn't installed. A step failed.\n")
        return subprocess.CompletedProcess(cmd, 0, f"Parent Recap {version} is installed\n", "")


@pytest.fixture
def github(monkeypatch) -> Releases:
    return Releases(monkeypatch)


@pytest.fixture
def keys(tmp_path, monkeypatch) -> Keys:
    return Keys(tmp_path / "keys", monkeypatch)


@pytest.fixture
def pc(harness, monkeypatch) -> PC:
    return PC(harness, monkeypatch)


def release(github: Releases, keys: Keys, version: str, data: bytes | None = None) -> None:
    """`version` on `stable`, its zip signed by the test key."""
    data = data if data is not None else windows_zip(version)
    github.stable = version
    github.publish(version, data, keys.sign(data))


def update_command(harness, capsys) -> tuple[int, str]:
    code = harness.cli("update")
    return code, capsys.readouterr().out


def update_log(pc: PC) -> str:
    return (pc.folder / "logs" / update.LOG).read_text(encoding="utf-8")


def no_downloads(pc: PC) -> bool:
    folder = pc.folder / update.UPDATE
    return not folder.exists() or [p.name for p in folder.iterdir()] in ([], [update.STATE])


# ---------------------------------------------------------------- what it does

def test_a_newer_release_signed_by_the_key_is_installed_and_the_one_before_kept(
        harness, github, keys, pc, capsys):
    release(github, keys, "0.20.0")

    code, out = update_command(harness, capsys)

    assert code == 0, out
    assert "updated from version 0.19.0 to 0.20.0" in out
    assert pc.version() == "0.20.0"
    assert links.is_link(pc.folder / "app")
    assert versions.version(versions.previous(pc.folder)) == "0.19.0"
    # The verified release's Python installer, run with the installed version's Python, from the
    # plugin copy, as install.ps1 hands over to it.
    running = pc.folder / "versions" / f"{INSTALLED}.aaa"
    assert pc.installs == [[str(running / ".venv" / "Scripts" / "python.exe"), "-I",
                            str(pc.folder / "plugin" / "app" / "windows_install.py"), "--downloaded"]]
    assert no_downloads(pc)
    code, out = update_command(harness, capsys)
    assert code == 0 and "up to date: it runs version 0.20.0" in out


def test_a_release_whose_doctor_fails_is_switched_back_and_kept_as_failed(harness, github, keys, pc, capsys):
    release(github, keys, "0.20.0")
    pc.fails["0.20.0"] = ["Wilma (Mia)"]

    code, out = update_command(harness, capsys)

    assert code == 1
    assert "check failed where version 0.19.0 passed (Wilma (Mia))" in out
    assert "switched back to version 0.19.0" in out
    assert pc.version() == INSTALLED
    assert versions.version(versions.previous(pc.folder)) == "0.20.0"
    assert update.state(pc.folder)["failed"] == "0.20.0"
    assert not (pc.folder / "plugin").exists() and no_downloads(pc)


@pytest.mark.parametrize("ends, says", [(1, "ended with exit status 1"),
                                        ("time limit", "was ended at its time limit of 60 minutes")])
def test_an_installer_that_does_not_finish_after_the_switch_is_switched_back_and_kept_as_failed(
        harness, github, keys, pc, capsys, ends, says):
    release(github, keys, "0.20.0")
    pc.ends_after_switch = ends

    code, out = update_command(harness, capsys)

    assert code == 1
    assert f"its installer didn't finish (windows_install.py {says})" in out
    assert "switched back to version 0.19.0" in out
    assert pc.version() == INSTALLED and update.state(pc.folder)["failed"] == "0.20.0"
    assert f"install: 0.20.0: windows_install.py {says}; app points at 0.20.0" in update_log(pc)


def test_a_changed_zip_is_refused_and_nothing_of_it_runs(harness, github, keys, pc, capsys):
    github.stable = "0.20.0"
    signed = windows_zip("0.20.0")
    changed = release_zip("0.20.0", extra={"parent-recap/app/windows_install.py": "import shutil\n"})
    github.publish("0.20.0", changed, keys.sign(signed))

    code, out = update_command(harness, capsys)

    assert code == 1 and "signature doesn't verify" in out
    assert pc.installs == [] and pc.version() == INSTALLED and no_downloads(pc)
    assert {Path(c[0]).stem for c in harness.commands} == {"ssh-keygen"}


def test_a_zip_signed_by_another_key_is_refused(harness, github, keys, pc, capsys):
    github.stable = "0.20.0"
    data = windows_zip("0.20.0")
    github.publish("0.20.0", data, keys.sign(data, keys.other))

    code, out = update_command(harness, capsys)

    assert code == 1 and "signature doesn't verify" in out
    assert pc.installs == [] and pc.version() == INSTALLED and no_downloads(pc)


@pytest.mark.parametrize("inside", ["0.18.0", "0.19.0", "0.20.1"], ids=["older", "same", "another"])
def test_a_signed_zip_of_another_version_in_the_release_s_place_is_refused_and_nothing_of_it_runs(
        harness, github, keys, pc, capsys, inside):
    release(github, keys, "0.20.0", windows_zip(inside))

    code, out = update_command(harness, capsys)

    assert code == 1 and f"holds version {inside}, not 0.20.0" in out
    assert pc.installs == [] and pc.version() == INSTALLED and no_downloads(pc)
    assert {Path(c[0]).stem for c in harness.commands} == {"ssh-keygen"}


# ---------------------------------------------------------------- where it doesn't update itself

def test_a_pc_without_ssh_keygen_says_it_can_t_update_itself_and_downloads_nothing(
        harness, github, keys, pc, capsys, tmp_path):
    missing = tmp_path / "System32" / "OpenSSH" / "ssh-keygen.exe"
    harness.system = dataclasses.replace(WINDOWS, ssh_keygen=missing)
    release(github, keys, "0.20.0")

    code, out = update_command(harness, capsys)

    assert code == 0
    assert out.strip() == (f"This PC can't update Parent Recap by itself: it has no ssh-keygen at "
                           f"{missing}, the program that checks a release is Parent Recap's. To "
                           "update Parent Recap, paste the install line into PowerShell.")
    assert github.requests == [] and harness.commands == [] and no_downloads(pc)
    # doctor and the setup page say the same.
    assert "can't update Parent Recap by itself" in update.status().line()


def test_an_ssh_keygen_too_old_to_verify_says_the_pc_can_t_update_itself(
        harness, github, keys, pc, capsys, monkeypatch):
    release(github, keys, "0.20.0")

    def too_old(*_a: Any, **_k: Any) -> bool:
        raise release_signature.NoSshKeygen(f"the ssh-keygen at {HOST_SSH_KEYGEN} can't verify signatures")
    monkeypatch.setattr(release_signature, "verified", too_old)

    code, out = update_command(harness, capsys)

    assert code == 1
    assert "This PC can't update Parent Recap by itself" in out
    assert "paste the install line into PowerShell" in out
    assert pc.installs == [] and pc.version() == INSTALLED and no_downloads(pc)


def test_an_install_from_before_versions_is_told_to_paste_the_install_line_into_powershell_once(
        harness, github, monkeypatch, capsys):
    harness.system = dataclasses.replace(WINDOWS, ssh_keygen=HOST_SSH_KEYGEN)
    folder = harness.home / "AppData" / "Local" / "ParentRecap"
    (folder / "runtime").mkdir(parents=True)
    (folder / "app" / ".venv").mkdir(parents=True)
    (folder / "app" / "VERSION").write_text("0.18.0\n", encoding="utf-8")
    monkeypatch.setattr(sys, "prefix", str(folder / "app" / ".venv"))
    github.stable = "0.20.0"

    code, out = update_command(harness, capsys)

    assert code == 0
    assert ("This Parent Recap was installed before it could update itself. Paste the install line "
            "into PowerShell once; from then on it updates itself.") in out
    assert github.requests == [] and harness.commands == []


# ---------------------------------------------------------------- what it never does

def test_it_starts_no_powershell_and_no_ps1_file(harness, github, keys, pc, capsys):
    release(github, keys, "0.20.0")
    pc.fails["0.20.0"] = ["Calendar"]  # the switch back's steps too

    update_command(harness, capsys)

    assert pc.installs and pc.version() == INSTALLED  # it installed, then switched back
    for cmd in harness.commands:
        assert Path(cmd[0]).stem.lower() not in ("powershell", "pwsh", "cmd", "bash", "sh")
        assert not any(str(part).lower().endswith(".ps1") for part in cmd), cmd
    # The installer is told it runs for an update, as on a Mac.
    assert [env[UPDATING] for env in pc.install_envs] == ["1"]


# ---------------------------------------------------------------- the junction, the Linked Device and the lock

def test_the_linked_device_is_stopped_for_the_switch_and_running_after_the_update(
        harness, github, keys, pc, capsys):
    release(github, keys, "0.20.0")

    assert update_command(harness, capsys)[0] == 0

    assert pc.linked_device_at_points and not any(pc.linked_device_at_points)
    assert harness.jobs.always_running and harness.jobs.always is not None


def test_the_linked_device_is_stopped_for_the_switch_back_and_running_after_it(
        harness, github, keys, pc, capsys):
    release(github, keys, "0.20.0")
    pc.fails["0.20.0"] = ["Calendar"]

    code, out = update_command(harness, capsys)

    assert code == 1 and "switched back to version 0.19.0" in out
    # The switch's two links and the switch back's two.
    assert len(pc.linked_device_at_points) == 4 and not any(pc.linked_device_at_points)
    assert harness.jobs.always_running and harness.jobs.always_restarts >= 1


def test_the_update_holds_the_run_lock_for_the_whole_update(harness, github, keys, pc, capsys):
    release(github, keys, "0.20.0")
    pc.fails["0.20.0"] = ["Calendar"]
    cfg = Config.model_validate(harness.config)
    held: dict[str, bool] = {}
    look = github.request

    def at_the_look(*a: Any, **k: Any) -> requests.Response:
        held.setdefault("look", run_lock.is_busy(cfg))
        return look(*a, **k)
    github.request = at_the_look  # type: ignore[method-assign]
    pc.at_install = lambda: held.setdefault("install", run_lock.is_busy(cfg))

    def at_a_step_of_the_switch_back(cmd: list[str], _prog: str, **_k: Any) -> None:
        if cmd[0] == pc.python() and "family_brief.setup_wilma" in cmd:
            held["switch back"] = run_lock.is_busy(cfg)
    harness.processes.answers.insert(0, at_a_step_of_the_switch_back)

    code, out = update_command(harness, capsys)

    assert code == 1 and "switched back to version 0.19.0" in out
    assert held == {"look": True, "install": True, "switch back": True}
    # The installer's switch is told the lock is the update's own, so it doesn't wait on it.
    assert pc.install_envs[0][HOLDS_RUN_LOCK] == str(run_lock.lock_path(cfg))
    assert not run_lock.is_busy(cfg)


def test_an_update_while_a_brief_is_being_made_says_it_is_busy(harness, github, keys, pc, capsys):
    release(github, keys, "0.20.0")
    assert harness.cli("update", "on") == 0
    capsys.readouterr()

    with run_lock.exclusive(Config.model_validate(harness.config)):
        code, out = update_command(harness, capsys)

    assert code == run_lock.BUSY and "Parent Recap is busy" in out
    assert github.requests == [] and pc.installs == []


# ---------------------------------------------------------------- a step the system stops

def stopped_at_the_download(github: Releases, pc: PC, monkeypatch: pytest.MonkeyPatch) -> str:
    error = requests.ConnectionError(BLOCKED)
    request = github.request

    def blocked(session: requests.Session, method: str, url: str, **k: Any) -> requests.Response:
        if url.endswith(".zip"):
            raise error
        return request(session, method, url, **k)
    monkeypatch.setattr(requests.Session, "request", blocked)
    return f"download: 0.20.0: failed: GitHub couldn't be reached (ConnectionError): is this PC online? " \
           f"(ConnectionError: {error})"


def stopped_at_the_unpacking(github: Releases, pc: PC, monkeypatch: pytest.MonkeyPatch) -> str:
    def blocked(*_a: Any, **_k: Any) -> None:
        raise BLOCKED
    monkeypatch.setattr(update.shutil, "copyfileobj", blocked)
    return f"unpack: 0.20.0: failed: the release zip couldn't be unpacked (OSError: {BLOCKED})"


def stopped_at_the_link(github: Releases, pc: PC, monkeypatch: pytest.MonkeyPatch) -> str:
    point = links.point

    def blocked(link: Path, to: Path) -> None:
        if link.name == "app" and "x" in Path(to).name.rsplit(".", 1)[-1]:
            raise BLOCKED
        point(link, to)
    monkeypatch.setattr(links, "point", blocked)
    return f"couldn't switch {pc.folder / 'app'} to the new version (OSError: {BLOCKED})"


def stopped_at_the_installer(github: Releases, pc: PC, monkeypatch: pytest.MonkeyPatch) -> str:
    pc.installer_raises = BLOCKED
    return f"install: 0.20.0: windows_install.py couldn't be started (OSError: {BLOCKED})"


def stopped_at_doctor(github: Releases, pc: PC, monkeypatch: pytest.MonkeyPatch) -> str:
    pc.doctor_raises["0.20.0"] = BLOCKED
    return f"check: 0.20.0's doctor: 1 failing: doctor didn't run (OSError: {BLOCKED})"


@pytest.mark.parametrize("stop", [stopped_at_the_download, stopped_at_the_unpacking,
                                  stopped_at_the_link, stopped_at_the_installer, stopped_at_doctor],
                         ids=["download", "unpacking", "link", "installer", "doctor"])
def test_a_step_the_system_stops_is_in_the_update_log_word_for_word_and_the_installed_version_runs(
        harness, github, keys, pc, capsys, monkeypatch, stop):
    release(github, keys, "0.20.0")
    tasks = pc.tasks()
    logged = stop(github, pc, monkeypatch)

    code, out = update_command(harness, capsys)

    assert code == 1, out
    assert logged in update_log(pc)
    assert pc.version() == INSTALLED
    assert pc.tasks() == tasks
    assert not (pc.folder / "plugin").exists() and no_downloads(pc)
