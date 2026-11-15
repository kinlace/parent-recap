"""Installs or updates Parent Recap on a Windows PC from the release this file is in: the steps of
the Windows install once a Python is there (ADR 0019, ADR 0011).

`install.ps1`, the line a family pastes, does only what must happen before any Python is there
(the administrator and x64 checks, the download of the `stable` branch, the pinned Python by
checksum) and then runs this file on that Python, from the release it downloaded. So that an update
can run the same steps without starting a `.ps1` file, they are here, in Python (#990): the pinned
Node by checksum, the pinned Python too when the one this runs on isn't the release's, the program
copied into a folder of its own in %LOCALAPPDATA%\\ParentRecap\\versions beside the version that
runs, its venv, the packages by checksum, the switch of `app` to it (#991), setup's record, the
`parent-recap` command, the wilma CLI, the Baileys tree, the Linked Device on the new version, and
Parent Recap Settings and Check. Nothing needs an administrator or Developer Mode: `app` is a
junction.

Run by path and with -I, so the standard library only: nothing of this release is installed yet.

    python -I app\\windows_install.py [--log <file>] [--downloaded]

`--log` names the dated install log `install.ps1` started, which this appends to; without it, this
starts one of its own in the logs folder. `--downloaded` says the release is the copy of `stable`
the pasted line downloaded into %LOCALAPPDATA%\\ParentRecap\\plugin, which setup's record then
lists so uninstall removes it. Everything the programs it runs print goes to the log, not the
window. It exits with 0 when the install worked and 1 when it didn't, having said why and what the
family can do; one that didn't leaves `app` pointing at the version before. Safe to re-run: a run that stopped partway picks up from where it got to, and a run
with nothing new to do downloads and installs nothing. It never touches the config or the secrets.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import traceback
import urllib.request
from collections.abc import Callable
from pathlib import Path

# -- Parent Recap's own Python (ADR 0011)
# The python-build-standalone build install.sh pins, for Windows x64. install.ps1 pins the same
# one, which it downloads before this runs. To bump it, see CONTRIBUTING.md.
PYTHON_VERSION = "3.13.16"
PYTHON_BUILD = "20261003"
PYTHON_SHA256 = "5e100ee3d592ff500f4408a624f054d202e32d9dba8a12b2226bef81083fd778"
# Where the builds are, tried in turn, as in install.sh: Astral's mirror, which uv downloads from,
# then the project's GitHub releases (#843). The checksum decides what is used.
PYTHON_RELEASES = (
    "https://releases.astral.sh/github/python-build-standalone/releases/download",
    "https://github.com/astral-sh/python-build-standalone/releases/download",
)
PYTHON_FILE = f"cpython-{PYTHON_VERSION}+{PYTHON_BUILD}-x86_64-pc-windows-msvc-install_only.tar.gz"

# -- Parent Recap's own Node (ADR 0011)
# The nodejs.org LTS release install.sh pins, for Windows x64, which the wilma CLI and the Baileys
# tree run on (own_node.py). To bump it, see CONTRIBUTING.md.
NODE_VERSION = "24.21.0"
NODE_SHA256 = "158f7685b44de51f6c0df1d153526cbcd3e1bc739a8dfc607721cef75de9e541"
NODE_RELEASES = ("https://nodejs.org/dist",)
NODE_FILE = f"node-v{NODE_VERSION}-win-x64.zip"

TRY_AGAIN = "Run the install line again. If it fails again, tell the Parent Recap team."
SEND_LOG = "Run the install line again. If it fails again, send that log to the Parent Recap team."

# Windows' own tar, which unpacks .tar.gz and .zip alike: another one on PATH, such as Git's, may
# not read C:\ paths. And its own robocopy, wherever PATH leads.
SYSTEM32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
TAR = str(SYSTEM32 / "tar.exe")
ROBOCOPY = str(SYSTEM32 / "robocopy.exe")


class Stop(Exception):
    """A step that failed: what happened, and what the family can do about it."""

    def __init__(self, message: str, next_step: str = TRY_AGAIN):
        super().__init__(f"{message}\n   {next_step}")


def home() -> Path:
    """Parent Recap's folder, %LOCALAPPDATA%\\ParentRecap unless PARENT_RECAP_HOME says otherwise."""
    if os.environ.get("PARENT_RECAP_HOME"):
        return Path(os.environ["PARENT_RECAP_HOME"])
    return Path(os.environ["LOCALAPPDATA"]) / "ParentRecap"


def is_parent_recap(folder: Path) -> bool:
    """Whether `folder` is a copy of Parent Recap: its plugin manifest says so."""
    try:
        manifest = json.loads((folder / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return False
    return isinstance(manifest, dict) and manifest.get("name") == "parent-recap"


def same_path(a: str | Path, b: str | Path) -> bool:
    """As Windows compares paths: without case, and without a trailing backslash."""
    return os.path.normcase(str(a).rstrip("\\/")) == os.path.normcase(str(b).rstrip("\\/"))


def file_hash(path: Path) -> str:
    """A file's SHA-256 in capitals, as PowerShell's Get-FileHash gave it: the packages' note in the
    venv holds it, and stays the same as the one install.ps1 wrote."""
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest().upper()


class Log:
    """The dated install log: what the programs run print, and why a step failed."""

    def __init__(self, path: Path):
        self.path = path

    def write(self, text: str) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(text.rstrip("\n") + "\n")

    def run(self, command: list[str]) -> int:
        """Runs a program with its output going to the log, and returns its exit code. Piped, Python
        writes the PC's code page, which has no status line's emoji, so a step's last print would end
        it in a traceback (#596): the programs run here are told to write UTF-8."""
        env = {**os.environ, "PYTHONUTF8": "1"}
        with self.path.open("ab") as f:
            try:
                return subprocess.run(command, stdout=f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                      env=env).returncode
            except OSError as e:
                f.write(f"Couldn't start {command[0]}: {e}\n".encode())
                return 1

    def run_saying(self, command: list[str]) -> tuple[int, str]:
        """`run`, for a program whose output is meant for the family: returns its exit code and
        what it printed, which goes to the log too."""
        env = {**os.environ, "PYTHONUTF8": "1"}
        with self.path.open("ab") as f:
            try:
                r = subprocess.run(command, stdout=subprocess.PIPE, stderr=f, stdin=subprocess.DEVNULL, env=env)
            except OSError as e:
                f.write(f"Couldn't start {command[0]}: {e}\n".encode())
                return 1, ""
            said = r.stdout.decode("utf-8", "replace").strip()
            if said:
                f.write(f"{said}\n".encode())
        return r.returncode, said


def remove_folder(folder: Path) -> None:
    if folder.exists():
        shutil.rmtree(folder)


def download(what: str, urls: list[str], sha256: str, folder: Path, program: str, log: Log) -> Path:
    """Downloads the first of `urls` that serves the file into `folder`, checks it against `sha256`,
    and unpacks it into `folder`\\unpacked without its top folder, where `program` must then be.
    Returns that folder."""
    print(f"Downloading Parent Recap's own {what}...", flush=True)
    file = folder / urls[0].rsplit("/", 1)[-1]
    for url in urls:
        try:
            with urllib.request.urlopen(url, timeout=60) as response, file.open("wb") as out:
                shutil.copyfileobj(response, out)
            break
        except (OSError, ValueError) as e:
            log.write(f"Downloading {url} failed: {e}")
    else:
        raise Stop(f"Parent Recap couldn't download its {what} (is this PC online?).")
    if file_hash(file) != sha256.upper():
        raise Stop(f"The {what} Parent Recap downloaded isn't the one it expects (its checksum doesn't "
                   "match), so it wasn't used.")
    unpacked = folder / "unpacked"
    unpacked.mkdir()
    code = log.run([TAR, "-xf", str(file), "-C", str(unpacked), "--strip-components", "1"])
    if code != 0 or not (unpacked / program).is_file():
        raise Stop(f"Parent Recap couldn't unpack the {what} it downloaded.")
    return unpacked


def install_runtime(what: str, urls: list[str], sha256: str, destination: Path, program: str, log: Log) -> None:
    """Puts the pinned `what` in `destination` unless its `program` is there. It's downloaded and
    checked in a folder next to the runtimes, so the last step is a rename on the same disk, which
    can't leave half a runtime behind. A run that was cut off leaves only that folder, which the
    next run clears, and a runtime that is in place stays for the next run."""
    if (destination / program).is_file():
        return
    staging = destination.parent / ".download"
    remove_folder(staging)
    remove_folder(destination)
    staging.mkdir(parents=True)
    try:
        unpacked = download(what, urls, sha256, staging, program, log)
        try:
            unpacked.rename(destination)
        except OSError as e:
            log.write(f"Moving {unpacked} to {destination} failed: {e}")
            raise Stop(f"Parent Recap couldn't put the {what} it downloaded in place (is the disk full?).") from e
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def venv_home(app: Path) -> str:
    """The folder of the Python the venv in `app` was made with, from its pyvenv.cfg; empty without
    one."""
    try:
        lines = (app / ".venv" / "pyvenv.cfg").read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    for line in lines:
        key, sep, value = line.partition("=")
        if sep and key.strip() == "home":
            return value.strip().rstrip("\\")
    return ""


def is_link(path: Path) -> bool:
    """Whether `path` is a junction, as `app` is on Windows, or a symlink."""
    return path.is_junction() or path.is_symlink()


def linked_version(target: Path) -> Path | None:
    """The version's folder in `target`\\versions that `app` is a link to, or None when `app` isn't
    one, as in an install from before versions (family_brief/versions.py, which this can't import
    yet)."""
    app = target / "app"
    if not is_link(app):
        return None
    to = os.readlink(app)
    folder = target / "versions" / Path(to.removeprefix("\\\\?\\")).name
    return folder if folder.is_dir() else None


def version_of(folder: Path) -> str:
    try:
        return (folder / "VERSION").read_text(encoding="ascii", errors="replace").strip()
    except OSError:
        return ""


class Install:
    """One run of the install, from the release `plugin` into Parent Recap's folder `target`.

    Each release goes in a folder of its own, `versions\\<version>.<random>`, beside the version that
    runs, which goes on as it was; once it is installed, the new version's own `versions switch`
    makes `app` a junction to it, keeps the version before to switch back to, and takes over an
    install from before versions (ADR 0047, family_brief/versions.py). The same version installed
    again, as a run with nothing new to do or manage adding Google's packages, is installed again
    in its own folder, so nothing is downloaded or installed that is there."""

    def __init__(self, plugin: Path, target: Path, log: Log, downloaded: bool):
        self.plugin, self.target, self.log, self.downloaded = plugin, target, log, downloaded
        self.app = target / "app"
        self.versions = target / "versions"
        self.logs = target / "logs"
        self.runtimes = target / "runtime"
        self.runtime = self.runtimes / f"python-{PYTHON_VERSION}-{PYTHON_BUILD}"
        self.node_runtime = self.runtimes / f"node-v{NODE_VERSION}"
        self.folder = self.app  # the version's folder, once `place` has chosen it
        self.new = False  # whether that folder is this run's own, which it removes if it fails

    @property
    def venv_python(self) -> Path:
        return self.folder / ".venv" / "Scripts" / "python.exe"

    def run(self) -> None:
        if not (is_parent_recap(self.plugin) and (self.plugin / "app" / "pyproject.toml").is_file()):
            raise Stop(f"{self.plugin} isn't a release of Parent Recap, so nothing was installed from it.")
        # The Python this runs on is the release's when install.ps1 started it; one the release
        # moved on from, as an update's may be, is fetched here.
        install_runtime("Python", [f"{r}/{PYTHON_BUILD}/{PYTHON_FILE}" for r in PYTHON_RELEASES], PYTHON_SHA256,
                        self.runtime, "python.exe", self.log)
        install_runtime("Node", [f"{r}/v{NODE_VERSION}/{NODE_FILE}" for r in NODE_RELEASES], NODE_SHA256,
                        self.node_runtime, "node.exe", self.log)
        version = self.place()
        try:
            self.copy_program(version)
            self.make_venv()
            self.install_packages()
            self.switch()
        except BaseException:
            # An install that fails leaves nothing of itself, and `app` as it was. A switch that
            # failed once it pointed `app` here leaves it to the next switch to clear up.
            if self.new and linked_version(self.target) != self.folder:
                shutil.rmtree(self.folder, ignore_errors=True)
            raise
        self.run_program_steps()
        print(f"Parent Recap {version} is installed in {self.app}")
        # The window's PATH is the one it opened with, before the user's PATH had the folder.
        print(f"   Command: parent-recap in a new PowerShell window; in this one: "
              f"{self.target / 'bin' / 'parent-recap.cmd'}")

    def release_version(self) -> str:
        manifest = json.loads((self.plugin / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8-sig"))
        return str(manifest.get("version", "unknown"))

    def place(self) -> str:
        """Chooses the version's folder: the one `app` points at when it holds this version, else
        a new one of this run's own. Returns the version."""
        version = self.release_version()
        self.versions.mkdir(exist_ok=True)
        running = linked_version(self.target)
        if running is not None and version_of(running) == version:
            self.folder, self.new = running, False
        else:
            self.folder = Path(tempfile.mkdtemp(prefix=f"{version}.", dir=self.versions))
            self.new = True
        return version

    def copy_program(self, version: str) -> None:
        """Copies the program, without what belongs to this PC (the venv, Python's caches, VERSION
        and RUNTIMES) and without the tests, which don't go to families, then writes VERSION and
        RUNTIMES, the runtimes it runs on, which the switch keeps while it's kept."""
        source = self.plugin / "app"
        code = self.log.run([ROBOCOPY, str(source), str(self.folder), "/MIR",
                             "/XD", ".venv", "__pycache__", "*.egg-info", str(source / "tests"),
                             "/XF", "VERSION", "RUNTIMES",
                             "/NFL", "/NDL", "/NJH", "/NJS", "/NP"])
        # robocopy's 8 and above are failures; below, what it copied.
        if code >= 8:
            raise Stop(f"Parent Recap couldn't copy its program into {self.folder} (is the disk full?).")
        for name, text in (("VERSION", f"{version}\n"),
                           ("RUNTIMES", f"{self.runtime.name}\n{self.node_runtime.name}\n")):
            file = self.folder / name
            if not file.is_file() or file.read_text(encoding="ascii", errors="replace") != text:
                file.write_text(text, encoding="ascii", newline="\n")

    def make_venv(self) -> None:
        """A venv on another Python, such as an older pinned one, is made again."""
        if same_path(venv_home(self.folder), self.runtime) and self.venv_python.is_file():
            return
        remove_folder(self.folder / ".venv")
        if self.log.run([str(self.runtime / "python.exe"), "-m", "venv", str(self.folder / ".venv")]) != 0:
            raise Stop(f"Parent Recap couldn't set up its Python in {self.folder}. The full log is at {self.log.path}")

    def switch(self) -> None:
        """Points `app` at the version's folder with the new version's own `versions switch`, which
        says why when it can't, and then removes the versions and runtimes no kept version runs on.
        The Python this runs on, which an update's may be, stays while it runs: Windows doesn't let
        a running program's files be removed, and the next install removes it."""
        # What it prints says why it changed nothing, for the window as well as the log.
        code, said = self.log.run_saying([str(self.venv_python), "-m", "family_brief.versions", "switch",
                                          str(self.target), str(self.folder)])
        if code != 0:
            if said:
                raise Stop(said, "If it fails again, tell the Parent Recap team.")
            raise Stop(f"Parent Recap couldn't switch to the new version. The full log is at {self.log.path}",
                       SEND_LOG)

    def install_packages(self) -> None:
        """Prebuilt packages only, at the versions that were tested and checked against the checksums
        app\\constraints.txt holds (see CONTRIBUTING.md), as install.sh installs them: pip first, then
        what the program needs (app\\requirements.py names it), then the program itself from `app`,
        which pip can't check by checksum, with nothing of its own. What they were installed for is
        noted in the venv: the same again needs no pip, so a run with nothing new to do needs no
        network. The program's own code is installed in place (-e), so a new copy of it needs no pip
        either."""
        stamp = self.folder / ".venv" / "parent-recap-packages.txt"
        had = stamp.read_text(encoding="ascii", errors="replace").strip() if stamp.is_file() else ""
        packages = f"{file_hash(self.folder / 'constraints.txt')} {file_hash(self.folder / 'pyproject.toml')}"
        installed = False
        if not had.startswith(f"{packages} "):
            print("Installing Parent Recap's Python packages...", flush=True)
            self.pip("pip")
            self.install_required()
            self.pip_or_stop([str(self.venv_python), "-m", "pip", "install", "--disable-pip-version-check", "--no-deps",
                              "--no-build-isolation", "-e", str(self.folder)])
            installed = True
        # Google's packages only for a Household whose config uses Google Calendar, which the
        # program installed above reads: turning it on in manage runs the install again, and an
        # update keeps them.
        google = self.log.run([str(self.venv_python), "-m", "family_brief.google_packages", "wanted"]) == 0
        # "True" and "False" as PowerShell wrote them, so a note install.ps1 wrote still matches.
        wanted = f"{packages} google={google}"
        if google and (installed or had != wanted):
            if not installed:
                print("Installing Parent Recap's Python packages...", flush=True)
            self.install_required("google")
            installed = True
        if had != wanted:
            stamp.write_text(wanted + "\n", encoding="ascii")
        if not installed:
            print("Parent Recap's Python packages are up to date.", flush=True)

    def pip_failed(self) -> Stop:
        return Stop(f"Parent Recap couldn't install its Python packages. The full log is at {self.log.path}", SEND_LOG)

    def pip_or_stop(self, command: list[str]) -> None:
        if self.log.run(command) != 0:
            raise self.pip_failed()

    def pip(self, *names: str) -> None:
        """Installs `names` by checksum, prebuilt only, at the versions constraints.txt pins."""
        self.pip_or_stop([str(self.venv_python), "-m", "pip", "install", "--only-binary", ":all:",
                          "--disable-pip-version-check", "--require-hashes", "-c", str(self.folder / "constraints.txt"),
                          *names])

    def install_required(self, *extras: str) -> None:
        """The packages the program needs, with those of `extras`, by checksum."""
        with self.log.path.open("ab") as errors:
            asked = subprocess.run([str(self.venv_python), "-I", str(self.folder / "requirements.py"), *extras],
                                   stdout=subprocess.PIPE, stderr=errors, stdin=subprocess.DEVNULL)
        if asked.returncode != 0:
            raise self.pip_failed()
        names = asked.stdout.decode("utf-8", "replace").split()
        if names:
            self.pip(*names)

    def run_program_steps(self) -> None:
        """The program's own steps, through `app` on the venv's python.exe as install.sh runs them:
        setup's record, the command, the wilma CLI, the Baileys tree, the Linked Device on this
        version, and Parent Recap Settings and Check."""
        python = str(self.app / ".venv" / "Scripts" / "python.exe")
        record = [python, "-m", "family_brief.install_record", "program", str(self.app), "versions", str(self.versions),
                  "logs", str(self.logs), "runtime", str(self.runtimes)]
        if self.downloaded:
            record += ["plugin", str(self.plugin)]
        if self.log.run(record) != 0:
            raise Stop(f"Parent Recap couldn't write setup's record. The full log is at {self.log.path}")
        # `parent-recap` in a folder of its own, `target`\bin, on the user's PATH, so every new
        # PowerShell window runs it without its full path (#560).
        if self.log.run([python, "-m", "family_brief.command_path", "install", str(self.target)]) != 0:
            raise Stop(f"Parent Recap couldn't put its command on the PATH. The full log is at {self.log.path}")
        # A Household that connected Wilma gets the wilma CLI version this release pins (ADR 0008),
        # on the Node above. One that couldn't be updated keeps working, and the next install tries
        # again.
        self.log.run([python, "-m", "family_brief.setup_wilma", "update"])
        # The Baileys tree the Linked Device runs on (ADR 0025), as install.sh installs it.
        self.log.run([python, "-m", "family_brief.baileys_install", "install"])
        # A Linked Device running now is started again on this version's command and Node, which
        # may be new. It never fails the install.
        self.log.run([python, "-m", "family_brief.linked_device", "restart"])
        # Parent Recap Settings and Parent Recap Check in the Start menu (ADR 0042), as install.sh
        # puts them in ~/Applications: made again by every install, so an update brings them. It
        # never fails the install.
        self.log.run([python, "-m", "family_brief.program_entries", "install", str(self.target)])


def stopped(error: BaseException, log: Log | None) -> int:
    """Says in the window why the install stopped, in the words install.ps1 used, and what the family
    can do, and in the log; returns 1, the exit code of an install that didn't work. A step that
    failed says so itself; anything else is said as it came, with its traceback in the log."""
    said = str(error) if isinstance(error, Stop) else f"{error}\n   {TRY_AGAIN}"
    print(f"Parent Recap wasn't installed. {said}", flush=True)
    if log:
        try:
            log.write(f"Stopped: {said}" if isinstance(error, Stop) else "".join(traceback.format_exception(error)))
        except OSError:
            pass
    return 1


def finish(step: Callable[[], None], log: Log) -> int:
    """Runs `step`: 0 when it worked, and 1 when it didn't, having said why."""
    try:
        step()
    except Exception as e:  # noqa: BLE001 - whatever stopped it, the family is told
        return stopped(e, log)
    return 0


def start_log(target: Path, given: str | None) -> Log:
    """The log `install.ps1` started, or a dated one of this run's own."""
    if given:
        log = Log(Path(given))
    else:
        logs = target / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        log = Log(logs / f"install-{datetime.datetime.now():%Y-%m-%d-%H%M%S}.log")
        log.write(f"PC: {platform.machine()}, Windows {platform.version()}, Python {PYTHON_VERSION}, Node {NODE_VERSION}")
    log.write(f"Installer: {Path(__file__).resolve()} on {sys.executable} ({platform.python_version()}), "
              f"Node {NODE_VERSION}")
    return log


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="windows_install.py", description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--log", help="the install log to append to")
    parser.add_argument("--downloaded", action="store_true",
                        help="the release is the copy of stable the pasted line downloaded")
    args = parser.parse_args(argv)
    # Piped, as CI and an update run it, the window's code page may not have a path's letters.
    sys.stdout.reconfigure(errors="replace")
    if os.name != "nt":
        return stopped(Stop("This install line is for Windows.", "On a Mac, paste the line in the README into Terminal."),
                       None)
    target = home()
    try:
        for folder in (target, target / "logs", target / "runtime"):
            folder.mkdir(parents=True, exist_ok=True)
        log = start_log(target, args.log)
    except OSError as e:
        return stopped(e, None)
    return finish(Install(Path(__file__).resolve().parent.parent, target, log, args.downloaded).run, log)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
