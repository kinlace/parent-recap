"""`parent-recap update` (ADR 0047): it installs the newest release `stable` holds, checks it with
`doctor` and switches back when that fails where the version before passed; and each line of the
statement at the top of family_brief/update.py. A release that says in its zip that it needs the
family (#918) installs with its dialogs when they start it.

Driven through main() on real folders in a temporary HOME. The network edge is a fake GitHub
(`Releases`) behind `requests`, which records every request. The release's own programs are the
harness's process fakes: `ssh-keygen`, which verifies a fake signature made by `Releases`, the
release's `install.sh`, which installs the version beside the running one and switches as the
real one does (versions.switch), and each version's `doctor`. The real signature check is in
test_release_signature.py, and install.sh's in test_install.py."""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import subprocess
import sys
import zipfile
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Callable

import pytest
import requests
import yaml

from family_brief import install_record, release_signature, run_lock, update, versions
from family_brief.config import Config
from fake_system import LINUX, WINDOWS

# install.sh's layout, on a Mac.
pytestmark = pytest.mark.posix

INSTALLED = "0.19.0"
FAMILY = ("parent@example.com", "partner@example.com", "Mia", "Leo", "3B parents")


# ---------------------------------------------------------------- the fake GitHub

def release_zip(version: str, *, name: str = "parent-recap", extra: dict[str, str] | None = None) -> bytes:
    """A release zip as scripts/release.py builds it: one `parent-recap/` folder."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("parent-recap/.claude-plugin/plugin.json",
                   json.dumps({"name": name, "version": version}))
        info = zipfile.ZipInfo("parent-recap/install.sh")
        info.external_attr = 0o100755 << 16
        z.writestr(info, "#!/bin/bash\necho installing\n")
        z.writestr("parent-recap/app/pyproject.toml", '[project]\nname = "family-brief"\n')
        for path, text in (extra or {}).items():
            z.writestr(path, text)
    return out.getvalue()


def signature(data: bytes, key: str = "workflow") -> bytes:
    """The fake ssh-keygen's signature of `data` by `key`."""
    return f"{key}:{release_signature.NAMESPACE}:{hashlib.sha256(data).hexdigest()}\n".encode()


class CutOff(io.BytesIO):
    """A download the connection drops halfway."""

    def read(self, size: int = -1) -> bytes:
        if self.tell() >= len(self.getvalue()) // 2:
            raise requests.ConnectionError("connection reset")
        return super().read(min(size, len(self.getvalue()) // 2 - self.tell()) if size > 0 else -1)


class Releases:
    """This repository on GitHub: the version `stable` holds, and each release's zip and
    signature, served through a redirect to where GitHub keeps release files, as GitHub does.
    `requests` records each request: its URL, headers and what else it carried."""

    def __init__(self, mp: pytest.MonkeyPatch) -> None:
        self.stable: str | None = INSTALLED
        self.files: dict[str, bytes] = {}  # by name in the release: parent-recap-X.zip, .sig
        self.cut_off: set[str] = set()
        self.redirect_to = "release-assets.githubusercontent.com"
        self.requests: list[dict[str, Any]] = []
        mp.setattr(requests.Session, "request",
                   lambda session, method, url, **k: self.request(session, method, url, **k))

    def publish(self, version: str, zip_data: bytes | None = None, sig: bytes | None = None) -> None:
        data = zip_data if zip_data is not None else release_zip(version)
        self.files[f"v{version}/parent-recap-{version}.zip"] = data
        self.files[f"v{version}/parent-recap-{version}.zip.sig"] = sig if sig is not None else signature(data)

    def hosts(self) -> set[str]:
        return {r["host"] for r in self.requests}

    def request(self, session: requests.Session, method: str, url: str, **k: Any) -> requests.Response:
        prepared = session.prepare_request(requests.Request(method, url, params=k.get("params"),
                                                            data=k.get("data"), json=k.get("json"),
                                                            headers=k.get("headers")))
        host = re.match(r"https://([^/]+)", url)[1]
        self.requests.append({"method": method, "url": prepared.url, "host": host,
                              "headers": dict(prepared.headers), "body": prepared.body,
                              "allow_redirects": k.get("allow_redirects")})
        path = url.split(host, 1)[1]
        if host == "raw.githubusercontent.com":
            if path == "/kinlace/parent-recap/stable/.claude-plugin/plugin.json" and self.stable:
                return response(200, json.dumps({"name": "parent-recap", "version": self.stable}).encode())
            return response(404, b"")
        prefix = "/kinlace/parent-recap/releases/download/"
        if host == "github.com" and path.startswith(prefix):
            return response(302, b"", location=f"https://{self.redirect_to}/files{path[len(prefix) - 1:]}")
        if host == "release-assets.githubusercontent.com" and path.startswith("/files/"):
            name = path.removeprefix("/files/")
            if name not in self.files:
                return response(404, b"")
            data = self.files[name]
            return response(200, data, cut=name.split("/")[-1] in self.cut_off)
        return response(404, b"")


def response(status: int, data: bytes, *, location: str | None = None, cut: bool = False) -> requests.Response:
    r = requests.Response()
    r.status_code = status
    r.raw = CutOff(data) if cut else io.BytesIO(data)
    r.headers["Content-Length"] = str(len(data))
    if location:
        r.headers["Location"] = location
    return r


# ---------------------------------------------------------------- the installed program

class Mac:
    """Parent Recap installed in ~/ParentRecap from a release, running from `app`, with the
    release's programs as process fakes: `ssh-keygen`, its `install.sh` and each version's
    `doctor`, whose failing checks are `fails[version]`."""

    def __init__(self, harness, monkeypatch) -> None:
        self.harness = harness
        self.folder = harness.home / "ParentRecap"
        (self.folder / "runtime" / "python-1").mkdir(parents=True)
        self.fails: dict[str, list[str]] = {}
        self.installer_fails = False
        self.installer_interrupted = False
        self.installer_cannot_start = False
        # How the release's install.sh ends once it has switched, where it doesn't finish: its
        # exit status, or "time limit" when it is ended there.
        self.installer_ends_after_switch: int | str | None = None
        self.doctor_cannot_run: set[str] = set()  # versions whose doctor doesn't answer
        # Versions whose doctor ends with a traceback: what it printed before, then a non-zero exit.
        self.doctor_crashes: dict[str, str] = {}
        self.installs: list[list[str]] = []
        self.install_envs: list[dict[str, str]] = []
        # What the release's install.sh runs once it has switched, as a test gives it: its output.
        self.after_switch: Callable[[dict[str, str]], str] | None = None
        running = self.built(INSTALLED, "aaa")
        versions.switch(self.folder, running)
        install_record.add("program", str(self.folder / "app"))
        install_record.add("versions", str(self.folder / "versions"))
        install_record.add("logs", str(self.folder / "logs"))
        install_record.add("runtime", str(self.folder / "runtime"))
        (self.folder / "logs").mkdir(exist_ok=True)
        # The program runs from its venv, as the jobs and `parent-recap` start it.
        monkeypatch.setattr(sys, "prefix", str(running / ".venv"))
        harness.processes.answers.append(self.answer)

    def built(self, version: str, suffix: str) -> Path:
        v = self.folder / "versions" / f"{version}.{suffix}"
        (v / ".venv" / "bin").mkdir(parents=True)
        (v / "VERSION").write_text(f"{version}\n")
        (v / "RUNTIMES").write_text("python-1\n")
        (v / "pyproject.toml").write_text('[project]\nname = "family-brief"\n')
        return v

    def version(self) -> str:
        return versions.version(versions.current(self.folder))

    def answer(self, cmd: list[str], prog: str, **k: Any) -> subprocess.CompletedProcess | None:
        if prog == "ssh-keygen":
            return self.ssh_keygen(cmd, k["stdin"])
        if cmd[0] == "/bin/bash" and cmd[1].endswith("/install.sh"):
            return self.install(cmd, k["env"])
        if cmd[0] == str(self.folder / "app" / ".venv" / "bin" / "parent-recap") and cmd[-1] == "doctor":
            version = self.version()
            if version in self.doctor_cannot_run:
                raise subprocess.TimeoutExpired(cmd, update.DOCTOR_TIMEOUT)
            if version in self.doctor_crashes:
                return subprocess.CompletedProcess(
                    cmd, 1, self.doctor_crashes[version],
                    "Traceback (most recent call last):\nKeyError: 'parent@example.com'\n")
            out = "✅ Config file: fine\n" + "".join(f"❌ {item}: broken in {version}\n"
                                                   for item in self.fails.get(version, []))
            return subprocess.CompletedProcess(cmd, 1 if self.fails.get(version) else 0, out, "")
        if cmd[0] == str(self.folder / "app" / ".venv" / "bin" / "python"):
            return subprocess.CompletedProcess(cmd, 0, "", "")  # switch-back's steps
        return None

    def ssh_keygen(self, cmd: list[str], stdin) -> subprocess.CompletedProcess:
        args = dict(zip(cmd[1::2], cmd[2::2]))
        assert cmd[0] == release_signature.SSH_KEYGEN and args["-Y"] == "verify"
        assert args["-f"] == str(release_signature.SIGNERS)
        sig = Path(args["-s"]).read_bytes()
        good = args["-n"] == release_signature.NAMESPACE and sig == signature(stdin.read(), args["-I"])
        return subprocess.CompletedProcess(cmd, 0 if good else 255, b"", b"")

    def install(self, cmd: list[str], env: dict[str, str]) -> subprocess.CompletedProcess:
        """The release's install.sh: the version in a folder of its own, then the switch."""
        self.installs.append(cmd)
        self.install_envs.append(env)
        plugin = Path(cmd[1]).parent
        version = json.loads((plugin / ".claude-plugin" / "plugin.json").read_text())["version"]
        assert env["PARENT_RECAP_HOME"] == str(self.folder)
        if self.installer_fails:
            return subprocess.CompletedProcess(cmd, 1, "", "❌ pip failed\n")
        if self.installer_interrupted:
            raise KeyboardInterrupt  # the family pressed Ctrl-C in the Terminal
        if self.installer_cannot_start:
            raise OSError("bash is missing")
        versions.switch(self.folder, self.built(version, f"x{len(self.installs)}"))
        out = self.after_switch(env) if self.after_switch else ""
        if self.installer_ends_after_switch == "time limit":
            raise subprocess.TimeoutExpired(cmd, update.INSTALL_TIMEOUT, output=out)
        if self.installer_ends_after_switch is not None:  # a step after the switch, under set -e
            return subprocess.CompletedProcess(cmd, self.installer_ends_after_switch, out,
                                               "❌ a step after the switch failed\n")
        return subprocess.CompletedProcess(cmd, 0, f"{out}✅ Parent Recap {version} installed\n", "")


@pytest.fixture
def github(monkeypatch) -> Releases:
    return Releases(monkeypatch)


@pytest.fixture
def mac(harness, monkeypatch) -> Mac:
    return Mac(harness, monkeypatch)


def update_command(harness, capsys) -> tuple[int, str]:
    code = harness.cli("update")
    return code, capsys.readouterr().out


def update_log(mac: Mac) -> str:
    return (mac.folder / "logs" / update.LOG).read_text()


def no_downloads(mac: Mac) -> bool:
    folder = mac.folder / update.UPDATE
    return not folder.exists() or [p.name for p in folder.iterdir()] in ([], [update.STATE])


# ---------------------------------------------------------------- what it does

def test_a_higher_version_on_stable_is_installed_checked_and_then_reported(harness, github, mac, capsys):
    github.stable = "0.20.0"
    github.publish("0.20.0")

    code, out = update_command(harness, capsys)

    assert code == 0
    assert "updated from version 0.19.0 to 0.20.0" in out
    assert mac.version() == "0.20.0"
    assert versions.version(versions.previous(mac.folder)) == "0.19.0"
    assert no_downloads(mac)
    # The release's own installer ran, from the plugin copy, as the install line runs it.
    assert mac.installs == [["/bin/bash", str(mac.folder / "plugin" / "install.sh")]]
    assert json.loads((mac.folder / "plugin" / ".claude-plugin" / "plugin.json").read_text())["version"] == "0.20.0"
    # And the program then reports the new version: the next look finds it up to date.
    code, out = update_command(harness, capsys)
    assert code == 0 and "up to date: it runs version 0.20.0" in out


@pytest.mark.parametrize("on_stable", [INSTALLED, "0.18.2"])
def test_the_same_or_a_lower_version_on_stable_changes_nothing(harness, github, mac, capsys, on_stable):
    github.stable = on_stable

    code, out = update_command(harness, capsys)

    assert code == 0 and "Parent Recap is up to date: it runs version 0.19.0" in out
    assert mac.version() == INSTALLED and mac.installs == []
    assert github.hosts() == {"raw.githubusercontent.com"}  # nothing downloaded
    assert no_downloads(mac)


def test_a_household_several_releases_behind_goes_straight_to_the_newest(harness, github, mac, capsys):
    for v in ("0.20.0", "0.21.0", "0.22.1"):
        github.publish(v)
    github.stable = "0.22.1"

    assert update_command(harness, capsys)[0] == 0

    assert mac.version() == "0.22.1" and len(mac.installs) == 1
    assert not any("v0.20.0" in r["url"] or "v0.21.0" in r["url"] for r in github.requests)


def test_a_zip_whose_signature_does_not_verify_is_deleted_and_its_installer_never_started(harness, github, mac, capsys):
    github.stable = "0.20.0"
    data = release_zip("0.20.0")
    github.publish("0.20.0", data, signature(data, key="someone-else"))

    code, out = update_command(harness, capsys)

    assert code == 1
    assert "signature doesn't verify" in out and "Nothing was changed" in out
    assert mac.installs == [] and mac.version() == INSTALLED
    assert no_downloads(mac) and not (mac.folder / "plugin").exists()


def test_a_changed_zip_does_not_verify(harness, github, mac, capsys):
    github.stable = "0.20.0"
    data = release_zip("0.20.0")
    github.publish("0.20.0", release_zip("0.20.0", extra={"parent-recap/evil.sh": "rm -rf ~\n"}),
                   signature(data))

    assert update_command(harness, capsys)[0] == 1

    assert mac.installs == [] and mac.version() == INSTALLED and no_downloads(mac)


@pytest.mark.parametrize("inside", ["0.19.0", "0.18.0", "0.20.1"])
def test_a_signed_zip_of_another_version_in_the_release_s_place_is_refused(harness, github, mac, capsys, inside):
    github.stable = "0.20.0"
    github.publish("0.20.0", release_zip(inside))  # signed by our key, but not 0.20.0

    code, out = update_command(harness, capsys)

    assert code == 1 and f"holds version {inside}, not 0.20.0" in out
    assert mac.installs == [] and mac.version() == INSTALLED and no_downloads(mac)


@pytest.mark.parametrize("cut", ["parent-recap-0.20.0.zip", "parent-recap-0.20.0.zip.sig"])
def test_a_download_cut_off_changes_nothing_and_leaves_no_partial_files(harness, github, mac, capsys, cut):
    github.stable = "0.20.0"
    github.publish("0.20.0")
    github.cut_off.add(cut)

    code, out = update_command(harness, capsys)

    assert code == 1 and "was cut off" in out and "Nothing was changed" in out
    assert mac.installs == [] and mac.version() == INSTALLED and no_downloads(mac)


def test_a_release_that_is_missing_or_a_github_that_is_down_changes_nothing(harness, github, mac, capsys):
    github.stable = "0.20.0"  # but no release published

    code, out = update_command(harness, capsys)
    assert code == 1 and "GitHub answered 404" in out

    github.stable = None
    code, out = update_command(harness, capsys)
    assert code == 1 and "couldn't look for a newer release" in out
    assert mac.installs == [] and mac.version() == INSTALLED and no_downloads(mac)


def test_a_failure_the_new_version_s_doctor_has_and_the_old_did_not_switches_back(harness, github, mac, capsys):
    github.stable = "0.20.0"
    github.publish("0.20.0")
    mac.fails["0.20.0"] = ["Wilma (Mia)"]

    code, out = update_command(harness, capsys)

    assert code == 1
    assert "check failed where version 0.19.0 passed (Wilma (Mia))" in out
    assert "switched back to version 0.19.0" in out
    assert mac.version() == INSTALLED
    assert versions.version(versions.previous(mac.folder)) == "0.20.0"
    # The plugin copy is the version's that runs again.
    assert not (mac.folder / "plugin").exists()
    assert no_downloads(mac)
    assert update.state(mac.folder)["failed"] == "0.20.0"


def test_a_failure_both_versions_have_does_not_switch_back(harness, github, mac, capsys):
    github.stable = "0.20.0"
    github.publish("0.20.0")
    mac.fails = {INSTALLED: ["Gmail"], "0.20.0": ["Gmail"]}

    code, out = update_command(harness, capsys)

    assert code == 0 and "updated from version 0.19.0 to 0.20.0" in out
    assert mac.version() == "0.20.0"


def test_an_installer_that_fails_leaves_the_running_version_and_the_plugin_copy(harness, github, mac, capsys):
    a_plugin_copy(mac)
    github.stable = "0.20.0"
    github.publish("0.20.0")
    mac.installer_fails = True

    code, out = update_command(harness, capsys)

    assert code == 1 and "couldn't be installed" in out
    assert mac.version() == INSTALLED
    assert json.loads((mac.folder / "plugin" / ".claude-plugin" / "plugin.json").read_text())["version"] == INSTALLED
    assert no_downloads(mac)
    assert "pip failed" in update_log(mac)


@pytest.mark.parametrize("ends, says", [(1, "ended with exit status 1"), (143, "ended with exit status 143"),
                                        ("time limit", "was ended at its time limit of 60 minutes")])
def test_an_installer_that_does_not_finish_after_the_switch_is_a_failed_update_and_switches_back(
        harness, github, mac, capsys, ends, says):
    plugin_json = a_plugin_copy(mac)
    github.stable = "0.20.0"
    github.publish("0.20.0")
    mac.installer_ends_after_switch = ends

    code, out = update_command(harness, capsys)

    assert code == 1
    assert f"its installer didn't finish (install.sh {says})" in out
    assert "switched back to version 0.19.0" in out
    assert mac.version() == INSTALLED
    assert versions.version(versions.previous(mac.folder)) == "0.20.0"
    # What a switch back restores: the version's own pieces, by its own steps, and its plugin copy.
    python = str(mac.folder / "app" / ".venv" / "bin" / "python")
    assert [python, "-m", "family_brief.setup_wilma", "update"] in harness.commands
    assert json.loads(plugin_json.read_text())["version"] == INSTALLED and no_downloads(mac)
    state = update.state(mac.folder)
    assert state["failed"] == "0.20.0"
    assert state["last_result"] == "0.20.0's installer didn't finish; back on 0.19.0"
    # The log says how the installer ended and why the update was taken as failed.
    log = update_log(mac)
    assert f"install: 0.20.0: install.sh {says}; app points at 0.20.0" in log
    assert f"switch: 0.20.0 -> 0.19.0: back, as 0.20.0's install.sh {says} after the switch" in log
    # The new version wasn't checked: only the version before's doctor ran.
    assert log.count("'s doctor:") == 1


def test_an_installer_that_does_not_finish_with_no_version_to_switch_back_to_says_so(
        harness, github, mac, capsys):
    github.stable = "0.20.0"
    github.publish("0.20.0")
    mac.installer_ends_after_switch = 1
    mac.after_switch = lambda env: (mac.folder / "versions" / "previous").unlink() or ""

    code, out = update_command(harness, capsys)

    assert code == 1 and mac.version() == "0.20.0"
    assert "its installer didn't finish (install.sh ended with exit status 1)" in out
    assert "no previous version to switch back to" in out
    assert update.state(mac.folder)["failed"] == "0.20.0"
    assert update_log(mac).count("no previous version to switch back to") == 1


def test_a_wilma_cli_or_a_baileys_tree_that_could_not_be_installed_leaves_the_update_a_success(
        harness, github, mac, capsys):
    """install.sh's steps that never fail the install say what they couldn't do and end with 0
    (test_setup_wilma.py, test_baileys_install.py), so the installer does too."""
    github.stable = "0.20.0"
    github.publish("0.20.0")
    mac.after_switch = lambda env: (
        "⚠️  The wilma CLI couldn't be updated to 2.5.0, so Wilma is read with the one already "
        "there. Run the install again later.\n"
        "⚠️  The Linked Device's WhatsApp library couldn't be installed. WhatsApp can't be read "
        "as a Linked Device until it is. Run the install again later.\n")

    code, out = update_command(harness, capsys)

    assert code == 0 and "updated from version 0.19.0 to 0.20.0" in out
    assert mac.version() == "0.20.0" and update.state(mac.folder)["failed"] is None
    log = update_log(mac)
    assert "The wilma CLI couldn't be updated" in log  # the installer's own output is kept
    assert "install: 0.20.0: install.sh ended with exit status 0; app points at 0.20.0" in log


def test_the_update_state_is_kept_across_the_switch_and_the_switch_back(harness, github, mac, capsys):
    github.stable = "0.20.0"
    github.publish("0.20.0")
    mac.fails["0.20.0"] = ["Calendar"]
    update_command(harness, capsys)
    after_back = update.state(mac.folder)
    assert after_back["failed"] == "0.20.0" and "back on 0.19.0" in after_back["last_result"]

    github.publish("0.20.1")
    github.stable = "0.20.1"
    update_command(harness, capsys)

    state = update.state(mac.folder)
    assert mac.version() == "0.20.1"
    assert state["failed"] is None and state["last_result"] == "updated 0.19.0 to 0.20.1"
    assert state["last_look"] is not None
    # Outside every version's folder, so neither switch took it with it.
    assert (mac.folder / update.UPDATE / update.STATE).is_file()


def a_plugin_copy(mac: Mac, version: str = INSTALLED) -> Path:
    """The plugin copy the install line left in ~/ParentRecap/plugin."""
    (mac.folder / "plugin" / ".claude-plugin").mkdir(parents=True)
    plugin_json = mac.folder / "plugin" / ".claude-plugin" / "plugin.json"
    plugin_json.write_text(json.dumps({"name": "parent-recap", "version": version}))
    return plugin_json


def test_an_installer_that_cannot_start_leaves_the_running_version_and_the_plugin_copy(harness, github, mac, capsys):
    plugin_json = a_plugin_copy(mac)
    github.stable = "0.20.0"
    github.publish("0.20.0")
    mac.installer_cannot_start = True

    code, out = update_command(harness, capsys)

    assert code == 1 and "couldn't be installed" in out
    assert mac.version() == INSTALLED
    assert json.loads(plugin_json.read_text())["version"] == INSTALLED and no_downloads(mac)


def test_an_update_interrupted_mid_install_puts_the_plugin_copy_back_and_leaves_no_download(harness, github, mac, capsys):
    plugin_json = a_plugin_copy(mac)
    github.stable = "0.20.0"
    github.publish("0.20.0")
    mac.installer_interrupted = True

    with pytest.raises(KeyboardInterrupt):
        update_command(harness, capsys)

    assert mac.version() == INSTALLED
    assert json.loads(plugin_json.read_text())["version"] == INSTALLED and no_downloads(mac)


def test_a_new_version_whose_doctor_does_not_run_is_switched_back_even_if_the_old_one_s_did_not(harness, github, mac, capsys):
    github.stable = "0.20.0"
    github.publish("0.20.0")
    mac.doctor_cannot_run = {INSTALLED, "0.20.0"}

    code, out = update_command(harness, capsys)

    assert code == 1 and "switched back to version 0.19.0" in out
    assert update.DOCTOR_DIDNT_RUN in out
    assert mac.version() == INSTALLED


@pytest.mark.parametrize("printed", ["", "✅ Config file: fine\n"], ids=["nothing", "passing checks"])
@pytest.mark.parametrize("before_too", [False, True])
def test_a_new_version_whose_doctor_crashes_did_not_run_and_is_switched_back(
        harness, github, mac, capsys, printed, before_too):
    github.stable = "0.20.0"
    github.publish("0.20.0")
    mac.doctor_crashes = {"0.20.0": printed, **({INSTALLED: printed} if before_too else {})}

    code, out = update_command(harness, capsys)

    assert code == 1 and "switched back to version 0.19.0" in out
    assert f"check failed where version 0.19.0 passed ({update.DOCTOR_DIDNT_RUN})" in out
    assert mac.version() == INSTALLED and update.state(mac.folder)["failed"] == "0.20.0"
    log = update_log(mac)
    assert ("0.20.0's doctor: 1 failing: doctor didn't run (it ended with exit status 1 and "
            "printed no failing check)") in log
    assert "back, as 0.20.0 fails doctor didn't run" in log
    # What the traceback said isn't kept: it may name what is the Household's.
    assert "Traceback" not in log and "parent@example.com" not in log


def test_a_doctor_that_exits_non_zero_over_a_failing_check_is_read_from_its_lines(harness, github, mac, capsys):
    github.stable = "0.20.0"
    github.publish("0.20.0")
    mac.fails = {INSTALLED: ["Gmail"], "0.20.0": ["Gmail"]}  # each doctor exits with 1

    code, out = update_command(harness, capsys)

    assert code == 0 and mac.version() == "0.20.0"
    log = update_log(mac)
    assert "0.20.0's doctor: 1 failing: Gmail\n" in log and update.DOCTOR_DIDNT_RUN not in log


def test_a_doctor_that_crashes_after_a_failing_check_is_read_from_its_lines(harness, github, mac, capsys):
    github.stable = "0.20.0"
    github.publish("0.20.0")
    mac.doctor_crashes = {"0.20.0": "❌ Calendar: broken in 0.20.0\n"}

    code, out = update_command(harness, capsys)

    assert code == 1 and "check failed where version 0.19.0 passed (Calendar)" in out
    assert mac.version() == INSTALLED


def test_the_update_log_has_each_step_with_versions_and_times_and_nothing_of_the_family_s(harness, github, mac, capsys):
    github.stable = "0.20.0"
    github.publish("0.20.0")
    mac.fails["0.20.0"] = ["Wilma (Mia)"]

    update_command(harness, capsys)

    log = update_log(mac)
    steps = re.findall(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d (\w+): (.*)$", log, re.M)
    assert [s for s, _ in steps] == ["look", "download", "verify", "verify", "check", "install",
                                     "install", "switch", "check", "switch"]
    assert "installed 0.19.0, stable holds 0.20.0" in log
    assert "0.19.0 -> 0.20.0" in log and "0.20.0 -> 0.19.0" in log
    # The failing check is named, as doctor names it; the Household's addresses and groups aren't.
    assert "Wilma (Mia)" in log
    for private in ("parent@example.com", "partner@example.com", "3B parents", "Leo"):
        assert private not in log


# ---------------------------------------------------------------- what it never does

# The hosts update.py names, as ADR 0047 states them: adding one fails here until this changes too.
GITHUB_HOSTS = {"raw.githubusercontent.com", "github.com", "objects.githubusercontent.com",
                "release-assets.githubusercontent.com"}


def test_the_module_names_only_its_hosts_and_its_repository_is_a_constant():
    source = Path(update.__file__).read_text()
    named = set(re.findall(r"https?://([A-Za-z0-9.-]+)", source))
    named |= {h for h in re.findall(r"[\"']([a-z0-9.-]+\.(?:com|net|org|io|fi|dev))[\"']", source)}
    assert set(update.HOSTS) == GITHUB_HOSTS
    assert named <= GITHUB_HOSTS
    assert update.REPOSITORY == "kinlace/parent-recap"
    assert "gateway" not in source.split('"""', 2)[2]  # beyond the statement: no gateway call


def test_its_requests_go_only_to_its_hosts_and_carry_nothing_about_the_household(harness, github, mac, capsys):
    github.stable = "0.20.0"
    github.publish("0.20.0")

    update_command(harness, capsys)

    assert github.hosts() <= set(update.HOSTS)
    assert all(r["method"] == "GET" and r["body"] is None and r["allow_redirects"] is False
               for r in github.requests)
    assert all("kinlace/parent-recap" in r["url"] or r["host"] == github.redirect_to
               for r in github.requests)
    for r in github.requests:
        assert set(r["headers"]) <= {"User-Agent", "Accept-Encoding", "Accept", "Connection"}
        assert r["headers"]["User-Agent"] == "parent-recap-update"
        text = r["url"] + json.dumps(r["headers"])
        assert not any(p in text for p in (*FAMILY, str(harness.home), INSTALLED))


def test_a_redirect_to_a_host_not_on_its_list_is_not_followed(harness, github, mac, capsys):
    github.stable = "0.20.0"
    github.publish("0.20.0")
    github.redirect_to = "downloads.example.com"

    code, out = update_command(harness, capsys)

    assert code == 1 and "downloads.example.com, which isn't one of the addresses" in out
    assert "downloads.example.com" not in github.hosts()
    assert mac.installs == [] and no_downloads(mac)


def test_it_starts_no_sudo_admin_dialog_curl_or_shell_with_downloaded_text(harness, github, mac, capsys):
    github.stable = "0.20.0"
    github.publish("0.20.0")
    mac.fails["0.20.0"] = ["Calendar"]  # the switch back's steps too

    update_command(harness, capsys)

    for cmd in harness.commands:
        name = Path(cmd[0]).name
        assert name not in ("sudo", "osascript", "curl", "unzip", "sh", "zsh")
        assert not any("administrator" in part for part in cmd)
        if name == "bash":  # only the verified release's own installer, as a file
            assert cmd == ["/bin/bash", str(mac.folder / "plugin" / "install.sh")]
    # And the installer is told it runs for an update, so the wake's password dialog doesn't start.
    assert all(env["PARENT_RECAP_UPDATING"] == "1" for env in mac.install_envs)
    assert harness.wake.asked == []


# Docs/setup-internals.md, "What an install writes outside Parent Recap's folder", on a Mac.
INSTALL_WRITES = (".family", ".family/*", ".zprofile", "Applications/Parent Recap *.app*",
                  "Library/LaunchAgents/com.parentrecap.*", ".config/wilmai/*",
                  ".agents/skills/parent-recap-*", "Library/Caches/pip*")


def snapshot(home: Path) -> dict[str, tuple]:
    seen = {}
    for root, dirs, files in os.walk(home):
        for name in [*dirs, *files]:
            p = Path(root) / name
            st = p.lstat()
            seen[str(p.relative_to(home))] = (st.st_mtime_ns, st.st_size,
                                              os.readlink(p) if p.is_symlink() else None)
    return seen


@pytest.mark.parametrize("doctor_fails", [False, True])
def test_every_path_it_changes_is_in_parent_recap_s_folder_or_where_an_install_writes(
        harness, github, mac, capsys, doctor_fails):
    harness.set_up_sources()
    github.stable = "0.20.0"
    github.publish("0.20.0")
    if doctor_fails:
        mac.fails["0.20.0"] = ["Calendar"]
    before = snapshot(harness.home)

    update_command(harness, capsys)

    after = snapshot(harness.home)
    changed = {p for p in before.keys() | after.keys() if before.get(p) != after.get(p)}
    assert changed  # it did install
    outside = [p for p in changed if not p.startswith("ParentRecap")
               and not any(fnmatch(p, w) for w in INSTALL_WRITES)]
    assert outside == []


@pytest.mark.parametrize("on, says", [(WINDOWS, "doesn't update itself on Windows"),
                                      (LINUX, "This is the hosted version")])
def test_on_windows_and_on_the_hosted_server_it_does_not_apply(harness, github, mac, capsys, on, says):
    harness.system = on
    github.stable = "0.20.0"

    code, out = update_command(harness, capsys)

    assert code == 0 and says in out
    assert github.requests == [] and mac.installs == [] and harness.commands == []


def test_a_hosted_config_does_not_apply(harness, github, mac, capsys):
    harness.config["hosted"] = True
    github.stable = "0.20.0"

    code, out = update_command(harness, capsys)

    assert code == 0 and "This is the hosted version" in out
    assert github.requests == [] and mac.installs == []


def test_a_program_not_run_from_an_installed_release_does_not_update(harness, github, capsys, monkeypatch):
    github.stable = "0.20.0"

    code, out = update_command(harness, capsys)

    assert code == 0 and "doesn't run from an installed release" in out
    assert github.requests == [] and harness.commands == []
    assert not (harness.home / "ParentRecap").exists()


def test_uninstall_removes_the_downloads_the_state_and_the_update_log(harness, github, mac, capsys):
    github.stable = "0.20.0"
    github.publish("0.20.0")
    update_command(harness, capsys)
    # What an update cut off halfway leaves.
    (mac.folder / update.UPDATE / f"{update.DOWNLOAD}abc").mkdir()
    (mac.folder / update.UPDATE / f"{update.DOWNLOAD}abc" / "parent-recap-0.21.0.zip").write_bytes(b"PK")
    assert (mac.folder / "logs" / update.LOG).exists()

    assert harness.cli("uninstall", "--confirm", "--remove-archive") == 0

    assert not (mac.folder / update.UPDATE).exists()
    assert not (mac.folder / "logs").exists()


# ---------------------------------------------------------------- a release that needs the family (#918)

ASKS = {"en": "macOS asks for your Mac password once.", "fi": "macOS kysyy Macin salasanaa kerran.",
        "zh": "macOS 会问一次 Mac 密码。"}


def needing_family(version: str, below: str | None = None, asks: Any = ASKS) -> bytes:
    """A release zip that says it needs the family on every version below `below` (itself,
    unless given), as scripts/release.py builds it from release.json."""
    note = json.dumps({"background_from": below or version, "asks": asks})
    return release_zip(version, extra={"parent-recap/release.json": note})


def test_started_by_the_command_a_release_that_needs_the_family_installs_with_its_dialogs(
        harness, github, mac, capsys):
    github.stable = "0.20.0"
    github.publish("0.20.0", needing_family("0.20.0"))

    code, out = update_command(harness, capsys)

    assert code == 0 and mac.version() == "0.20.0"
    # It first says what the family will be asked, then how it went.
    asks, done = out.strip().split("\n")
    assert asks == f"Version 0.20.0 needs you at the computer. {ASKS['en']}"
    assert "updated from version 0.19.0 to 0.20.0" in done
    # The installer isn't told to leave out what asks for a password.
    assert ["PARENT_RECAP_UPDATING" in env for env in mac.install_envs] == [False]
    assert "with its dialogs" in update_log(mac)
    assert update.status(str(config_path(harness))).waiting is None


def test_a_household_at_or_above_the_version_a_release_names_takes_it_as_any_other(
        harness, github, mac, capsys):
    github.stable = "0.21.0"
    github.publish("0.21.0", needing_family("0.21.0", below=INSTALLED))  # carried forward

    code, out = update_command(harness, capsys)

    assert code == 0 and mac.version() == "0.21.0"
    assert "needs you at the computer" not in out
    assert [env["PARENT_RECAP_UPDATING"] for env in mac.install_envs] == ["1"]


def test_a_zip_whose_needs_family_value_was_changed_does_not_verify(harness, github, mac, capsys):
    github.stable = "0.20.0"
    signed = needing_family("0.20.0")
    # Someone took the value out of the zip, so that it would install in the background.
    github.publish("0.20.0", release_zip("0.20.0", extra={"parent-recap/release.json": json.dumps(
        {"background_from": None, "asks": None})}), signature(signed))

    code, out = update_command(harness, capsys)

    assert code == 1 and "signature doesn't verify" in out
    assert mac.installs == [] and mac.version() == INSTALLED and no_downloads(mac)


@pytest.mark.parametrize("note", ["not json", "[]", json.dumps({"background_from": "soon"}),
                                  json.dumps({"background_from": 20})])
def test_a_release_whose_needs_family_note_cannot_be_read_is_not_installed(harness, github, mac, capsys, note):
    github.stable = "0.20.0"
    github.publish("0.20.0", release_zip("0.20.0", extra={"parent-recap/release.json": note}))

    code, out = update_command(harness, capsys)

    assert code == 1 and "release.json" in out and "can't be read" in out
    assert mac.installs == [] and mac.version() == INSTALLED and no_downloads(mac)


def test_a_release_that_names_no_version_installs_as_any_other(harness, github, mac, capsys):
    github.stable = "0.20.0"
    github.publish("0.20.0", release_zip("0.20.0", extra={"parent-recap/release.json": json.dumps(
        {"background_from": None, "asks": None})}))

    code, out = update_command(harness, capsys)

    assert code == 0 and mac.version() == "0.20.0" and "needs you" not in out
    assert [env["PARENT_RECAP_UPDATING"] for env in mac.install_envs] == ["1"]


@pytest.mark.parametrize("below", [None, "0.20.0"])
def test_a_release_json_that_also_says_the_version_and_parts_is_read_as_before(
        harness, github, mac, capsys, below):
    """The keys of #958 (ADR 0051) change nothing for the version before, which reads only what
    it reads of needing the family."""
    github.stable = "0.20.0"
    github.publish("0.20.0", release_zip("0.20.0", extra={"parent-recap/release.json": json.dumps(
        {"version": "0.20.0", "background_from": below, "asks": ASKS if below else None,
         "parts": {"core": "0.19.0", "nl/konnect": "0.20.0"}})}))

    code, out = update_command(harness, capsys)

    assert code == 0 and mac.version() == "0.20.0"
    assert ("needs you at the computer" in out) == bool(below)
    assert [("PARENT_RECAP_UPDATING" in env) for env in mac.install_envs] == [not below]


# ---------------------------------------------------------------- the setting (#917)

def config_path(harness) -> Path:
    return harness.home / ".family" / "config.yaml"


def saved_config(harness) -> dict[str, Any]:
    return yaml.safe_load(config_path(harness).read_text())


def test_a_new_install_and_one_from_before_the_setting_have_updates_on(harness, github, mac, capsys):
    # The config says nothing of updates: a new one, and one written before the setting.
    assert "updates" not in harness.config
    update_command(harness, capsys)  # writes the harness's config, as any command does here

    status = update.status(str(config_path(harness)))

    assert status.enabled and status.unavailable is None and status.version == INSTALLED
    assert "updates" not in saved_config(harness)


def test_update_off_and_on_save_the_setting_and_say_which_it_now_is(harness, github, mac, capsys):
    assert harness.cli("update", "off") == 0

    out = capsys.readouterr().out
    assert out.startswith("Updates are off") and "downloads nothing" in out
    assert "parent-recap update on" in out
    assert saved_config(harness)["updates"] == {"enabled": False}
    assert update.status(str(config_path(harness))).enabled is False
    assert github.requests == []  # the setting asks no host

    harness.config = saved_config(harness)
    assert harness.cli("update", "on") == 0

    out = capsys.readouterr().out
    assert out.startswith("Updates are on") and "parent-recap update off" in out
    assert saved_config(harness)["updates"] == {"enabled": True}
    # Everything else the config says is as it was.
    assert {k: v for k, v in saved_config(harness).items() if k != "updates"} \
        == {k: v for k, v in harness.config.items() if k != "updates"}


def test_the_setting_survives_an_update_and_a_switch_back(harness, github, mac, capsys):
    harness.config["updates"] = {"enabled": False}
    github.stable = "0.20.0"
    github.publish("0.20.0")

    code, _ = update_command(harness, capsys)  # by hand: off only stops the evening's own

    assert code == 0 and mac.version() == "0.20.0"
    assert saved_config(harness)["updates"] == {"enabled": False}
    assert update.status(str(config_path(harness))).enabled is False
    assert harness.cli("switch-back") == 0
    assert mac.version() == INSTALLED and saved_config(harness)["updates"] == {"enabled": False}


@pytest.mark.parametrize("setting", ["on", "off"])
@pytest.mark.parametrize("on, says", [(WINDOWS, "doesn't update itself on Windows"),
                                      (LINUX, "This is the hosted version")])
def test_on_windows_and_on_the_hosted_server_the_setting_explains_and_saves_nothing(
        harness, github, mac, capsys, on, says, setting):
    harness.system = on

    assert harness.cli("update", setting) == 0

    assert says in capsys.readouterr().out
    assert "updates" not in saved_config(harness) and github.requests == []


def test_for_a_hosted_household_the_setting_explains_and_saves_nothing(harness, github, mac, capsys):
    harness.config["hosted"] = True

    assert harness.cli("update", "off") == 0

    assert "This is the hosted version" in capsys.readouterr().out
    assert "updates" not in saved_config(harness)


def test_the_manage_skill_says_how_to_turn_updates_off_or_on_and_how_to_update_now():
    skill = (Path(__file__).parents[3] / "skills" / "manage" / "SKILL.md").read_text(encoding="utf-8")
    [row] = [line for line in skill.splitlines() if line.startswith("| Turn updates off or on")]

    for command in ("`$FB update off`", "`$FB update on`", "`$FB update` installs the newest release now"):
        assert command in row, command
    assert "downloads nothing" in row  # what off leaves on, said before turning it off


def test_each_readme_says_it_updates_itself_and_links_to_how_and_to_what_it_downloads():
    root = Path(__file__).parents[3]
    for name, says in (("README.md", "updates itself"), ("README.fi.md", "päivittää itsensä"),
                       ("README.zh-CN.md", "自动更新")):
        text = (root / name).read_text(encoding="utf-8")
        [sentence] = [line for line in text.splitlines() if says in line]
        assert "(docs/install.md#updating)" in sentence, name
        assert "(SECURITY.md#how-parent-recap-updates-itself)" in sentence, name
    assert "\n## Updating\n" in (root / "docs" / "install.md").read_text(encoding="utf-8")


def test_no_page_claims_more_than_the_update_does():
    # The installer fetches from other hosts than GitHub, an install writes outside Parent
    # Recap's folder, and /bin/bash runs the verified installer (#919).
    root = Path(__file__).parents[3]
    for path in [*root.glob("README*.md"), *(root / "docs").glob("*.md")]:
        text = " ".join(path.read_text(encoding="utf-8").lower().split())
        for claim in ("downloads only from github", "writes only inside parent recap's folder",
                      "no script is run by a shell"):
            assert claim not in text, f"{path.name}: {claim}"


def test_update_takes_nothing_but_on_or_off(harness, mac, capsys):
    with pytest.raises(SystemExit):
        harness.cli("update", "never")
    assert "invalid choice" in capsys.readouterr().err


# ---------------------------------------------------------------- one update at a time (#977)

BUSY_SAYS = ("Parent Recap is busy", "Try again in a few minutes",
             "Parent Recapilla on juuri nyt muuta tekeillä", "muutaman minuutin kuluttua",
             "Parent Recap 正忙", "过几分钟再试")


def while_installing(mac: Mac, then: Callable[[], Any]) -> None:
    """Has `then` run once while the update's installer runs, after its switch."""
    def after_switch(_env: dict[str, str]) -> str:
        mac.after_switch = None
        then()
        return ""
    mac.after_switch = after_switch


def where_the_lock_is_held(harness, github: Releases, mac: Mac, cfg: Config) -> dict[str, bool]:
    """Whether `cfg`'s run lock is held at an update's `look` and at a step of its `switch
    back`, filled in as the update gets to each."""
    held: dict[str, bool] = {}
    look = github.request

    def at_the_look(*a: Any, **k: Any) -> requests.Response:
        held.setdefault("look", run_lock.is_busy(cfg))
        return look(*a, **k)
    github.request = at_the_look  # type: ignore[method-assign]

    def at_a_step_of_the_switch_back(cmd: list[str], _prog: str, **_k: Any) -> None:
        if cmd[0] == str(mac.folder / "app" / ".venv" / "bin" / "python"):
            held["switch back"] = run_lock.is_busy(cfg)
    harness.processes.answers.insert(0, at_a_step_of_the_switch_back)
    return held


def changed_since(before: dict[str, tuple], home: Path) -> set[str]:
    """The paths under `home` that differ from the snapshot `before`, but for the config, which
    the test's own command line writes again."""
    after = snapshot(home)
    return {p for p in before.keys() | after.keys()
            if before.get(p) != after.get(p)} - {".family/config.yaml"}


def test_a_second_update_while_one_is_going_says_it_is_busy_and_changes_nothing(
        harness, github, mac, capsys):
    github.stable = "0.20.0"
    github.publish("0.20.0")
    second: dict[str, Any] = {}

    def a_second_update() -> None:
        capsys.readouterr()
        before, asked = snapshot(harness.home), len(github.requests)
        second["code"] = harness.cli("update")
        second["out"] = capsys.readouterr().out
        second["changed"] = changed_since(before, harness.home)
        second["asked"] = len(github.requests) - asked
    while_installing(mac, a_second_update)

    code, out = update_command(harness, capsys)

    assert second["code"] == run_lock.BUSY
    for words in BUSY_SAYS:
        assert words in second["out"], words
    assert second["changed"] == set() and second["asked"] == 0
    # The first went on as if nothing had been tried beside it.
    assert code == 0 and "updated from version 0.19.0 to 0.20.0" in out
    assert mac.version() == "0.20.0" and len(mac.installs) == 1


def test_update_while_a_brief_is_being_made_says_it_is_busy_and_changes_nothing(
        harness, github, mac, capsys):
    github.stable = "0.20.0"
    github.publish("0.20.0")
    assert harness.cli("update", "on") == 0  # the config is there, as on a Household's Mac

    with run_lock.exclusive(Config.model_validate(harness.config)):  # the evening run
        before = snapshot(harness.home)
        code, out = update_command(harness, capsys)
        changed = changed_since(before, harness.home)

    assert code == run_lock.BUSY
    for words in BUSY_SAYS:
        assert words in out, words
    assert changed == set()
    assert github.requests == [] and mac.installs == [] and no_downloads(mac)
    assert update_command(harness, capsys)[0] == 0 and mac.version() == "0.20.0"  # once it ended


def test_an_evening_run_that_starts_during_a_family_s_update_stops_as_beside_another_run(
        harness, github, mac, capsys, caplog):
    github.stable = "0.20.0"
    github.publish("0.20.0")
    run: dict[str, Any] = {}
    while_installing(mac, lambda: run.update(code=harness.run()))

    code, _ = update_command(harness, capsys)

    assert run["code"] == run_lock.BUSY
    assert "Another Parent Recap run is still going" in caplog.text
    assert harness.sent == [] and harness.model_calls == []
    assert code == 0 and mac.version() == "0.20.0"


def test_the_lock_is_held_from_before_the_look_through_the_switch_back_and_then_let_go(
        harness, github, mac, capsys):
    github.stable = "0.20.0"
    github.publish("0.20.0")
    mac.fails["0.20.0"] = ["Calendar"]
    cfg = Config.model_validate(harness.config)
    held = where_the_lock_is_held(harness, github, mac, cfg)

    code, out = update_command(harness, capsys)

    assert code == 1 and "switched back to version 0.19.0" in out and mac.version() == INSTALLED
    assert held == {"look": True, "switch back": True}
    assert not run_lock.is_busy(cfg)


@pytest.mark.parametrize("on", [WINDOWS, LINUX])
def test_where_it_does_not_apply_it_takes_no_lock(harness, github, mac, capsys, on):
    harness.system = on

    with run_lock.exclusive(Config.model_validate(harness.config)):
        code, out = update_command(harness, capsys)

    assert code == 0 and "Parent Recap is busy" not in out


def test_update_on_and_off_are_saved_while_a_brief_is_being_made(harness, github, mac, capsys):
    with run_lock.exclusive(Config.model_validate(harness.config)):
        assert harness.cli("update", "off") == 0

    assert saved_config(harness)["updates"] == {"enabled": False}
