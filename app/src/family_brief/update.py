"""`parent-recap update`: the program updates itself to the newest release (ADR 0047).

What it downloads, from where, how it is verified and what it never does:

- It reads the version the `stable` branch holds, and downloads that version's release zip and
  its signature, from this repository's releases on GitHub. The repository is REPOSITORY, a
  constant: nothing read from the network changes where it downloads from. Its requests go only
  to the hosts in HOSTS, all GitHub's for this repository, and carry nothing about the Household:
  no cookie, no sign-in, no name, no version of its own.
- It downloads and unpacks inside the program, without `curl`, `unzip` or a shell.
- Nothing it downloads is run before its signature verified (release_signature.py), and nothing
  is piped from the network into a shell. What runs is the verified release's own installer, its
  `install.sh`, run as a file.
- The version `stable` holds isn't signed. After verifying the zip it reads the version inside
  it: it must be the one asked for and higher than the installed one.
- It reads nothing from the maintainers' gateway, so nothing the gateway answers (to a Run
  Report, ADR 0048, or anything else) can start an update or choose a release.
- Nothing asks for admin rights: the installer is told it runs for an update
  (schedule_update.UPDATING), and leaves what would ask for a password as it is.
- It writes only to the places an install already writes: Parent Recap's folder (its `update/`
  folder for downloads and its state, `plugin/`, `versions/` and `app` through the installer,
  `logs/update.log`) and the files outside it that belong to the install, which
  docs/setup-internals.md lists.

Once installed, the new version's `doctor` runs. A failure it reports that the version before
didn't (its `doctor` runs first) switches back to that version (versions.py).

The verified installer makes requests of its own, to the hosts an install already uses (Python
packages, the Linked Device's npm tree, a new Python or Node). Those aren't this module's;
SECURITY.md is to list them (#919).

Only on a Mac, and only for a program run from an installed release: on the hosted version the
server is deployed by its maintainers (ADR 0045), and Windows is decided separately.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests

from . import install_record, own_node, private_files, release_signature, versions
from .schedule_update import UPDATING
from .system import profile

REPOSITORY = "kinlace/parent-recap"
# Every host the update asks, and the only ones a redirect may lead to: the `stable` branch's
# files, the releases page, and where GitHub keeps a release's files.
HOSTS = ("raw.githubusercontent.com", "github.com", "objects.githubusercontent.com",
         "release-assets.githubusercontent.com")
STABLE_VERSION = f"https://raw.githubusercontent.com/{REPOSITORY}/stable/.claude-plugin/plugin.json"
RELEASES = f"https://github.com/{REPOSITORY}/releases/download"

UPDATE = "update"           # in Parent Recap's folder: STATE and the downloads
STATE = "state.json"
DOWNLOAD = "download."      # a download's folder in UPDATE starts with this
FILES = (STATE, f".{STATE}.incoming")  # what else UPDATE holds
LOG = "update.log"          # in Parent Recap's logs folder
PLUGIN = "plugin"           # the plugin copy the install line puts in Parent Recap's folder
PREFIX = "parent-recap"     # the folder a release zip unpacks into (scripts/release.py)

VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
MAX_DOWNLOAD = 200 * 1024 * 1024  # a release zip is a few MB: more is not one
TIMEOUT = 60                      # seconds without an answer from GitHub
INSTALL_TIMEOUT = 60 * 60         # the installer builds a venv and may fetch a Python or Node
DOCTOR_TIMEOUT = 15 * 60
MAX_REDIRECTS = 5
DOCTOR_DIDNT_RUN = "doctor didn't run"  # the failing item when a version's doctor couldn't run
# The evening run installs a release only once this Mac has seen it on `stable` a day: time for
# the maintainers to stop a bad release before families take it (ADR 0047). It first sees it after
# an evening's Brief, so the next evening's look, which can come a little sooner after its time or
# across a change to summer time, counts as a day: it was out before this Mac first saw it.
EVENING_WAIT = timedelta(hours=24) - timedelta(hours=2)


class Refused(Exception):
    """A step that went wrong: the message says why, and nothing was installed."""


@dataclass(frozen=True)
class Outcome:
    """What `update` did. `result` is one of: not-applicable, up-to-date, waiting (a release not
    yet a day old, for the evening run), updated, switched-back, failed. `version` is the one it
    now runs, where it got that far."""
    result: str
    message: str
    version: str | None = None


# ---------------------------------------------------------------- versions

def parsed(version: str) -> tuple[int, int, int] | None:
    """A release's version as numbers, or None when it isn't X.Y.Z."""
    m = VERSION.match(version.strip())
    return (int(m[1]), int(m[2]), int(m[3])) if m else None


def newer(candidate: str, installed: str) -> bool:
    """Whether `candidate` is a release's version higher than `installed`."""
    new, old = parsed(candidate), parsed(installed)
    return new is not None and old is not None and new > old


def doctor_failures(output: str) -> set[str]:
    """The items `doctor` printed as failing (`❌ <item>: <detail>`), without their details, which
    may change from one run to the next."""
    failing = set()
    for line in output.splitlines():
        if line.startswith("❌"):
            failing.add(line.removeprefix("❌").strip().split(": ", 1)[0])
    return failing


# ---------------------------------------------------------------- state and log

def state(folder: Path | None = None) -> dict:
    """The update's state: `last_look` and `last_result` (when it last read `stable`'s version and
    what came of it), `failed` (a version whose install or check failed, or None) and `seen` (the
    version `stable` held when it changed and when this Mac first saw it there, or None), which
    the evening run's day's wait counts from. Kept in Parent Recap's folder outside the versions,
    so a switch and a switch back keep it."""
    path = (folder or own_node.home()) / UPDATE / STATE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    return {"last_look": None, "last_result": None, "failed": None, "seen": None,
            **(data if isinstance(data, dict) else {})}


def _save(folder: Path, **changes) -> None:
    """Records a look and what came of it: `last_look` is now, unless given."""
    data = {**state(folder), "last_look": _now(), **changes}
    (folder / UPDATE).mkdir(mode=0o700, parents=True, exist_ok=True)
    incoming = folder / UPDATE / f".{STATE}.incoming"  # renamed over it: never half written
    private_files.write(incoming, json.dumps(data, indent=2) + "\n")
    os.replace(incoming, folder / UPDATE / STATE)


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class _Log:
    """The update log in Parent Recap's logs folder: each step with its versions and time. It
    names versions, steps and the checks that failed, never what the Household's messages say."""

    def __init__(self, folder: Path) -> None:
        self.path = folder / "logs" / LOG

    def __call__(self, step: str, text: str) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(f"{_now()} {step}: {text}\n")

    def output(self, what: str, text: str) -> None:
        """A program's own output, such as the installer's, indented under its step."""
        if text.strip():
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(f"  {what}:\n" + "".join(f"    {line}\n" for line in text.splitlines()))


# ---------------------------------------------------------------- whether it applies

def not_applicable(config: str | None = None) -> str | None:
    """Why the update doesn't apply to this program, or None when it does. Asks no host."""
    system = profile.current().system
    if system == "windows":
        return ("Parent Recap doesn't update itself on Windows yet. To update it, paste the "
                "install line into PowerShell again.")
    if system != "macos" or _hosted(config):
        return ("This is the hosted version: the Parent Recap team updates the server, so there "
                "is nothing to update here.")
    folder = own_node.running_from()
    if folder is None:
        return ("This Parent Recap doesn't run from an installed release (it runs from a copy of "
                "its code), so it doesn't update itself.")
    if versions.current(folder) is None:
        return ("This Parent Recap was installed before it could update itself. Paste the install "
                "line into Terminal once; from then on it updates itself.")
    if parsed(versions.version(versions.current(folder))) is None:
        return ("The installed version can't be told, so Parent Recap doesn't update itself. Paste "
                "the install line into Terminal to update it.")
    return None


def _hosted(config: str | None) -> bool:
    from .config import Config
    try:
        return Config.load(config).hosted
    except Exception:  # noqa: BLE001 - no config yet: a family's own computer
        return False


# ---------------------------------------------------------------- the network

def _session() -> requests.Session:
    """A session that sends nothing of the Household's: no cookies, and no proxy settings or
    `.netrc` sign-in from the environment."""
    s = requests.Session()
    s.trust_env = False
    s.headers["User-Agent"] = "parent-recap-update"
    return s


def _get(session: requests.Session, url: str) -> requests.Response:
    """GETs `url`, following redirects only to HOSTS over HTTPS. Raises Refused otherwise."""
    for _ in range(MAX_REDIRECTS + 1):
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.hostname not in HOSTS:
            raise Refused(f"GitHub sent the download to {parts.hostname or url}, which isn't one "
                          "of the addresses Parent Recap downloads from")
        try:
            r = session.get(url, stream=True, allow_redirects=False, timeout=TIMEOUT)
        except requests.RequestException as e:
            raise Refused(f"GitHub couldn't be reached ({type(e).__name__}): is this Mac "
                          "online?") from e
        if r.is_redirect:
            url = urljoin(url, r.headers.get("location", ""))
            r.close()
            continue
        if r.status_code != 200:
            r.close()
            raise Refused(f"GitHub answered {r.status_code} for {parts.path}")
        return r
    raise Refused("GitHub redirected the download too many times")


def stable_version(session: requests.Session | None = None) -> str:
    """The version the `stable` branch holds. Raises Refused."""
    r = _get(session or _session(), STABLE_VERSION)
    try:
        version = json.loads(r.content)["version"]
    except (requests.RequestException, OSError, ValueError, KeyError, TypeError) as e:
        raise Refused("the version on stable couldn't be read") from e
    finally:
        r.close()
    if not isinstance(version, str) or parsed(version) is None:
        raise Refused("the version on stable isn't one Parent Recap knows how to read")
    return version


def _download(session: requests.Session, url: str, to: Path) -> None:
    """Downloads `url` into the file `to`. A download that fails or is cut off raises Refused,
    and its folder is removed by the caller."""
    r = _get(session, url)
    expected = r.headers.get("content-length")
    got = 0
    try:
        with to.open("wb") as f:
            for chunk in r.iter_content(64 * 1024):
                got += len(chunk)
                if got > MAX_DOWNLOAD:
                    raise Refused(f"{to.name} is far larger than a release")
                f.write(chunk)
    except (requests.RequestException, OSError) as e:
        raise Refused(f"the download of {to.name} was cut off") from e
    finally:
        r.close()
    if expected is not None and expected.isdigit() and int(expected) != got:
        raise Refused(f"the download of {to.name} was cut off")


# ---------------------------------------------------------------- unpacking

def _unpack(zip_path: Path, into: Path) -> Path:
    """Unpacks a verified release zip into `into`, inside the program, and returns its plugin
    folder. Each file must be in the zip's one folder."""
    root = into.resolve()
    try:
        with zipfile.ZipFile(zip_path) as z:
            for info in z.infolist():
                name = info.filename
                target = (root / name).resolve()
                if not name.startswith(f"{PREFIX}/") or not target.is_relative_to(root / PREFIX):
                    raise Refused(f"the release zip holds {name}, outside its folder")
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                if (info.external_attr >> 16) & 0o111:
                    target.chmod(0o700)
    except (zipfile.BadZipFile, OSError) as e:
        raise Refused("the release zip couldn't be unpacked") from e
    return root / PREFIX


def _zip_version(plugin: Path) -> str | None:
    try:
        data = json.loads((plugin / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        return data["version"] if isinstance(data.get("version"), str) else None
    except (OSError, ValueError, KeyError, AttributeError):
        return None


def _is_parent_recap(plugin: Path) -> bool:
    try:
        data = json.loads((plugin / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        return data.get("name") in ("parent-recap", "family-brief")
    except (OSError, ValueError, AttributeError):
        return False


# ---------------------------------------------------------------- the update

class _Updater:
    def __init__(self, folder: Path, config: str | None, log: _Log) -> None:
        self.folder, self.config, self.log = folder, config, log
        self.had_plugin = False
        self.moved = False  # whether `install` changed the plugin copy, which a restore undoes

    def program(self) -> list[str]:
        """The `parent-recap` `app` points at, with the config the update was given."""
        command = [str(self.folder / versions.APP / ".venv" / "bin" / "parent-recap")]
        return command + (["-c", self.config] if self.config else [])

    def doctor(self, version: str) -> set[str]:
        """The checks the version `app` points at fails, by its own `doctor`."""
        try:
            r = subprocess.run([*self.program(), "doctor"], stdin=subprocess.DEVNULL,
                               capture_output=True, text=True, timeout=DOCTOR_TIMEOUT)
            output = r.stdout
        except (OSError, subprocess.TimeoutExpired) as e:
            output = f"❌ {DOCTOR_DIDNT_RUN}: {type(e).__name__}"
        failing = doctor_failures(output)
        self.log("check", f"{version}'s doctor: "
                 + (f"{len(failing)} failing: {', '.join(sorted(failing))}" if failing else "no failures"))
        return failing

    def install(self, plugin: Path, version: str, spare: Path) -> bool:
        """Puts the release in `plugin/`, where the install line puts it, and runs its own
        installer, which installs it beside the running version and switches `app` to it. The
        plugin copy before goes in `spare` until the update is done. Returns whether `app` now
        points at `version`."""
        target = self.folder / PLUGIN
        if target.exists() and not _is_parent_recap(target):
            raise Refused(f"{target} holds something that isn't Parent Recap, so it was left as it is")
        self.had_plugin = target.exists()
        self.moved = True
        if self.had_plugin:
            os.rename(target, spare)
        os.rename(plugin, target)
        command = ["/bin/bash", str(target / "install.sh")]
        if install_record.entries("codex-skill"):
            command.append("--codex")  # the Codex skills come with the release, as the line does
        env = {**os.environ, "PARENT_RECAP_HOME": str(self.folder), UPDATING: "1"}
        self.log("install", f"{version}: running its install.sh")
        try:
            r = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                               env=env, timeout=INSTALL_TIMEOUT)
            self.log.output("install.sh", r.stdout + r.stderr)
            code = r.returncode
        except (OSError, subprocess.TimeoutExpired) as e:
            code = None
            self.log.output("install.sh", f"couldn't finish: {type(e).__name__}")
        now = versions.current(self.folder)
        switched = now is not None and versions.version(now) == version
        self.log("install", f"{version}: install.sh ended with {code}; app points at "
                 f"{versions.version(now) if now else 'nothing'}")
        return switched

    def restore_plugin(self, spare: Path) -> None:
        """Puts the plugin copy back as it was before `install`: the one before, or none.
        Nothing when `install` didn't change it."""
        if not self.moved:
            return
        target = self.folder / PLUGIN
        if spare.exists() or not self.had_plugin:
            shutil.rmtree(target, ignore_errors=True)
        if spare.exists():
            os.rename(spare, target)
        self.moved = False


def update(config: str | None = None, *, retry_failed: bool = True,
           wait: timedelta | None = None) -> Outcome:
    """Looks for a newer release on `stable` and installs it, checks it with `doctor` and switches
    back when it fails a check the version before didn't. With `retry_failed` False, a version
    that failed before isn't tried again; with `wait`, a version is installed only once this Mac
    has seen it on `stable` that long (the evening run's, `evening`). Never raises for what the
    network or the release did: the Outcome says it."""
    if (reason := not_applicable(config)) is not None:
        return Outcome("not-applicable", reason)
    folder = own_node.running_from()
    assert folder is not None
    log = _Log(folder)
    installed = versions.version(versions.current(folder))  # type: ignore[arg-type]
    session = _session()
    try:
        wanted = stable_version(session)
    except Refused as e:
        log("look", f"installed {installed}; stable couldn't be read: {e}")
        _save(folder, last_result=f"look failed: {e}")
        return Outcome("failed", f"Parent Recap couldn't look for a newer release: {e}. "
                       "Nothing was changed.", installed)
    log("look", f"installed {installed}, stable holds {wanted}")
    seen = _seen(folder, wanted)
    if not newer(wanted, installed):
        _save(folder, last_result=f"up to date at {installed}", seen=seen)
        return Outcome("up-to-date", f"Parent Recap is up to date: it runs version {installed}, "
                       "the newest release.", installed)
    if not retry_failed and state(folder)["failed"] == wanted:
        log("look", f"{wanted} failed before; not tried again")
        _save(folder, last_result=f"{wanted} failed before; not tried again", seen=seen)
        return Outcome("failed", f"Version {wanted} failed its check before, so it wasn't tried "
                       "again. Nothing was changed.", installed)
    if wait is not None:
        first = datetime.fromisoformat(seen["at"])
        if datetime.now(timezone.utc) - first < wait:
            log("look", f"{wanted} first seen {seen['at']}; installed on an evening a day later")
            _save(folder, last_result=f"{wanted} waits until it is a day old", seen=seen)
            return Outcome("waiting", f"Version {wanted} is installed once it has been out a "
                           "day.", installed)
    _save(folder, seen=seen)
    return _update_to(folder, config, log, session, installed, wanted)


def evening(config: str | None = None) -> Outcome:
    """The update the evening run starts once its Brief is sent, or it decided to send nothing
    (ADR 0047): `update`, for a release this Mac has seen on `stable` a day, and not for one that
    failed before. It runs in the background, so nothing it starts asks for a password
    (schedule_update.UPDATING), and inside the evening job, under its run lock, so the evening
    jobs aren't installed again: they start the program from the same path, which now points at
    the version switched to. The evening run catches and logs whatever it raises
    (run.run_unless_caught_up), so the run's result stays as it was."""
    return update(config, retry_failed=False, wait=EVENING_WAIT)


def _seen(folder: Path, version: str) -> dict:
    """`state`'s `seen` for `version`, on `stable` now: when this Mac first saw it there."""
    seen = state(folder)["seen"]
    if isinstance(seen, dict) and seen.get("version") == version and isinstance(seen.get("at"), str):
        try:
            if datetime.fromisoformat(seen["at"]).tzinfo is not None:
                return seen
        except ValueError:
            pass
    return {"version": version, "at": _now()}


def _update_to(folder: Path, config: str | None, log: _Log, session: requests.Session,
               installed: str, wanted: str) -> Outcome:
    (folder / UPDATE).mkdir(mode=0o700, parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=DOWNLOAD, dir=folder / UPDATE))
    updater = _Updater(folder, config, log)
    spare = work / "plugin-before"
    keep = False  # whether the plugin copy stays the new version's
    try:
        name = f"parent-recap-{wanted}.zip"
        zip_path, signature = work / name, work / f"{name}.sig"
        try:
            for file in (zip_path, signature):
                _download(session, f"{RELEASES}/v{wanted}/{file.name}", file)
        except Refused as e:
            log("download", f"{wanted}: failed: {e}")
            raise
        log("download", f"{wanted}: {name} ({zip_path.stat().st_size} bytes) and its signature")
        try:
            good = release_signature.verified(zip_path, signature)
        except (OSError, subprocess.TimeoutExpired) as e:
            log("verify", f"{wanted}: couldn't be checked: {type(e).__name__}")
            raise Refused("its signature couldn't be checked, so it was deleted") from e
        if not good:
            log("verify", f"{wanted}: the signature doesn't verify; deleted")
            raise Refused("its signature doesn't verify against Parent Recap's release keys, so "
                          "it isn't a release of Parent Recap's and was deleted")
        log("verify", f"{wanted}: the signature verifies")
        plugin = _unpack(zip_path, work / "unpacked")
        inside = _zip_version(plugin)
        if inside != wanted or not newer(inside, installed):
            log("verify", f"{wanted}: the zip holds version {inside}; refused and deleted")
            raise Refused(f"the release zip holds version {inside}, not {wanted}, a version "
                          f"higher than the installed {installed}, so it was deleted")
        log("verify", f"{wanted}: the zip holds version {inside}")
        before = updater.doctor(installed)
        if not updater.install(plugin, wanted, spare):
            updater.restore_plugin(spare)
            log("install", f"{wanted}: not installed; {installed} runs as it did")
            _save(folder, last_result=f"{wanted} didn't install", failed=wanted)
            return Outcome("failed", f"Version {wanted} couldn't be installed, so Parent Recap "
                           f"runs version {installed} as it did. The update log is "
                           f"{log.path}.", installed)
        log("switch", f"{installed} -> {wanted}")
        after = updater.doctor(wanted)
        # A doctor that didn't run is a failure even where the one before didn't run either.
        new = (after - before) | (after & {DOCTOR_DIDNT_RUN})
        if new:
            outcome = _switch_back(folder, log, updater, spare, installed, wanted, new)
            keep = outcome.version == wanted
            return outcome
        keep = True
        _save(folder, last_result=f"updated {installed} to {wanted}", failed=None)
        return Outcome("updated", f"✅ Parent Recap updated from version {installed} to "
                       f"{wanted}, and its checks pass as before.", wanted)
    except Refused as e:
        _save(folder, last_result=f"{wanted} refused: {e}")
        return Outcome("failed", f"Version {wanted} wasn't installed: {e}. Nothing was "
                       "changed.", installed)
    finally:
        if not keep:  # whatever ended the update, the plugin copy is the running version's
            updater.restore_plugin(spare)
        shutil.rmtree(work, ignore_errors=True)


def _switch_back(folder: Path, log: _Log, updater: _Updater, spare: Path, installed: str,
                 wanted: str, new: set[str]) -> Outcome:
    failing = ", ".join(sorted(new))
    try:
        with open(log.path, "a", encoding="utf-8") as out:
            running, _ = versions.switch_back_whole(folder, stdout=out, stderr=out)
    except versions.NoPrevious:  # install.sh keeps the version before, so only if it was removed
        log("switch", f"{wanted}: no previous version to switch back to")
        _save(folder, last_result=f"{wanted} failed its check; no version to switch back to",
              failed=wanted)
        return Outcome("failed", f"Version {wanted} was installed, but its check failed where "
                       f"version {installed} passed ({failing}), and there is no previous "
                       f"version to switch back to. The update log is {log.path}.", wanted)
    updater.restore_plugin(spare)
    log("switch", f"{wanted} -> {running}: back, as {wanted} fails {failing}")
    _save(folder, last_result=f"{wanted} failed its check; back on {running}",
          failed=wanted)
    return Outcome("switched-back", f"Version {wanted} was installed, but its check failed where "
                   f"version {installed} passed ({failing}), so Parent Recap switched back to "
                   f"version {running}. The update log is {log.path}.", running)


# ---------------------------------------------------------------- parent-recap update

def cmd_update(args: argparse.Namespace) -> int:
    outcome = update(args.config)
    print(outcome.message)
    return 1 if outcome.result in ("failed", "switched-back") else 0


def register(sub) -> None:
    p = sub.add_parser("update", help="Install the newest release, check it with doctor and "
                       "switch back if the check fails (on a Mac)")
    p.set_defaults(func=cmd_update)
