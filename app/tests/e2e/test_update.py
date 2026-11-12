"""`parent-recap update` (ADR 0047): it installs the newest release `stable` holds, checks it with
`doctor` and switches back when that fails where the version before passed; and each line of the
statement at the top of family_brief/update.py.

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
from typing import Any

import pytest
import requests

from family_brief import install_record, release_signature, update, versions
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
        self.doctor_cannot_run: set[str] = set()  # versions whose doctor doesn't answer
        self.installs: list[list[str]] = []
        self.install_envs: list[dict[str, str]] = []
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
        return subprocess.CompletedProcess(cmd, 0, f"✅ Parent Recap {version} installed\n", "")


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
    # And the installer is told it runs for an update, so it asks for no password.
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
