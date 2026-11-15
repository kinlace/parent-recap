"""`app/windows_install.py`, the Windows install's steps once a Python is there, which `install.ps1`
hands over to (#990).

What only Windows has (robocopy, the venv's `Scripts\\python.exe`, the Start menu) runs for real in
CI's windows-install job. Here: that it runs on the standard library alone, as a file, with the
exit code and words `install.ps1` used; the pinned runtimes fetched by checksum from `file://`
stand-ins for the download hosts and unpacked with this system's `tar`; and the steps after the
venv against a log that records what would have run.
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import io
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
INSTALLER = ROOT / "app" / "windows_install.py"


def load():
    spec = importlib.util.spec_from_file_location("windows_install", INSTALLER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


wi = load()


# --- Run before anything of the release is installed --------------------------------------------

def test_it_imports_only_the_standard_library():
    tree = ast.parse(INSTALLER.read_text(encoding="utf-8"))
    imported = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import)
                for alias in node.names}
    imported |= {node.module.split(".")[0] for node in ast.walk(tree)
                 if isinstance(node, ast.ImportFrom) and node.module}

    assert imported - {"__future__"} <= sys.stdlib_module_names


@pytest.mark.posix
def test_run_as_a_file_off_windows_it_says_so_exits_with_1_and_makes_nothing(tmp_path):
    # -S as well as -I: no site-packages, so a package of the venv running the tests can't help it.
    home = tmp_path / "ParentRecap"
    done = subprocess.run([sys.executable, "-I", "-S", str(INSTALLER)], capture_output=True, text=True,
                          env={**os.environ, "PARENT_RECAP_HOME": str(home)})

    assert done.returncode == 1
    assert done.stdout == ("Parent Recap wasn't installed. This install line is for Windows.\n"
                           "   On a Mac, paste the line in the README into Terminal.\n")
    assert not home.exists()


def test_a_step_that_fails_says_what_to_do_in_install_ps1s_words_and_exits_with_1(tmp_path, capsys):
    log = wi.Log(tmp_path / "install.log")

    def fails():
        raise wi.Stop("Parent Recap couldn't write setup's record. The full log is at x.log")

    assert wi.finish(fails, log) == 1
    assert capsys.readouterr().out == ("Parent Recap wasn't installed. Parent Recap couldn't write setup's record. "
                                       "The full log is at x.log\n   " + wi.TRY_AGAIN + "\n")
    # Said in the log too, without a traceback: CI's install check takes one there as a step that
    # broke (#596).
    assert "Traceback" not in log.path.read_text(encoding="utf-8")
    assert "couldn't write setup's record" in log.path.read_text(encoding="utf-8")


def test_anything_else_that_stops_it_is_said_as_it_came_with_its_traceback_in_the_log(tmp_path, capsys):
    log = wi.Log(tmp_path / "install.log")

    def breaks():
        raise PermissionError(13, "Access is denied", "C:\\x")

    assert wi.finish(breaks, log) == 1
    assert capsys.readouterr().out.startswith("Parent Recap wasn't installed. [Errno 13] Access is denied")
    assert "Traceback" in log.path.read_text(encoding="utf-8")


def test_a_step_that_worked_exits_with_0(tmp_path):
    assert wi.finish(lambda: None, wi.Log(tmp_path / "install.log")) == 0


# --- The pinned runtimes, by checksum ------------------------------------------------------------

@pytest.fixture
def runtimes(tmp_path, monkeypatch) -> dict:
    """A stand-in runtime tarball as python-build-standalone lays one out (one top folder), served
    from a folder by file:// URL, with this system's tar in place of Windows' own."""
    monkeypatch.setattr(wi, "TAR", shutil.which("tar"))
    served = tmp_path / "served"
    served.mkdir()
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w:gz") as tar:
        exe = tarfile.TarInfo("python/python.exe")
        exe.size = 4
        tar.addfile(exe, io.BytesIO(b"MZ!!"))
    archive = served / "cpython.tar.gz"
    archive.write_bytes(data.getvalue())
    (tmp_path / "runtime").mkdir()
    return {"url": archive.as_uri(), "missing": (served / "nothing-here.tar.gz").as_uri(),
            "sha256": hashlib.sha256(data.getvalue()).hexdigest(), "runtime": tmp_path / "runtime",
            "log": wi.Log(tmp_path / "install.log")}


def install(r: dict, urls: list[str], sha256: str | None = None) -> Path:
    destination = r["runtime"] / "python-3.13.16-20261003"
    wi.install_runtime("Python", urls, sha256 or r["sha256"], destination, "python.exe", r["log"])
    return destination


def test_a_runtime_comes_from_the_first_host_that_serves_it_and_is_put_in_place(runtimes, capsys):
    destination = install(runtimes, [runtimes["missing"], runtimes["url"]])

    assert (destination / "python.exe").read_bytes() == b"MZ!!"
    assert sorted(p.name for p in runtimes["runtime"].iterdir()) == [destination.name]
    assert "Downloading Parent Recap's own Python..." in capsys.readouterr().out
    assert f"Downloading {runtimes['missing']} failed" in runtimes["log"].path.read_text(encoding="utf-8")


def test_a_runtime_whose_checksum_doesnt_match_isnt_used(runtimes):
    with pytest.raises(wi.Stop) as stopped:
        install(runtimes, [runtimes["url"]], sha256="0" * 64)

    assert str(stopped.value) == ("The Python Parent Recap downloaded isn't the one it expects (its checksum "
                                  "doesn't match), so it wasn't used.\n   " + wi.TRY_AGAIN)
    assert list(runtimes["runtime"].iterdir()) == []


def test_a_runtime_no_host_serves_says_to_check_the_pc_is_online_and_leaves_nothing(runtimes):
    with pytest.raises(wi.Stop, match=r"couldn't download its Python \(is this PC online\?\)"):
        install(runtimes, [runtimes["missing"]])

    assert list(runtimes["runtime"].iterdir()) == []


def test_a_run_cut_off_partway_is_picked_up_by_the_next(runtimes):
    # What a run cut off before the rename leaves, and a half runtime from before that rule.
    (runtimes["runtime"] / ".download" / "unpacked").mkdir(parents=True)
    (runtimes["runtime"] / "python-3.13.16-20261003" / "Lib").mkdir(parents=True)

    destination = install(runtimes, [runtimes["url"]])

    assert (destination / "python.exe").is_file()
    assert sorted(p.name for p in runtimes["runtime"].iterdir()) == [destination.name]


def test_a_runtime_in_place_is_kept_and_nothing_is_downloaded(runtimes, capsys):
    (runtimes["runtime"] / "python-3.13.16-20261003").mkdir()
    (runtimes["runtime"] / "python-3.13.16-20261003" / "python.exe").write_bytes(b"old")

    destination = install(runtimes, ["https://example.invalid/never"])

    assert (destination / "python.exe").read_bytes() == b"old"
    assert capsys.readouterr().out == ""


def test_it_fetches_the_pinned_node_and_python_from_the_hosts_install_sh_uses():
    sh = (ROOT / "install.sh").read_text(encoding="utf-8")

    assert all(release in sh for release in wi.PYTHON_RELEASES)
    assert all(release in sh for release in wi.NODE_RELEASES)


# --- The steps once the venv is there -------------------------------------------------------------

class Recorded(wi.Log):
    """The install log, with each program the install runs recorded instead of run. `fails` says
    which exit with 1, by a part of their command."""

    def __init__(self, path: Path, fails: tuple[str, ...] = ()):
        super().__init__(path)
        self.ran: list[list[str]] = []
        self.fails = fails

    def run(self, command: list[str]) -> int:
        self.ran.append(command)
        return 1 if any(part in command for part in self.fails) else 0


@pytest.fixture
def installed(tmp_path) -> wi.Install:
    """An install whose program and venv are in place."""
    plugin = tmp_path / "plugin"
    target = tmp_path / "ParentRecap"
    app = target / "app"
    (app / ".venv" / "Scripts").mkdir(parents=True)
    (app / "constraints.txt").write_text("pip==1 --hash=sha256:00\n")
    (app / "pyproject.toml").write_text("[project]\nname = 'family-brief'\n")
    (target / "runtime").mkdir()
    return wi.Install(plugin, target, Recorded(tmp_path / "install.log", fails=("wanted",)), downloaded=True)


def modules(log: Recorded) -> list[str]:
    return [c[c.index("-m") + 1] for c in log.ran if "-m" in c]


def test_with_the_packages_installed_for_the_same_files_no_package_is_installed(installed, capsys):
    # As install.ps1 wrote the note: Get-FileHash's capitals, PowerShell's False.
    app = installed.app
    note = (f"{hashlib.sha256((app / 'constraints.txt').read_bytes()).hexdigest().upper()} "
            f"{hashlib.sha256((app / 'pyproject.toml').read_bytes()).hexdigest().upper()} google=False")
    (app / ".venv" / "parent-recap-packages.txt").write_text(note + "\r\n", encoding="ascii")

    installed.install_packages()

    assert modules(installed.log) == ["family_brief.google_packages"]
    assert capsys.readouterr().out == "Parent Recap's Python packages are up to date.\n"


def test_setup_s_record_comes_first_then_the_command_the_wilma_cli_baileys_and_the_start_menu(installed):
    installed.run_program_steps()

    assert modules(installed.log) == ["family_brief.install_record", "family_brief.command_path",
                                      "family_brief.setup_wilma", "family_brief.baileys_install",
                                      "family_brief.program_entries"]
    record = installed.log.ran[0]
    # The copy of stable the pasted line downloaded is recorded, so uninstall removes it.
    assert record[record.index("plugin") + 1] == str(installed.plugin)
    # On the venv's python.exe, never pip's parent-recap.exe, which Defender can block (#591).
    assert {c[0] for c in installed.log.ran} == {str(installed.venv_python)}


def test_a_wilma_cli_or_baileys_tree_that_couldnt_be_updated_doesnt_fail_the_install(installed):
    installed.log.fails = ("family_brief.setup_wilma", "family_brief.baileys_install", "family_brief.program_entries")

    installed.run_program_steps()

    assert modules(installed.log)[-1] == "family_brief.program_entries"


def test_a_record_that_couldnt_be_written_fails_the_install_naming_the_log(installed):
    installed.log.fails = ("family_brief.install_record",)

    with pytest.raises(wi.Stop, match="couldn't write setup's record. The full log is at .*install.log"):
        installed.run_program_steps()


def test_runtimes_the_program_no_longer_runs_on_go_and_the_others_stay(installed):
    runtimes = installed.runtimes
    for name in ("python-3.12.1-20240101", "node-v20.0.0", installed.runtime.name, installed.node_runtime.name,
                 "wilma-something"):
        (runtimes / name).mkdir()

    installed.remove_old_runtimes()

    assert sorted(p.name for p in runtimes.iterdir()) == sorted([installed.runtime.name, installed.node_runtime.name,
                                                                 "wilma-something"])


def test_the_python_the_installer_runs_on_stays_when_the_release_pins_another(installed, monkeypatch):
    # As an update's may: started on the version before's Python, which Windows won't let go while
    # it runs. The next install, on the new one, removes it.
    running = installed.runtimes / "python-3.12.1-20240101"
    running.mkdir()
    monkeypatch.setattr(wi.sys, "base_prefix", str(running))

    installed.remove_old_runtimes()

    assert running.is_dir()
