"""`install.sh` installs Parent Recap's own pinned Python and Node, and only prebuilt packages at
the versions in the shipped constraints file.

Runs the real `install.sh` and the real pip, offline: pip reads packages only from a folder made
here (`fake_packages.py`), and the program it installs is a stand-in with the real one's layout.
The pinned Python comes from a fake `curl` serving a stand-in python-build-standalone tarball
(`python_build`), the pinned Node from a stand-in nodejs.org tarball (`serve_node`), the
Mac's kind from a fake `sysctl` and its macOS version from a fake `sw_vers`, and a Linux server
and its chip from a fake `uname` (`a_linux_server`). A `python3` on PATH
fails, so nothing runs on a Python the Mac already has.
"""
from __future__ import annotations

import ensurepip
import hashlib
import importlib.metadata
import itertools
import json
import os
import re
import shutil
import subprocess
import sys
import sysconfig
import tarfile
import tomllib
from pathlib import Path

import pytest

from fake_packages import project, sdist, wheel

ROOT = Path(__file__).resolve().parents[3]


PINNED = ("3.13.16", "20261003")
NODE = "24.21.0"
NODE_ARCHES = {"aarch64": "arm64", "x86_64": "x64"}
MACOS = "15.5"
# Each system as python-build-standalone names it, as nodejs.org does, and its pins' prefix.
SYSTEMS = [("apple-darwin", "darwin", ""), ("unknown-linux-gnu", "linux", "LINUX_")]
# Where install.sh downloads the pinned Python from, in turn: Astral's mirror, then GitHub (#843).
MIRROR = "https://releases.astral.sh/github/python-build-standalone/releases/download"
GITHUB = "https://github.com/astral-sh/python-build-standalone/releases/download"


@pytest.fixture(scope="session")
def python_build(tmp_path_factory) -> Path:
    """python-build-standalone's install_only layout under `python/`, standing in for the pinned
    Python: a copy of the base Python running the tests (a copy, so its real path is in the
    folder it's unpacked to), `python3` linking to it, and `lib` its standard library."""
    # A copied framework binary still finds its home through the framework, so a venv made with
    # it lands on that Python instead of the copy.
    if sysconfig.get_config_var("PYTHONFRAMEWORK"):
        reason = ("the stand-in pinned Python can't be copied from a framework build "
                  "(python.org's); run on a python-build-standalone Python, e.g. uv's")
        if os.environ.get("CI"):
            pytest.fail(reason)
        pytest.skip(reason)
    root = tmp_path_factory.mktemp("python-build")
    bin_ = root / "python" / "bin"
    bin_.mkdir(parents=True)
    exe = f"python{sys.version_info.major}.{sys.version_info.minor}"
    shutil.copy2(os.path.realpath(sys._base_executable), bin_ / exe)
    (bin_ / "python3").symlink_to(exe)
    (root / "python" / "lib").symlink_to(Path(sys.base_prefix) / "lib")
    return root


@pytest.fixture
def mac(tmp_path: Path, python_build: Path) -> dict:
    packages = tmp_path / "packages"
    packages.mkdir()
    plugin = tmp_path / "plugin"
    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "parent-recap", "version": "9.9.9"}))
    shutil.copy(ROOT / "install.sh", plugin / "install.sh")
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    m = {"tmp": tmp_path, "packages": packages, "plugin": plugin, "home": tmp_path / "home",
         "bin": bin_, "served": tmp_path / "served", "build": python_build,
         "downloads": tmp_path / "downloads.log", "down": tmp_path / "down"}
    m["served"].mkdir()
    # Serves the file named like the URL's last part, or fails as curl does offline, or as it
    # does on a 504 for a URL starting with a line of `down`.
    fake(bin_ / "curl", f"""
url=; out=
while [ $# -gt 0 ]; do case "$1" in -o) out=$2; shift ;; http*) url=$1 ;; esac; shift; done
echo "$url" >> "{m['downloads']}"
if [ -f "{m['down']}" ]; then
  while read -r down; do
    case "$url" in "$down"*) echo "curl: (22) The requested URL returned error: 504" >&2; exit 22 ;; esac
  done < "{m['down']}"
fi
served="{m['served']}/$(basename "$url")"
[ -f "$served" ] || {{ echo "curl: (6) Could not resolve host: github.com" >&2; exit 6; }}
cp "$served" "$out"
""")
    fake(bin_ / "python3", 'echo "the Mac\'s own python3 was run" >&2; exit 97')
    a_mac(m, "arm64")
    macos(m, MACOS)
    serve(m, *PINNED)
    serve_node(m, NODE)
    return m


def fake(path: Path, body: str) -> None:
    path.write_text("#!/bin/sh\n" + body.lstrip("\n"))
    path.chmod(0o755)


def a_mac(mac: dict, kind: str) -> None:
    """What `sysctl -n hw.optional.arm64` and `uname -m` say on an Apple Silicon Mac, an Intel
    Mac, or an Apple Silicon Mac in a Rosetta terminal."""
    sysctl = {"arm64": "echo 1", "rosetta": "echo 1",
              "intel": 'echo "sysctl: unknown oid \'hw.optional.arm64\'" >&2; exit 1'}[kind]
    fake(mac["bin"] / "sysctl", f'[ "$*" = "-n hw.optional.arm64" ] || exit 2\n{sysctl}')
    machine = "arm64" if kind == "arm64" else "x86_64"
    fake(mac["bin"] / "uname", f'[ "$1" = -m ] && echo {machine} || echo Darwin')


def a_linux_server(mac: dict, machine: str) -> None:
    """What `uname` and `uname -m` say on a Linux server with the chip `machine`. No sysctl."""
    fake(mac["bin"] / "uname", f'[ "$1" = -m ] && echo {machine} || echo Linux')
    fake(mac["bin"] / "sysctl", 'echo "sysctl was run on Linux" >&2; exit 2')


def macos(mac: dict, version: str) -> None:
    """What `sw_vers -productVersion` says."""
    fake(mac["bin"] / "sw_vers", f'[ "$*" = -productVersion ] || exit 2\necho {version}')


def build_name(version: str, build: str, arch: str, system: str = "apple-darwin") -> str:
    return f"cpython-{version}+{build}-{arch}-{system}-install_only.tar.gz"


def serve(mac: dict, version: str, build: str, tamper: bool = False) -> None:
    """Serves the build for both kinds of Mac and of Linux server, and pins it in the plugin's
    install.sh. Tampered, what's served isn't what the pinned checksum says."""
    script = mac["plugin"] / "install.sh"
    text = script.read_text()
    text = re.sub(r"(?m)^PYTHON_VERSION=.*$", f"PYTHON_VERSION={version}", text)
    text = re.sub(r"(?m)^PYTHON_BUILD=.*$", f"PYTHON_BUILD={build}", text)
    for (system, _, pin), arch in itertools.product(SYSTEMS, ("aarch64", "x86_64")):
        tarball = mac["served"] / build_name(version, build, arch, system)
        with tarfile.open(tarball, "w:gz", compresslevel=1) as tar:
            tar.add(mac["build"] / "python", arcname="python")
        digest = hashlib.sha256(tarball.read_bytes()).hexdigest()
        if tamper:
            with tarfile.open(tarball, "w:gz", compresslevel=1) as tar:
                tar.add(mac["build"] / "python" / "bin", arcname="python/bin")
        key = pin + arch.upper()
        text, n = re.subn(rf"(?m)^PYTHON_SHA256_{key}=.*$", f"PYTHON_SHA256_{key}={digest}", text)
        assert n == 1, key
    script.write_text(text)


def node_name(version: str, arch: str, system: str = "darwin") -> str:
    return f"node-v{version}-{system}-{NODE_ARCHES[arch]}.tar.xz"


def serve_node(mac: dict, version: str, tamper: bool = False) -> None:
    """Serves a stand-in nodejs.org build for both kinds of Mac and of Linux server, a `node`
    and npm's entry point in its layout, and pins it in the plugin's install.sh. Tampered, what's
    served isn't what the pinned checksum says."""
    script = mac["plugin"] / "install.sh"
    text = re.sub(r"(?m)^NODE_VERSION=.*$", f"NODE_VERSION={version}", script.read_text())
    for (_, system, pin), (arch, node_arch) in itertools.product(SYSTEMS, NODE_ARCHES.items()):
        top = mac["tmp"] / f"node-v{version}-{system}-{node_arch}"
        (top / "bin").mkdir(parents=True, exist_ok=True)
        (top / "bin" / "node").write_text(f"#!/bin/sh\necho v{version}\n")
        (top / "bin" / "node").chmod(0o755)
        npm = top / "lib" / "node_modules" / "npm" / "bin" / "npm-cli.js"
        npm.parent.mkdir(parents=True, exist_ok=True)
        npm.write_text("#!/usr/bin/env node\n")
        tarball = mac["served"] / node_name(version, arch, system)
        with tarfile.open(tarball, "w:xz") as tar:
            tar.add(top, arcname=top.name)
        digest = hashlib.sha256(tarball.read_bytes()).hexdigest()
        if tamper:
            with tarfile.open(tarball, "w:xz") as tar:
                tar.add(top / "bin", arcname=f"{top.name}/bin")
        key = pin + node_arch.upper()
        text, n = re.subn(rf"(?m)^NODE_SHA256_{key}=.*$", f"NODE_SHA256_{key}={digest}", text)
        assert n == 1, key
    script.write_text(text)


def offline(mac: dict) -> None:
    for tarball in mac["served"].iterdir():
        tarball.unlink()


def node_offline(mac: dict) -> None:
    for tarball in mac["served"].glob("node-*"):
        tarball.unlink()


def program(mac: dict, requires: tuple[str, ...], constraints: str | None = None,
            google: tuple[str, ...] = ()) -> None:
    """A stand-in for app/: a `family-brief` that needs `requires`, and `google` in its google
    extra, with its constraints file pinning `constraints` (`name==version` lines), or every
    package in the packages folder, each with the checksums of its files there."""
    app = project(mac["plugin"] / "app", "family-brief", "9.9.9", requires, extras={"google": list(google)},
                  scripts={"parent-recap": "family_brief.__main__:main"})
    shutil.copy(ROOT / "app" / "requirements.py", app / "requirements.py")
    (app / "family_brief").mkdir()
    (app / "family_brief" / "__init__.py").write_text("")
    (app / "family_brief" / "__main__.py").write_text(
        "import os, sys\ndef main():\n    print('runs on', os.path.realpath(sys.executable))\n")
    # The real ones record what the install made (test_uninstall.py), put the command on the PATH
    # (test_command_path.py), install the Baileys tree (test_baileys_install.py), restart the
    # Linked Device (test_jobs.py), put Parent Recap Settings and Check in ~/Applications
    # (test_program_entries.py) and update the evening schedule (test_schedule_update.py). These
    # note, in order, that they were asked.
    for module in ("install_record", "command_path", "baileys_install", "linked_device",
                   "program_entries", "schedule_update"):
        (app / "family_brief" / f"{module}.py").write_text(
            "import pathlib, sys\n"
            "with open(pathlib.Path.home() / 'installer.calls', 'a') as calls:\n"
            f"    calls.write(' '.join(['{module}', *sys.argv[1:2]]) + '\\n')\n")
    # The real one moves an installed wilma CLI to the pinned version (test_setup_wilma.py). This
    # one notes that it was asked.
    (app / "family_brief" / "setup_wilma.py").write_text(
        "import pathlib, sys\n"
        "with open(pathlib.Path.home() / 'setup_wilma.calls', 'a') as calls:\n"
        "    calls.write(' '.join(sys.argv[1:]) + '\\n')\n")
    # The real one reads the config with PyYAML; its answer is tested in test_without_google_packages.py.
    (app / "family_brief" / "google_packages.py").write_text(
        "import pathlib, sys\n"
        "config = pathlib.Path.home() / '.family' / 'config.yaml'\n"
        "sys.exit(0 if config.exists() and 'mode: google' in config.read_text() else 1)\n")
    (app / "constraints.txt").write_text(pinned(mac["packages"], constraints))
    # The real one, which install.sh runs to switch to the version it installed (ADR 0047).
    shutil.copy(ROOT / "app" / "src" / "family_brief" / "versions.py",
                app / "family_brief" / "versions.py")


def pinned(packages: Path, constraints: str | None) -> str:
    """A constraints file as scripts/pin_hashes.py writes one: each `name==version` line of
    `constraints`, or by default each package in `packages`, with the checksums of its files
    there. pip is pinned at the version a new venv already has, as nothing serves another."""
    files: dict[tuple[str, str], list[str]] = {}
    for path in sorted(p for p in packages.iterdir() if p.is_file()):
        name, version = path.name.removesuffix(".tar.gz").split("-")[:2]
        files.setdefault((canonical(name), version), []).append(hashlib.sha256(path.read_bytes()).hexdigest())
    if constraints is None:
        pins = list(files)
    else:
        pins = [(canonical(name), version) for name, version in
                (line.split("==") for line in constraints.splitlines() if line)]
    if "pip" not in dict(pins):
        pins.append(("pip", ensurepip.version()))
    # A pin with no file here gets a checksum nothing has: the venv's own pip is already
    # installed, so pip doesn't look at it.
    return "".join(f"{name}=={version} "
                   + " ".join(f"--hash=sha256:{h}" for h in files.get((name, version), ["0" * 64])) + "\n"
                   for name, version in pins)


def version(mac: dict, number: str) -> None:
    """The release the plugin folder holds."""
    (mac["plugin"] / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "parent-recap", "version": number}))


def google_mode(mac: dict) -> None:
    (mac["home"] / ".family").mkdir(parents=True, exist_ok=True)
    (mac["home"] / ".family" / "config.yaml").write_text("google_calendar:\n  mode: google\n")


def install(mac: dict, path_first: Path | None = None) -> subprocess.CompletedProcess:
    path = f"{mac['bin']}:{os.environ['PATH']}"
    path = f"{path_first}:{path}" if path_first else path
    env = {**os.environ, "PATH": path, "HOME": str(mac["home"]), "PARENT_RECAP_HOME": str(mac["home"] / "ParentRecap"),
           "PIP_NO_INDEX": "1", "PIP_FIND_LINKS": str(mac["packages"]),
           "PIP_DISABLE_PIP_VERSION_CHECK": "1", "PIP_NO_CACHE_DIR": "1"}
    env.pop("PIP_CONSTRAINT", None)
    return subprocess.run(["/bin/bash", str(mac["plugin"] / "install.sh")], capture_output=True, text=True,
                          env=env, timeout=300)


def runtimes(mac: dict) -> Path:
    return mac["home"] / "ParentRecap" / "runtime"


def venv_python(mac: dict) -> Path:
    return mac["home"] / "ParentRecap" / "app" / ".venv" / "bin" / "python"


def real_python(mac: dict) -> Path:
    return Path(os.path.realpath(venv_python(mac)))


def downloads(mac: dict) -> list[str]:
    return mac["downloads"].read_text().splitlines() if mac["downloads"].exists() else []


def installed(mac: dict) -> dict[str, str]:
    pip = mac["home"] / "ParentRecap" / "app" / ".venv" / "bin" / "pip"
    listing = subprocess.run([pip, "list", "--format", "json"], capture_output=True, text=True, check=True)
    return {p["name"]: p["version"] for p in json.loads(listing.stdout)}


@pytest.mark.macos
def test_installs_the_versions_in_the_constraints_file_not_the_newest(mac):
    for version in ("1.0", "2.0"):
        wheel(mac["packages"], "pinned-dep", version)
    program(mac, ("pinned-dep",), "pinned-dep==1.0\n")

    result = install(mac)

    assert result.returncode == 0, result.stdout + result.stderr
    assert installed(mac)["pinned-dep"] == "1.0"


@pytest.mark.macos
def test_a_package_with_no_prebuilt_version_stops_the_install_without_compiling(mac):
    built = mac["tmp"] / "compiled"
    sdist(mac["packages"], "needs-compiling", "1.0", build_marker=built)
    program(mac, ("needs-compiling",))

    result = install(mac)

    assert result.returncode != 0
    assert not built.exists()
    assert "needs-compiling" in install_log(mac).read_text()
    assert "installed to" not in result.stdout


# --- Google's packages, only for Google Calendar ------------------------------------------

@pytest.mark.macos
def test_a_default_install_leaves_out_googles_packages(mac):
    wheel(mac["packages"], "pure-dep", "1.0")
    wheel(mac["packages"], "google-dep", "1.0")
    program(mac, ("pure-dep",), "pure-dep==1.0\ngoogle-dep==1.0\n", google=("google-dep",))

    result = install(mac)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "pure-dep" in installed(mac) and "google-dep" not in installed(mac)


@pytest.mark.macos
def test_a_google_calendar_household_gets_them_at_the_pinned_version(mac):
    # An update for a Household already on Google Calendar, or manage turning it on: either way
    # the config says google before install.sh runs.
    for version in ("1.0", "2.0"):
        wheel(mac["packages"], "google-dep", version)
    program(mac, (), "google-dep==1.0\n", google=("google-dep",))
    google_mode(mac)

    result = install(mac)

    assert result.returncode == 0, result.stdout + result.stderr
    assert installed(mac)["google-dep"] == "1.0"


@pytest.mark.macos
def test_googles_packages_are_never_compiled_either(mac):
    built = mac["tmp"] / "compiled"
    sdist(mac["packages"], "google-dep", "1.0", build_marker=built)
    program(mac, (), google=("google-dep",))
    google_mode(mac)

    result = install(mac)

    assert result.returncode != 0
    assert not built.exists()
    assert "couldn't install its Python packages" in result.stdout
    assert "google-dep" in install_log(mac).read_text()


# --- The wilma CLI -------------------------------------------------------------------------------

@pytest.mark.macos
def test_every_install_moves_the_wilma_cli_to_the_pinned_version(mac):
    # An update is how a Household that connected Wilma gets the version a release pins.
    wheel(mac["packages"], "pure-dep", "1.0")
    program(mac, ("pure-dep",))

    for _ in range(2):
        result = install(mac)
        assert result.returncode == 0, result.stdout + result.stderr

    assert (mac["home"] / "setup_wilma.calls").read_text() == "update\nupdate\n"


# --- The Linked Device's Baileys tree ------------------------------------------------------------

@pytest.mark.macos
def test_every_install_installs_the_baileys_tree_once_the_runtimes_are_recorded(mac):
    # Its own module leaves an up-to-date tree alone, so this is how an update moves it.
    wheel(mac["packages"], "pure-dep", "1.0")
    program(mac, ("pure-dep",))

    for _ in range(2):
        result = install(mac)
        assert result.returncode == 0, result.stdout + result.stderr

    assert (mac["home"] / "installer.calls").read_text().splitlines()[:3] == \
        ["install_record program", "command_path install", "baileys_install install"]


# --- The evening schedule ------------------------------------------------------------------------

@pytest.mark.macos
def test_every_install_on_a_mac_updates_the_evening_schedule_last(mac):
    # How an older install's job comes to run under caffeinate, and its wake moves to the Brief's
    # time (#832): the module leaves an install that isn't scheduled alone.
    wheel(mac["packages"], "pure-dep", "1.0")
    program(mac, ("pure-dep",))

    for _ in range(2):
        result = install(mac)
        assert result.returncode == 0, result.stdout + result.stderr

    assert (mac["home"] / "installer.calls").read_text() == (
        "install_record program\ncommand_path install\nbaileys_install install\n"
        "linked_device restart\nprogram_entries install\nschedule_update\n") * 2


@pytest.mark.macos
def test_every_install_on_a_mac_makes_settings_and_check_for_its_folder(mac):
    # An update makes them again, so a family that installed before they existed gets them (ADR 0042).
    wheel(mac["packages"], "pure-dep", "1.0")
    program(mac, ("pure-dep",))
    entries = mac["plugin"] / "app" / "family_brief" / "program_entries.py"
    entries.write_text("import pathlib, sys\n"
                       "with open(pathlib.Path.home() / 'entries.calls', 'a') as calls:\n"
                       "    calls.write(' '.join(sys.argv[1:]) + '\\n')\n")

    for _ in range(2):
        result = install(mac)
        assert result.returncode == 0, result.stdout + result.stderr

    assert (mac["home"] / "entries.calls").read_text() == \
        f"install {mac['home'] / 'ParentRecap'}\n" * 2


@pytest.mark.macos
def test_the_last_lines_say_the_command_for_a_new_window_and_for_this_one(mac):
    # This Terminal's PATH is the one it opened with, before ~/.zprofile had the line (#560).
    wheel(mac["packages"], "pure-dep", "1.0")
    program(mac, ("pure-dep",))

    result = install(mac)

    assert result.returncode == 0, result.stdout + result.stderr
    last = result.stdout.splitlines()[-1]
    assert "parent-recap in a new Terminal window" in last
    assert str(mac["home"] / "ParentRecap" / "app" / ".venv" / "bin" / "parent-recap") in last
    assert (mac["home"] / "installer.calls").read_text().splitlines()[1] == "command_path install"


# --- Each version beside the running one (ADR 0047) ----------------------------------------------

def target(mac: dict) -> Path:
    return mac["home"] / "ParentRecap"


def running(mac: dict) -> Path:
    """The version's folder `app` points at."""
    app = target(mac) / "app"
    assert app.is_symlink(), "app isn't a link to a version"
    return Path(os.path.realpath(app))


def kept(mac: dict) -> Path | None:
    link = target(mac) / "versions" / "previous"
    return Path(os.path.realpath(link)) if link.is_symlink() else None


def runs(mac: dict) -> str:
    """What the program says it runs on, started as the jobs and entries start it."""
    ran = subprocess.run([target(mac) / "app" / ".venv" / "bin" / "parent-recap"],
                         capture_output=True, text=True)
    assert ran.returncode == 0, ran.stderr
    return ran.stdout.strip()


def code_of(mac: dict) -> Path:
    """The folder the program's code is imported from, run through `app`."""
    ran = subprocess.run([target(mac) / "app" / ".venv" / "bin" / "python", "-c",
                          "import family_brief, os; print(os.path.realpath(family_brief.__file__))"],
                         capture_output=True, text=True, check=True)
    return Path(ran.stdout.strip()).parents[1]


def watch_app_during_install(mac: dict) -> Path:
    """Has the install's step that runs on the new version before the switch note what `app`
    held then."""
    seen = mac["home"] / "app-during-install"
    (mac["plugin"] / "app" / "family_brief" / "google_packages.py").write_text(
        "import pathlib, sys\n"
        "app = pathlib.Path.home() / 'ParentRecap' / 'app' / 'VERSION'\n"
        f"pathlib.Path({str(seen)!r}).write_text(app.read_text() if app.exists() else 'none')\n"
        "sys.exit(1)\n")
    return seen


@pytest.mark.macos
def test_an_install_over_an_older_version_switches_to_it_only_once_it_finished(mac):
    program(mac, ())
    assert install(mac).returncode == 0
    old = running(mac)
    seen = watch_app_during_install(mac)
    version(mac, "9.9.10")

    result = install(mac)

    assert result.returncode == 0, result.stdout + result.stderr
    assert seen.read_text() == "9.9.9\n"  # the running version, while the new one installed
    new = running(mac)
    assert new != old and new.parent == target(mac) / "versions" and new.name.startswith("9.9.10.")
    assert (target(mac) / "app" / "VERSION").read_text() == "9.9.10\n"
    assert code_of(mac) == new
    # The version before is kept as it was, to switch back to.
    assert kept(mac) == old and (old / "VERSION").read_text() == "9.9.9\n"
    assert subprocess.run([old / ".venv" / "bin" / "python", "-c", ""]).returncode == 0
    assert "installed to" in result.stdout


@pytest.mark.macos
def test_an_install_that_fails_part_way_leaves_the_running_version_with_nothing_switched(mac):
    program(mac, ())
    assert install(mac).returncode == 0
    old, before = running(mac), runs(mac)
    sdist(mac["packages"], "needs-compiling", "1.0", build_marker=mac["tmp"] / "compiled")
    shutil.rmtree(mac["plugin"] / "app")
    program(mac, ("needs-compiling",))
    version(mac, "9.9.10")

    result = install(mac)

    assert result.returncode != 0
    assert "couldn't install its Python packages" in result.stdout
    assert running(mac) == old and kept(mac) is None and runs(mac) == before
    # Nothing of the failed install is left beside it.
    assert sorted(p.name for p in (target(mac) / "versions").iterdir()) == [old.name]


@pytest.mark.macos
def test_a_third_install_removes_the_oldest_version(mac):
    program(mac, ())
    assert install(mac).returncode == 0
    first = running(mac)
    version(mac, "9.9.10")
    assert install(mac).returncode == 0
    second = running(mac)
    version(mac, "9.9.11")

    result = install(mac)

    assert result.returncode == 0, result.stdout + result.stderr
    assert kept(mac) == second and not first.exists()
    assert sorted(p.name for p in (target(mac) / "versions").iterdir()) == sorted(
        [second.name, running(mac).name, "previous"])


@pytest.mark.macos
def test_installing_the_same_version_again_keeps_the_one_before_it(mac):
    # As manage does to add Google's packages: switching back still goes to another version.
    program(mac, ())
    assert install(mac).returncode == 0
    first = running(mac)
    version(mac, "9.9.10")
    assert install(mac).returncode == 0
    replaced = running(mac)

    assert install(mac).returncode == 0

    assert kept(mac) == first and running(mac) != replaced
    # It may still be running a Brief, so it goes with the next install.
    assert replaced.is_dir()
    version(mac, "9.9.11")
    assert install(mac).returncode == 0
    assert not replaced.exists() and not first.exists()


@pytest.mark.macos
def test_switching_back_runs_the_previous_version_on_its_own_python_from_the_same_path(mac):
    from family_brief import versions
    program(mac, ())
    assert install(mac).returncode == 0
    old, on_old = running(mac), runs(mac)
    serve(mac, "3.13.17", "20261101")
    version(mac, "9.9.10")
    assert install(mac).returncode == 0
    new, on_new = running(mac), runs(mac)
    assert on_new != on_old and "python-3.13.17-20261101" in on_new

    assert versions.switch_back(target(mac)) == ("9.9.9", "9.9.10")

    # Started from the same path, it's the version before, on the Python it was installed on.
    assert running(mac) == old and kept(mac) == new
    assert runs(mac) == on_old and code_of(mac) == old

    assert versions.switch_back(target(mac)) == ("9.9.10", "9.9.9")
    assert running(mac) == new and runs(mac) == on_new


def legacy_install(mac: dict, number: str) -> Path:
    """An install from before versions, as its install.sh left it: the program in `app` itself,
    with a venv built there on the pinned Python, which a first install here put in runtime/."""
    program(mac, ())
    assert install(mac).returncode == 0
    shutil.rmtree(target(mac) / "versions")
    (target(mac) / "app").unlink()
    app = target(mac) / "app"
    shutil.copytree(mac["plugin"] / "app", app)
    (app / "VERSION").write_text(f"{number}\n")
    python = runtimes(mac) / "python-3.13.16-20261003" / "bin" / "python3"
    subprocess.run([python, "-m", "venv", app / ".venv"], check=True)
    env = {**os.environ, "PIP_NO_INDEX": "1", "PIP_FIND_LINKS": str(mac["packages"]),
           "PIP_DISABLE_PIP_VERSION_CHECK": "1", "PIP_NO_CACHE_DIR": "1"}
    subprocess.run([app / ".venv" / "bin" / "pip", "install", "-q", "-e", app], env=env,
                   check=True, capture_output=True)
    return app


@pytest.mark.macos
def test_an_install_from_before_versions_is_taken_over_without_a_reinstall(mac):
    from family_brief import versions
    legacy_install(mac, "9.9.8")
    on_legacy = runs(mac)

    result = install(mac)

    assert result.returncode == 0, result.stdout + result.stderr
    before = kept(mac)
    assert before is not None and before.name == "9.9.8.before"
    assert running(mac).name.startswith("9.9.9.")

    # Its venv names `app`, so it runs again once `app` points at it.
    versions.switch_back(target(mac))
    assert (target(mac) / "app" / "VERSION").read_text() == "9.9.8\n"
    assert runs(mac) == on_legacy and code_of(mac) == before


# --- The install log -------------------------------------------------------------------------

def install_log(mac: dict) -> Path:
    logs = sorted((mac["home"] / "ParentRecap" / "logs").glob("install-*.log"))
    assert len(logs) == 1, logs
    assert re.fullmatch(r"install-\d{4}-\d{2}-\d{2}-\d{6}\.log", logs[0].name)
    return logs[0]


@pytest.mark.macos
def test_a_failing_pip_step_shows_a_plain_message_and_the_log_instead_of_pips_output(mac):
    sdist(mac["packages"], "needs-compiling", "1.0", build_marker=mac["tmp"] / "compiled")
    program(mac, ("needs-compiling",))

    result = install(mac)

    shown = result.stdout + result.stderr
    log = install_log(mac)
    assert result.returncode != 0
    assert "couldn't install its Python packages" in shown
    assert str(log) in shown
    assert "send" in shown and "Parent Recap team" in shown
    assert "needs-compiling" not in shown
    first, *rest = log.read_text().splitlines()
    assert first.startswith("Mac: ")
    # pip's words when the only file of the pinned version is one it would have to compile.
    assert "no matching distributions available" in "\n".join(rest) and "    needs-compiling" in rest


@pytest.mark.macos
def test_every_install_keeps_a_dated_log_that_starts_with_the_mac_it_ran_on(mac):
    wheel(mac["packages"], "pure-dep", "1.0")
    program(mac, ("pure-dep",))

    result = install(mac)

    assert result.returncode == 0, result.stdout + result.stderr
    first, *rest = install_log(mac).read_text().splitlines()
    venv = mac["home"] / "ParentRecap" / "app" / ".venv" / "bin"
    pip_version = subprocess.run([venv / "pip", "--version"], capture_output=True, text=True).stdout.split()[1]
    machine = "aarch64"  # the build picked for the fake sysctl's Apple Silicon Mac
    python = subprocess.run([venv / "python", "-c", "import platform; print(platform.python_version())"],
                            capture_output=True, text=True).stdout.strip()
    cc = shutil.which("cc") or "none"
    env = re.fullmatch(r"Mac: (\S+), macOS (\S+), Python (\S+) \((\S+)\), pip (\S+), cc (\S+)", first)
    assert env, first
    assert env.groups() == (machine, MACOS, env[3], python, pip_version, cc)
    assert env[3].startswith("~/ParentRecap/runtime/")
    assert os.path.realpath(env[3].replace("~", str(mac["home"]), 1)) == os.path.realpath(venv / "python")
    assert any("pure-dep" in line for line in rest)


@pytest.mark.macos
def test_the_log_writes_the_home_folder_as_a_tilde_so_it_names_no_account(mac):
    tools = mac["home"] / "bin"
    tools.mkdir(parents=True)
    (tools / "cc").write_text("#!/bin/sh\n")
    (tools / "cc").chmod(0o755)
    wheel(mac["packages"], "pure-dep", "1.0")
    program(mac, ("pure-dep",))

    result = install(mac, path_first=tools)

    assert result.returncode == 0, result.stdout + result.stderr
    first = install_log(mac).read_text().splitlines()[0]
    assert first.endswith(", cc ~/bin/cc")
    assert str(mac["home"]) not in first


# --- Every package checked against the checksums the release holds (#931) ---------------------

def tamper(mac: dict, name: str, version: str) -> None:
    """The package's file as served now isn't the one its checksum was taken of."""
    wheel(mac["packages"], name, version, files={"tampered.py": "print('not what was pinned')\n"})


@pytest.mark.macos
def test_a_package_that_doesnt_match_its_checksum_stops_an_update_and_the_running_version_works(mac):
    wheel(mac["packages"], "pure-dep", "1.0")
    program(mac, ("pure-dep",))
    assert install(mac).returncode == 0
    old, before = running(mac), runs(mac)
    version(mac, "9.9.10")
    tamper(mac, "pure-dep", "1.0")

    result = install(mac)

    assert result.returncode != 0
    assert "couldn't install its Python packages" in result.stdout
    assert "DO NOT MATCH THE HASHES" in "".join(p.read_text() for p in (target(mac) / "logs").glob("install-*.log"))
    assert running(mac) == old and runs(mac) == before
    assert sorted(p.name for p in (target(mac) / "versions").iterdir()) == [old.name]


@pytest.mark.macos
def test_googles_packages_are_checked_against_their_checksums_too(mac):
    wheel(mac["packages"], "google-dep", "1.0")
    program(mac, (), google=("google-dep",))
    google_mode(mac)
    tamper(mac, "google-dep", "1.0")

    result = install(mac)

    assert result.returncode != 0
    assert "couldn't install its Python packages" in result.stdout
    assert "DO NOT MATCH THE HASHES" in install_log(mac).read_text()
    assert not (target(mac) / "app").exists()


@pytest.mark.macos
def test_a_package_the_constraints_dont_pin_is_refused(mac):
    # One a pinned package brings in, which a newer version of it may name.
    wheel(mac["packages"], "pure-dep", "1.0", requires=("unpinned-dep",))
    wheel(mac["packages"], "unpinned-dep", "1.0")
    program(mac, ("pure-dep",), "pure-dep==1.0\n")

    result = install(mac)

    assert result.returncode != 0
    assert "unpinned-dep" in install_log(mac).read_text()
    assert not (target(mac) / "app").exists()


@pytest.fixture(scope="session")
def pip_wheel(tmp_path_factory) -> Path:
    """The pip running the tests, as a wheel of version 99.0, which no venv comes with."""
    folder = tmp_path_factory.mktemp("pip-wheel")
    files = {f.as_posix(): f.locate().read_bytes() for f in importlib.metadata.distribution("pip").files
             if f.parts[0] == "pip" and "__pycache__" not in f.parts}
    return wheel(folder, "pip", "99.0", files=files, scripts={"pip": "pip._internal.cli.main:main"})


@pytest.mark.macos
def test_pip_is_installed_at_the_version_the_constraints_pin_not_the_newest(mac, pip_wheel):
    shutil.copy(pip_wheel, mac["packages"])
    wheel(mac["packages"], "pure-dep", "1.0")
    program(mac, ("pure-dep",))

    result = install(mac)

    assert result.returncode == 0, result.stdout + result.stderr
    assert installed(mac)["pip"] == "99.0"
    # The log names the pip that installed the packages.
    assert ", pip 99.0, " in install_log(mac).read_text().splitlines()[0]


def test_the_installs_and_ci_take_no_newest_package_and_check_every_one():
    # The program from its own folder is the one install without checksums, and brings nothing.
    for path in ("install.sh", "install.ps1", ".github/workflows/tests.yml", "app/tests/README.md"):
        text = script_without_comments((ROOT / path).read_text())
        assert "--upgrade" not in text and "-U " not in text, path
        assert "--require-hashes" in text, path
        assert re.search(r"--no-deps['\",]*\s+['\"]?--no-build-isolation", text), path
        assert not re.search(r"pip install[^\n]*-c constraints\.txt -e", text), path


def script_without_comments(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


# --- What the release's own folder asks pip for (app/requirements.py) ----------------------------

def requirements(*extras: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-I", str(ROOT / "app" / "requirements.py"), *extras],
                          capture_output=True, text=True)


def test_it_names_the_build_system_and_the_dependencies_and_only_the_extras_asked_for():
    project_ = tomllib.loads((ROOT / "app" / "pyproject.toml").read_text())["project"]

    default, google = requirements().stdout.split(), requirements("google").stdout.split()

    assert default[0] == "setuptools" and "pydantic" in default and "PyYAML" in default
    assert not {n for n in default if n.startswith("google") or n in ("pytest", "playwright")}
    assert google[:len(default)] == default
    assert set(google) - set(default) == {"google-api-python-client", "google-auth-oauthlib", "google-auth-httplib2"}
    assert len(project_["dependencies"]) + 1 == len(default)


def test_an_extra_pyproject_doesnt_have_fails():
    result = requirements("gogle")

    assert result.returncode != 0 and "gogle" in result.stderr and result.stdout == ""


# --- The shipped constraints file ------------------------------------------------------------

def shipped() -> dict[str, tuple[str, list[str]]]:
    """Each pin in app/constraints.txt, by name: its version and its checksums."""
    text = (ROOT / "app" / "constraints.txt").read_text().replace("\\\n", " ")
    lines = [line.split() for line in text.splitlines() if line and not line.startswith("#")]
    return {canonical(spec.split("==")[0]): (spec.split("==")[1], [h.removeprefix("--hash=sha256:") for h in rest])
            for spec, *rest in lines}


def pins() -> dict[str, str]:
    return {name: version for name, (version, _) in shipped().items()}


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def test_the_constraints_pin_every_dependency_pip_and_the_build_system_to_one_version_and_its_checksums():
    pyproject = tomllib.loads((ROOT / "app" / "pyproject.toml").read_text())
    project_, extras = pyproject["project"], pyproject["project"]["optional-dependencies"]
    wanted = [*pyproject["build-system"]["requires"], *project_["dependencies"], *extras["test"], *extras["google"]]
    names = {canonical(re.match(r"[\w.-]+", d).group()) for d in wanted}

    assert names | {"pip"} <= pins().keys()
    assert all(re.fullmatch(r"[\w.+!]+", v) for v in pins().values())
    assert all(hashes and all(re.fullmatch(r"[0-9a-f]{64}", h) for h in hashes)
               for _, hashes in shipped().values())


def test_a_default_install_brings_in_no_google_package_and_no_cryptography():
    # Everything the default dependencies need, from the metadata of what's installed here.
    from importlib.metadata import requires
    from packaging.requirements import Requirement
    todo = [Requirement(d) for d in tomllib.loads((ROOT / "app" / "pyproject.toml").read_text())["project"]["dependencies"]]
    needed: set[str] = set()
    while todo:
        req = todo.pop()
        name = canonical(req.name)
        if name in needed or (req.marker and not req.marker.evaluate({"extra": ""})):
            continue
        needed.add(name)
        todo += [Requirement(r) for r in requires(req.name) or []]

    assert needed and not {n for n in needed if n.startswith("google") or n == "cryptography"}


def test_cryptography_stays_below_49_while_intel_macs_are_supported():
    # 49 and later have no prebuilt package for Intel Macs, so pip compiled it there and failed.
    assert int(pins()["cryptography"].split(".")[0]) < 49


# --- CI's check that every pin has a prebuilt package for both kinds of Mac -----------------

def check_wheels(tmp_path: Path, packages: Path, constraints: str) -> subprocess.CompletedProcess:
    (tmp_path / "constraints.txt").write_text(constraints)
    env = {**os.environ, "PIP_NO_INDEX": "1", "PIP_FIND_LINKS": str(packages), "PIP_NO_CACHE_DIR": "1"}
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "check_wheels.py"), str(tmp_path / "constraints.txt")],
                          capture_output=True, text=True, env=env, timeout=300)


def test_the_check_passes_when_every_pin_has_a_wheel_for_both_macs(tmp_path):
    packages = tmp_path / "packages"
    packages.mkdir()
    wheel(packages, "pure-dep", "1.0")
    for arch in ("arm64", "x86_64"):
        wheel(packages, "native-dep", "1.0", tag=f"cp311-abi3-macosx_11_0_{arch}")

    result = check_wheels(tmp_path, packages, "# a comment\npure-dep==1.0\nnative-dep==1.0\n")

    assert result.returncode == 0, result.stdout + result.stderr


def test_the_check_fails_when_a_pin_has_no_intel_wheel(tmp_path):
    packages = tmp_path / "packages"
    packages.mkdir()
    wheel(packages, "native-dep", "1.0", tag="cp311-abi3-macosx_11_0_arm64")
    wheel(packages, "native-dep", "2.0", tag="cp311-abi3-macosx_11_0_x86_64")  # not the pinned one
    sdist(packages, "native-dep", "1.0", build_marker=tmp_path / "compiled")

    result = check_wheels(tmp_path, packages, "native-dep==1.0\n")

    assert result.returncode != 0
    assert "Intel Mac (macosx_13_0_x86_64), Python 3.11" in result.stderr
    assert "Apple Silicon" not in result.stderr
    assert "native-dep" in result.stderr
    assert not (tmp_path / "compiled").exists()


# --- Regenerating the pins with their checksums (scripts/pin_hashes.py) ----------------------

def pin_hashes():
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import pin_hashes
    finally:
        sys.path.pop(0)
    return pin_hashes


def test_regenerating_keeps_the_comments_and_pins_each_package_with_every_wheels_checksum():
    wheels = {("pip", "26.3"): ["b" * 64], ("PyYAML", "6.0.3"): ["d" * 64, "c" * 64, "d" * 64]}
    header = "# The versions...\n# cryptography stays below 49.\n"

    text = pin_hashes().pinned(header, "PyYAML==6.0.3\npip==26.3\n", lambda n, v: wheels[n, v])

    assert text == (header + f"pip==26.3 \\\n    --hash=sha256:{'b' * 64}\n"
                    f"PyYAML==6.0.3 \\\n    --hash=sha256:{'c' * 64} \\\n    --hash=sha256:{'d' * 64}\n")


def test_regenerating_stops_at_a_package_with_no_wheel():
    with pytest.raises(SystemExit, match="only-sdist 1.0 has no wheel"):
        pin_hashes().pinned("", "only-sdist==1.0\n", lambda n, v: [])


@pytest.mark.macos
def test_install_stops_over_an_install_from_before_the_rename(mac):
    program(mac, ())
    (mac["home"] / "FamilyBrief" / "app").mkdir(parents=True)

    result = install(mac)

    assert result.returncode != 0
    assert "Uninstall it with its own version first" in result.stdout
    assert not (mac["home"] / "ParentRecap").exists()


@pytest.mark.macos
@pytest.mark.parametrize("version", ["13.4.1", "13.0", "12.7.6", "10.15.7"])
def test_a_macos_older_than_13_5_stops_the_install_before_any_download(mac, version):
    program(mac, ())
    macos(mac, version)

    result = install(mac)

    assert result.returncode != 0
    assert f"Parent Recap needs macOS 13.5 (Ventura) or newer; this Mac has macOS {version}." in result.stdout
    assert "Update macOS, or install Parent Recap on another computer." in result.stdout
    assert "vaatii macOS 13.5:n (Ventura) tai uudemman" in result.stdout
    assert "需要 macOS 13.5 (Ventura) 或更新版本" in result.stdout
    assert downloads(mac) == []
    assert not (mac["home"] / "ParentRecap").exists()


@pytest.mark.macos
@pytest.mark.parametrize("version", ["13.5", "13.6.9", "14.0", "26.1", "13.5 beta", "unknown"])
def test_macos_13_5_and_newer_install_as_before(mac, version):
    program(mac, ())
    macos(mac, version)

    result = install(mac)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "needs macOS" not in result.stdout


def test_both_commands_name_the_same_entry_point():
    scripts = tomllib.loads((ROOT / "app" / "pyproject.toml").read_text())["project"]["scripts"]
    assert scripts["parent-recap"] == scripts["family-brief"] == "family_brief.__main__:main"


# --- Parent Recap's own Python (ADR 0011) ---------------------------------------------------

@pytest.mark.macos
def test_a_fresh_install_runs_parent_recap_on_the_pinned_python(mac):
    program(mac, ())

    result = install(mac)

    assert result.returncode == 0, result.stdout + result.stderr
    pinned = runtimes(mac) / "python-3.13.16-20261003"
    assert real_python(mac).is_relative_to(pinned)
    ran = subprocess.run([venv_python(mac).parent / "parent-recap"], capture_output=True, text=True)
    assert ran.stdout.strip() == f"runs on {real_python(mac)}", ran.stderr
    assert "the Mac's own python3" not in result.stdout + result.stderr


@pytest.mark.macos
def test_a_fresh_install_puts_the_pinned_node_next_to_the_pinned_python(mac):
    program(mac, ())

    result = install(mac)

    assert result.returncode == 0, result.stdout + result.stderr
    node = runtimes(mac) / f"node-v{NODE}" / "bin" / "node"
    assert subprocess.run([node], capture_output=True, text=True).stdout.strip() == f"v{NODE}"
    assert (node.parents[1] / "lib" / "node_modules" / "npm" / "bin" / "npm-cli.js").is_file()
    assert sorted(p.name for p in runtimes(mac).iterdir()) == [f"node-v{NODE}", "python-3.13.16-20261003"]


@pytest.mark.macos
@pytest.mark.parametrize("kind, arch", [("arm64", "aarch64"), ("intel", "x86_64"),
                                        ("rosetta", "aarch64")])
def test_picks_the_build_for_the_mac_s_chip_even_in_a_rosetta_terminal(mac, kind, arch):
    program(mac, ())
    a_mac(mac, kind)

    result = install(mac)

    assert result.returncode == 0, result.stdout + result.stderr
    assert downloads(mac) == [f"{MIRROR}/20261003/{build_name(*PINNED, arch)}",
                              f"https://nodejs.org/dist/v{NODE}/{node_name(NODE, arch)}"]


@pytest.mark.macos
@pytest.mark.parametrize("down, used", [(MIRROR, GITHUB), (GITHUB, MIRROR)])
def test_the_python_comes_from_whichever_of_the_mirror_and_github_serves_it(mac, down, used):
    # GitHub answered every python-build-standalone download with 504 for hours (#843).
    program(mac, ())
    mac["down"].write_text(down + "\n")

    result = install(mac)

    assert result.returncode == 0, result.stdout + result.stderr
    python = [u for u in downloads(mac) if "cpython-" in u]
    assert python[-1] == f"{used}/20261003/{build_name(*PINNED, 'aarch64')}"
    assert real_python(mac).is_relative_to(runtimes(mac) / "python-3.13.16-20261003")


@pytest.mark.macos
def test_when_neither_serves_the_python_the_install_says_so_as_before(mac):
    program(mac, ())
    mac["down"].write_text(f"{MIRROR}\n{GITHUB}\n")

    result = install(mac)

    assert result.returncode != 0
    assert "couldn't download its Python" in result.stdout and "Nothing was changed" in result.stdout
    assert [u.rsplit("/", 2)[0] for u in downloads(mac)] == [MIRROR, GITHUB]
    assert not (mac["home"] / "ParentRecap").exists()


@pytest.mark.macos
def test_running_the_install_again_reuses_the_python_already_there(mac):
    program(mac, ())
    assert install(mac).returncode == 0
    before = real_python(mac)

    result = install(mac)

    assert result.returncode == 0, result.stdout + result.stderr
    assert len(downloads(mac)) == 2  # the Python and the Node, once
    assert real_python(mac) == before


@pytest.mark.macos
def test_a_venv_on_another_python_is_rebuilt_on_the_pinned_one(mac):
    # Today's installs: the venv sits on Homebrew's Python, here the one running the tests.
    program(mac, ())
    venv = mac["home"] / "ParentRecap" / "app" / ".venv"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", venv], check=True)
    old = Path(os.path.realpath(venv / "bin" / "python"))

    result = install(mac)

    assert result.returncode == 0, result.stdout + result.stderr
    assert real_python(mac).is_relative_to(runtimes(mac)) and real_python(mac) != old
    assert "family_brief" in subprocess.run([venv_python(mac), "-m", "pip", "list"],
                                            capture_output=True, text=True).stdout.replace("-", "_")


@pytest.mark.macos
def test_a_new_pinned_version_goes_into_a_new_folder_and_asks_nothing_of_the_family(mac):
    program(mac, ())
    assert install(mac).returncode == 0
    old = real_python(mac)
    serve(mac, "3.13.17", "20261101")
    version(mac, "9.9.10")

    result = install(mac)

    assert result.returncode == 0, result.stdout + result.stderr
    # The old one stays while the version before, which runs on it, is kept (ADR 0047).
    assert sorted(p.name for p in runtimes(mac).iterdir()) == [
        f"node-v{NODE}", "python-3.13.16-20261003", "python-3.13.17-20261101"]
    assert real_python(mac).is_relative_to(runtimes(mac) / "python-3.13.17-20261101") and real_python(mac) != old
    # No permission names the Python since WhatsApp is no longer read from this Mac (ADR 0027).
    assert "grant" not in result.stdout.lower() and "Privacy & Security" not in result.stdout
    assert str(real_python(mac)) not in result.stdout


@pytest.mark.macos
def test_a_new_pinned_node_replaces_the_old_one_once_no_kept_version_runs_on_it(mac):
    program(mac, ())
    assert install(mac).returncode == 0
    python = real_python(mac)
    serve_node(mac, "24.22.0")
    version(mac, "9.9.10")

    result = install(mac)

    assert result.returncode == 0, result.stdout + result.stderr
    assert sorted(p.name for p in runtimes(mac).iterdir()) == [
        f"node-v{NODE}", "node-v24.22.0", "python-3.13.16-20261003"]
    assert real_python(mac) == python

    version(mac, "9.9.11")
    assert install(mac).returncode == 0
    assert sorted(p.name for p in runtimes(mac).iterdir()) == ["node-v24.22.0", "python-3.13.16-20261003"]


@pytest.mark.macos
@pytest.mark.parametrize("break_it, said", [
    (offline, "couldn't download its Python"),
    (lambda m: serve(m, "3.13.17", "20261101", tamper=True), "The Python Parent Recap downloaded"),
    (node_offline, "couldn't download its Node"),
    (lambda m: serve_node(m, "24.22.0", tamper=True), "The Node Parent Recap downloaded"),
], ids=["python-offline", "python-checksum", "node-offline", "node-checksum"])
def test_a_failed_download_leaves_the_existing_install_as_it_was(mac, break_it, said):
    program(mac, ())
    assert install(mac).returncode == 0
    before = {"python": real_python(mac), "runtimes": sorted(runtimes(mac).iterdir()),
              "version": (mac["home"] / "ParentRecap" / "app" / "VERSION").read_text()}
    serve(mac, "3.13.17", "20261101")
    serve_node(mac, "24.22.0")
    (mac["plugin"] / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "parent-recap", "version": "9.9.10"}))
    break_it(mac)

    result = install(mac)

    shown = result.stdout + result.stderr
    assert result.returncode != 0
    assert said in shown and "Nothing was changed" in shown and "install again" in shown
    assert "Traceback" not in shown and "curl:" not in shown
    assert {"python": real_python(mac), "runtimes": sorted(runtimes(mac).iterdir()),
            "version": (mac["home"] / "ParentRecap" / "app" / "VERSION").read_text()} == before


@pytest.mark.macos
def test_a_new_pinned_version_keeps_the_old_one_when_pip_fails(mac):
    program(mac, ())
    assert install(mac).returncode == 0
    serve(mac, "3.13.17", "20261101")
    sdist(mac["packages"], "needs-compiling", "1.0", build_marker=mac["tmp"] / "compiled")
    shutil.rmtree(mac["plugin"] / "app")
    program(mac, ("needs-compiling",))

    result = install(mac)

    assert result.returncode != 0
    assert "couldn't install its Python packages" in result.stdout
    assert (runtimes(mac) / "python-3.13.16-20261003" / "bin" / "python3").exists()


@pytest.mark.macos
@pytest.mark.parametrize("break_it", [offline, node_offline])
def test_a_failed_first_download_creates_nothing(mac, break_it):
    program(mac, ())
    break_it(mac)

    result = install(mac)

    assert result.returncode != 0
    assert "couldn't download" in result.stdout
    assert not (mac["home"] / "ParentRecap").exists()


# --- On a Linux server (ADR 0029) ------------------------------------------------------------

@pytest.mark.posix
@pytest.mark.parametrize("machine, arch", [("aarch64", "aarch64"), ("arm64", "aarch64"),
                                           ("x86_64", "x86_64")])
def test_a_linux_server_gets_the_linux_builds_for_its_chip(mac, machine, arch):
    program(mac, ())
    a_linux_server(mac, machine)

    result = install(mac)

    assert result.returncode == 0, result.stdout + result.stderr
    python = build_name(*PINNED, arch, "unknown-linux-gnu")
    node = node_name(NODE, arch, "linux")
    assert downloads(mac) == [
        f"{MIRROR}/20261003/{python}",
        f"https://nodejs.org/dist/v{NODE}/{node}"]
    assert real_python(mac).is_relative_to(runtimes(mac) / "python-3.13.16-20261003")
    ran = subprocess.run([venv_python(mac).parent / "parent-recap"], capture_output=True, text=True)
    assert ran.stdout.strip() == f"runs on {real_python(mac)}", ran.stderr
    assert install_log(mac).read_text().startswith(f"Linux: {arch}, ")
    last = result.stdout.splitlines()[-1]
    assert "parent-recap in a new login shell" in last
    assert (mac["home"] / "installer.calls").read_text().splitlines()[1] == "command_path install"
    # A Hosted Household's server gets no Settings or Check entries (ADR 0042).
    assert "program_entries install" not in (mac["home"] / "installer.calls").read_text()


@pytest.mark.posix
@pytest.mark.parametrize("break_it, said", [
    (lambda m: serve(m, *PINNED, tamper=True), "The Python Parent Recap downloaded"),
    (lambda m: serve_node(m, NODE, tamper=True), "The Node Parent Recap downloaded"),
    (offline, "couldn't download its Python (is this server online?)"),
], ids=["python-checksum", "node-checksum", "offline"])
def test_on_linux_a_download_that_isnt_the_pinned_one_creates_nothing(mac, break_it, said):
    program(mac, ())
    a_linux_server(mac, "aarch64")
    break_it(mac)

    result = install(mac)

    assert result.returncode != 0
    assert said in result.stdout and "Nothing was changed" in result.stdout
    assert not (mac["home"] / "ParentRecap").exists()


@pytest.mark.posix
def test_a_linux_server_with_another_chip_stops_before_any_download(mac):
    program(mac, ())
    a_linux_server(mac, "riscv64")

    result = install(mac)

    assert result.returncode != 0
    assert "riscv64" in result.stdout and "Nothing was changed" in result.stdout
    assert downloads(mac) == [] and not (mac["home"] / "ParentRecap").exists()
