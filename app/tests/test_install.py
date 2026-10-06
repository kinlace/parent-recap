"""`install.sh` installs only prebuilt packages, at the versions in the shipped constraints file.

Runs the real `install.sh` and the real pip, offline: pip reads packages only from a folder made
here (`fake_packages.py`), and the program it installs is a stand-in with the real one's layout.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from fake_packages import project, sdist, wheel

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def mac(tmp_path: Path) -> dict:
    packages = tmp_path / "packages"
    packages.mkdir()
    plugin = tmp_path / "plugin"
    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "parent-recap", "version": "9.9.9"}))
    shutil.copy(ROOT / "install.sh", plugin / "install.sh")
    return {"tmp": tmp_path, "packages": packages, "plugin": plugin, "home": tmp_path / "home"}


def program(mac: dict, requires: tuple[str, ...], constraints: str = "",
            google: tuple[str, ...] = ()) -> None:
    """A stand-in for app/: a `family-brief` that needs `requires`, and `google` in its google
    extra, with its constraints file."""
    app = project(mac["plugin"] / "app", "family-brief", "9.9.9", requires, extras={"google": list(google)})
    (app / "family_brief").mkdir()
    (app / "family_brief" / "__init__.py").write_text("")
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
    path = f"{path_first}:{os.environ['PATH']}" if path_first else os.environ["PATH"]
    env = {**os.environ, "PATH": path, "HOME": str(mac["home"]), "PARENT_RECAP_HOME": str(mac["home"] / "ParentRecap"),
           "PIP_NO_INDEX": "1", "PIP_FIND_LINKS": str(mac["packages"]),
           "PIP_DISABLE_PIP_VERSION_CHECK": "1", "PIP_NO_CACHE_DIR": "1"}
    env.pop("PIP_CONSTRAINT", None)
    return subprocess.run(["/bin/bash", str(mac["plugin"] / "install.sh")], capture_output=True, text=True,
                          env=env, timeout=300)


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
    machine = subprocess.run(["uname", "-m"], capture_output=True, text=True).stdout.strip()
    macos = subprocess.run(["sw_vers", "-productVersion"], capture_output=True, text=True).stdout.strip()
    python = subprocess.run([venv / "python", "-c", "import platform; print(platform.python_version())"],
                            capture_output=True, text=True).stdout.strip()
    cc = shutil.which("cc") or "none"
    env = re.fullmatch(r"Mac: (\S+), macOS (\S+), Python (\S+) \((\S+)\), pip (\S+), cc (\S+)", first)
    assert env, first
    assert env.groups() == (machine, macos, env[3], python, pip_version, cc)
    assert os.path.realpath(env[3]) == os.path.realpath(venv / "python")
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
