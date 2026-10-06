"""`install.sh` installs Parent Recap's own pinned Python, and only prebuilt packages at the
versions in the shipped constraints file.

Runs the real `install.sh` and the real pip, offline: pip reads packages only from a folder made
here (`fake_packages.py`), and the program it installs is a stand-in with the real one's layout.
The pinned Python comes from a fake `curl` serving a stand-in python-build-standalone tarball
(`python_build`), and the Mac's kind from a fake `sysctl`. A `python3` on PATH fails, so nothing
runs on a Python the Mac already has.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tomllib
from pathlib import Path

import pytest

from fake_packages import project, sdist, wheel

ROOT = Path(__file__).resolve().parents[2]


PINNED = ("3.13.16", "20261003")


@pytest.fixture(scope="session")
def python_build(tmp_path_factory) -> Path:
    """python-build-standalone's install_only layout under `python/`, standing in for the pinned
    Python: a copy of the base Python running the tests (a copy, so its real path is in the
    folder it's unpacked to), `python3` linking to it, and `lib` its standard library."""
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
         "downloads": tmp_path / "downloads.log"}
    m["served"].mkdir()
    # Serves the file named like the URL's last part, or fails as curl does offline.
    fake(bin_ / "curl", f"""
url=; out=
while [ $# -gt 0 ]; do case "$1" in -o) out=$2; shift ;; http*) url=$1 ;; esac; shift; done
echo "$url" >> "{m['downloads']}"
served="{m['served']}/$(basename "$url")"
[ -f "$served" ] || {{ echo "curl: (6) Could not resolve host: github.com" >&2; exit 6; }}
cp "$served" "$out"
""")
    fake(bin_ / "python3", 'echo "the Mac\'s own python3 was run" >&2; exit 97')
    a_mac(m, "arm64")
    serve(m, *PINNED)
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


def build_name(version: str, build: str, arch: str) -> str:
    return f"cpython-{version}+{build}-{arch}-apple-darwin-install_only.tar.gz"


def serve(mac: dict, version: str, build: str, tamper: bool = False) -> None:
    """Serves the build for both kinds of Mac and pins it in the plugin's install.sh. Tampered,
    what's served isn't what the pinned checksum says."""
    script = mac["plugin"] / "install.sh"
    text = script.read_text()
    text = re.sub(r"(?m)^PYTHON_VERSION=.*$", f"PYTHON_VERSION={version}", text)
    text = re.sub(r"(?m)^PYTHON_BUILD=.*$", f"PYTHON_BUILD={build}", text)
    for arch in ("aarch64", "x86_64"):
        tarball = mac["served"] / build_name(version, build, arch)
        with tarfile.open(tarball, "w:gz", compresslevel=1) as tar:
            tar.add(mac["build"] / "python", arcname="python")
        digest = hashlib.sha256(tarball.read_bytes()).hexdigest()
        if tamper:
            with tarfile.open(tarball, "w:gz", compresslevel=1) as tar:
                tar.add(mac["build"] / "python" / "bin", arcname="python/bin")
        key = arch.upper()
        text, n = re.subn(rf"(?m)^PYTHON_SHA256_{key}=.*$", f"PYTHON_SHA256_{key}={digest}", text)
        assert n == 1, key
    script.write_text(text)


def offline(mac: dict) -> None:
    for tarball in mac["served"].iterdir():
        tarball.unlink()


def program(mac: dict, requires: tuple[str, ...], constraints: str = "",
            google: tuple[str, ...] = ()) -> None:
    """A stand-in for app/: a `family-brief` that needs `requires`, and `google` in its google
    extra, with its constraints file."""
    app = project(mac["plugin"] / "app", "family-brief", "9.9.9", requires, extras={"google": list(google)},
                  scripts={"parent-recap": "family_brief.__main__:main"})
    (app / "family_brief").mkdir()
    (app / "family_brief" / "__init__.py").write_text("")
    (app / "family_brief" / "__main__.py").write_text(
        "import os, sys\ndef main():\n    print('runs on', os.path.realpath(sys.executable))\n")
    (app / "family_brief" / "install_record.py").write_text("")
    # The real one reads the config with PyYAML; its answer is tested in test_without_google_packages.py.
    (app / "family_brief" / "google_packages.py").write_text(
        "import pathlib, sys\n"
        "config = pathlib.Path.home() / '.family' / 'config.yaml'\n"
        "sys.exit(0 if config.exists() and 'mode: google' in config.read_text() else 1)\n")
    (app / "constraints.txt").write_text(constraints)


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


def test_installs_the_versions_in_the_constraints_file_not_the_newest(mac):
    for version in ("1.0", "2.0"):
        wheel(mac["packages"], "pinned-dep", version)
    program(mac, ("pinned-dep",), "pinned-dep==1.0\n")

    result = install(mac)

    assert result.returncode == 0, result.stdout + result.stderr
    assert installed(mac)["pinned-dep"] == "1.0"


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

def test_a_default_install_leaves_out_googles_packages(mac):
    wheel(mac["packages"], "pure-dep", "1.0")
    wheel(mac["packages"], "google-dep", "1.0")
    program(mac, ("pure-dep",), "pure-dep==1.0\ngoogle-dep==1.0\n", google=("google-dep",))

    result = install(mac)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "pure-dep" in installed(mac) and "google-dep" not in installed(mac)


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


# --- The install log -------------------------------------------------------------------------

def install_log(mac: dict) -> Path:
    logs = sorted((mac["home"] / "ParentRecap" / "logs").glob("install-*.log"))
    assert len(logs) == 1, logs
    assert re.fullmatch(r"install-\d{4}-\d{2}-\d{2}-\d{6}\.log", logs[0].name)
    return logs[0]


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
    assert any("No matching distribution found for needs-compiling" in line for line in rest)


def test_every_install_keeps_a_dated_log_that_starts_with_the_mac_it_ran_on(mac):
    wheel(mac["packages"], "pure-dep", "1.0")
    program(mac, ("pure-dep",))

    result = install(mac)

    assert result.returncode == 0, result.stdout + result.stderr
    first, *rest = install_log(mac).read_text().splitlines()
    venv = mac["home"] / "ParentRecap" / "app" / ".venv" / "bin"
    pip_version = subprocess.run([venv / "pip", "--version"], capture_output=True, text=True).stdout.split()[1]
    machine = "aarch64"  # the build picked for the fake sysctl's Apple Silicon Mac
    macos = subprocess.run(["sw_vers", "-productVersion"], capture_output=True, text=True).stdout.strip()
    python = subprocess.run([venv / "python", "-c", "import platform; print(platform.python_version())"],
                            capture_output=True, text=True).stdout.strip()
    cc = shutil.which("cc") or "none"
    env = re.fullmatch(r"Mac: (\S+), macOS (\S+), Python (\S+) \((\S+)\), pip (\S+), cc (\S+)", first)
    assert env, first
    assert env.groups() == (machine, macos, env[3], python, pip_version, cc)
    assert env[3].startswith("~/ParentRecap/runtime/")
    assert os.path.realpath(env[3].replace("~", str(mac["home"]), 1)) == os.path.realpath(venv / "python")
    assert any("pure-dep" in line for line in rest)


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


def test_an_older_prebuilt_version_is_picked_over_a_newer_one_that_would_compile(mac):
    wheel(mac["packages"], "needs-compiling", "1.0")
    built = mac["tmp"] / "compiled"
    sdist(mac["packages"], "needs-compiling", "2.0", build_marker=built)
    program(mac, ("needs-compiling",))

    result = install(mac)

    assert result.returncode == 0, result.stdout + result.stderr
    assert not built.exists()
    assert installed(mac)["needs-compiling"] == "1.0"


# --- The shipped constraints file ------------------------------------------------------------

def pins() -> dict[str, str]:
    lines = (ROOT / "app" / "constraints.txt").read_text().splitlines()
    pairs = [line.split("==") for line in lines if line and not line.startswith("#")]
    return {canonical(name): version for name, version in pairs}


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def test_the_constraints_pin_every_dependency_to_one_version():
    project_ = tomllib.loads((ROOT / "app" / "pyproject.toml").read_text())["project"]
    extras = project_["optional-dependencies"]
    wanted = [*project_["dependencies"], *extras["test"], *extras["google"]]
    names = {canonical(re.match(r"[\w.-]+", d).group()) for d in wanted}

    assert names <= pins().keys()
    assert all(re.fullmatch(r"[\w.+!]+", v) for v in pins().values())


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


def test_install_stops_over_an_install_from_before_the_rename(mac):
    program(mac, ())
    (mac["home"] / "FamilyBrief" / "app").mkdir(parents=True)

    result = install(mac)

    assert result.returncode != 0
    assert "Uninstall it with its own version first" in result.stdout
    assert not (mac["home"] / "ParentRecap").exists()


def test_both_commands_name_the_same_entry_point():
    scripts = tomllib.loads((ROOT / "app" / "pyproject.toml").read_text())["project"]["scripts"]
    assert scripts["parent-recap"] == scripts["family-brief"] == "family_brief.__main__:main"


# --- Parent Recap's own Python (ADR 0011) ---------------------------------------------------

def test_a_fresh_install_runs_parent_recap_on_the_pinned_python(mac):
    program(mac, ())

    result = install(mac)

    assert result.returncode == 0, result.stdout + result.stderr
    pinned = runtimes(mac) / "python-3.13.16-20261003"
    assert real_python(mac).is_relative_to(pinned)
    ran = subprocess.run([venv_python(mac).parent / "parent-recap"], capture_output=True, text=True)
    assert ran.stdout.strip() == f"runs on {real_python(mac)}", ran.stderr
    assert f"Real Python path (needed for the WhatsApp permission): {real_python(mac)}" in result.stdout
    assert "the Mac's own python3" not in result.stdout + result.stderr
    assert "grant" not in result.stdout.lower()  # setup asks for the permission the first time


@pytest.mark.parametrize("kind, arch", [("arm64", "aarch64"), ("intel", "x86_64"),
                                        ("rosetta", "aarch64")])
def test_picks_the_build_for_the_mac_s_chip_even_in_a_rosetta_terminal(mac, kind, arch):
    program(mac, ())
    a_mac(mac, kind)

    result = install(mac)

    assert result.returncode == 0, result.stdout + result.stderr
    assert downloads(mac) == ["https://github.com/astral-sh/python-build-standalone/releases/download/"
                              f"20261003/{build_name(*PINNED, arch)}"]


def test_running_the_install_again_reuses_the_python_already_there(mac):
    program(mac, ())
    assert install(mac).returncode == 0
    before = real_python(mac)

    result = install(mac)

    assert result.returncode == 0, result.stdout + result.stderr
    assert len(downloads(mac)) == 1
    assert real_python(mac) == before
    assert "grant" not in result.stdout.lower()


def test_a_venv_on_another_python_is_rebuilt_on_the_pinned_one(mac):
    # Today's installs: the venv sits on Homebrew's Python, here the one running the tests.
    program(mac, ())
    venv = mac["home"] / "ParentRecap" / "app" / ".venv"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", venv], check=True)
    old = Path(os.path.realpath(venv / "bin" / "python"))

    result = install(mac)

    assert result.returncode == 0, result.stdout + result.stderr
    assert real_python(mac).is_relative_to(runtimes(mac)) and real_python(mac) != old
    assert "grant" in result.stdout.lower() and str(real_python(mac)) in result.stdout
    assert "family_brief" in subprocess.run([venv_python(mac), "-m", "pip", "list"],
                                            capture_output=True, text=True).stdout.replace("-", "_")


def test_a_new_pinned_version_goes_into_a_new_folder_and_asks_to_grant_again(mac):
    program(mac, ())
    assert install(mac).returncode == 0
    old = real_python(mac)
    serve(mac, "3.13.17", "20261101")

    result = install(mac)

    assert result.returncode == 0, result.stdout + result.stderr
    assert sorted(p.name for p in runtimes(mac).iterdir()) == ["python-3.13.17-20261101"]
    assert real_python(mac).is_relative_to(runtimes(mac) / "python-3.13.17-20261101") and real_python(mac) != old
    shown = result.stdout
    assert "WhatsApp" in shown and "grant" in shown.lower() and "App Management" in shown
    assert str(real_python(mac)) in shown

    again = install(mac)
    assert again.returncode == 0 and "grant" not in again.stdout.lower()


@pytest.mark.parametrize("break_it, said", [(offline, "couldn't download"),
                                            (lambda m: serve(m, "3.13.17", "20261101", tamper=True),
                                             "checksum")])
def test_a_failed_download_leaves_the_existing_install_as_it_was(mac, break_it, said):
    program(mac, ())
    assert install(mac).returncode == 0
    before = {"python": real_python(mac), "runtimes": sorted(runtimes(mac).iterdir()),
              "version": (mac["home"] / "ParentRecap" / "app" / "VERSION").read_text()}
    serve(mac, "3.13.17", "20261101")
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


def test_a_failed_first_download_creates_nothing(mac):
    program(mac, ())
    offline(mac)

    result = install(mac)

    assert result.returncode != 0
    assert "couldn't download" in result.stdout
    assert not (mac["home"] / "ParentRecap").exists()
