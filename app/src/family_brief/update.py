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
- In the background nothing asks for admin rights: the installer is told it runs for an update
  (schedule_update.UPDATING), and leaves what would ask for a password as it is.
- A release that needs the family at the computer (a password dialog, a new permission, a QR
  scan, a config or state the version before can't read) says so in `release.json` inside the
  zip, where the signature covers it: the lowest version that can take it in the background, and
  a sentence in each language on what the family will be asked. It is read from nowhere but the
  verified zip, and only the newest release's is read. The evening run never installs such a
  release on an older version: it downloads it once to read that, keeps in the state that it
  waits and what it asks, and doesn't download that version again. The family starts it, with
  `parent-recap update` or the setup page's Update now, and then its installer runs with its
  dialogs (without schedule_update.UPDATING).
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

How to turn it off: the config's `updates.enabled`, which the setup page's switch and
`parent-recap update off` save (`set_enabled`). It is on unless the family turned it off. Turned
off, the evening run still reads the version `stable` holds, that one request and no other, so
the next Brief can say once per release that a newer one is out; it downloads nothing.
`parent-recap update` and the setup page's Update now still install the newest release when the
family asks. Nothing is downloaded while it is off, so the program can't know whether that
release needs the family: the Brief says only that it is out. `status` is what the setup page, `doctor` and the report a family sends show of all
this, and it asks no host.
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
from typing import Callable
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
NEEDS_FAMILY = "release.json"  # in a release zip: whether it needs the family (scripts/release.py)

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
    yet a day old, for the evening run), needs-family (a release the evening run left for the
    family to start, as it needs them at the computer), available (a newer release the evening
    run left alone, as updates are off), updated, switched-back, failed. `version` is the one it
    now runs, where
    it got that far, and `tried` the release it downloaded to install, where it got that far."""
    result: str
    message: str
    version: str | None = None
    tried: str | None = None


@dataclass(frozen=True)
class BriefNote:
    """What the next Brief says at its top about the evening's update (#916): the version it
    `updated` to, with `first` when it's the first such line the Household gets, which also says
    that Parent Recap updates itself; the version whose update `failed`, with the one `running`
    as before; the newer version that is `available` while updates are off (#917); or the version
    that is `waiting` for the family to start it, with what it `asks` of them by language
    (#918)."""
    updated: str | None = None
    first: bool = False
    failed: str | None = None
    running: str | None = None
    available: str | None = None
    waiting: str | None = None
    asks: dict[str, str] | None = None

    def asked(self, language: str) -> str:
        """What the waiting release asks of the family, in `language` or else in English."""
        return asked(self.asks, language)

    def link(self) -> str:
        """What changed in the release it updated to: its CHANGELOG, newest release first."""
        return f"https://github.com/{REPOSITORY}/blob/v{self.updated}/CHANGELOG.md"


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
    the evening run's day's wait counts from. For the next Brief (BriefNote): `tell_updated` (the
    version an evening update installed, until a Brief says so), `tell_failed` (the version whose
    evening update failed and the one still running, until a Brief says so), `told_failed` (the
    last failed version a Brief was to say so of, so each is said once) and `told_updates_itself`
    (whether a Brief has said that Parent Recap updates itself), and with updates off
    `tell_available` (the newer version on `stable`, until a Brief says so) and `told_available`
    (the last version a Brief was to say so of, so each is said once). For a release that needs
    the family: `waiting` (its `version`, the lowest version that takes it in the background,
    `from`, and what it `asks` by language, as its verified zip said; or None), which keeps the
    evening run from downloading that version again, and `told_waiting` (the last version a Brief
    said is waiting, so each is said once). Kept in Parent Recap's folder outside the versions,
    so a switch and a switch back keep it."""
    path = (folder or own_node.home()) / UPDATE / STATE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    return {"last_look": None, "last_result": None, "failed": None, "seen": None,
            "tell_updated": None, "tell_failed": None, "told_failed": None,
            "told_updates_itself": False, "tell_available": None, "told_available": None,
            "waiting": None, "told_waiting": None,
            **(data if isinstance(data, dict) else {})}


def _save(folder: Path, **changes) -> None:
    """Records a look and what came of it: `last_look` is now, unless given."""
    _write_state(folder, {**state(folder), "last_look": _now(), **changes})


def _write_state(folder: Path, data: dict) -> None:
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


# ---------------------------------------------------------------- the setting and the state

def enabled(config: str | None = None) -> bool:
    """Whether the evening run installs a newer release by itself: the config's `updates.enabled`,
    on for every Household that hasn't turned it off, also one installed before the setting."""
    from .config import Config
    try:
        return Config.load(config).updates.enabled
    except Exception:  # noqa: BLE001 - no config yet, or one doctor names: nothing turned it off
        return True


def set_enabled(config: Path, on: bool) -> bool:
    """Turns the program's updates on or off in the config at `config`, as the setup page's
    switch and `parent-recap update on|off` do. Returns whether it was saved."""
    from . import setup_save
    return setup_save.save(config, {"updates": on})["result"] == "saved"


@dataclass(frozen=True)
class Status:
    """The update state, as the setup page, `doctor` and the report a family sends show it: the
    installed `version`; why the program doesn't update itself here (`unavailable`), or None
    where it does; whether updates are `enabled`; when it last looked for a newer release and
    what came of it; the `newer` version this computer has seen on `stable`, if any; and when
    that version is `waiting` for the family to start it, what it `asks` of them by language."""
    version: str
    unavailable: str | None = None
    enabled: bool = True
    last_look: str | None = None
    last_result: str | None = None
    newer: str | None = None
    waiting: str | None = None
    asks: dict[str, str] | None = None

    def line(self) -> str:
        """All of it on one line, in `doctor`'s words."""
        if self.unavailable is not None:
            return f"version {self.version}; updates aren't available here: {self.unavailable}"
        setting = ("updates are on (parent-recap update off turns them off)" if self.enabled else
                   "updates are off: the evening run still looks for a newer release and "
                   "downloads nothing (parent-recap update on turns them on)")
        looked = (f"last looked {self.last_look}: {self.last_result}" if self.last_look
                  else "hasn't looked for a newer release yet")
        if self.waiting is None:
            return f"version {self.version}; {setting}; {looked}"
        asks = asked(self.asks, "en")
        return (f"version {self.version}; {setting}; {looked}; an update is waiting: version "
                f"{self.waiting} needs you at the computer{f' ({asks})' if asks else ''}, so it "
                "isn't installed by itself (parent-recap update installs it)")


def status(config: str | None = None) -> Status:
    """The update state of this program, for the config at `config`. Asks no host."""
    if (reason := not_applicable(config)) is not None:
        return Status(_program_version(), unavailable=reason)
    folder = own_node.running_from()
    assert folder is not None
    installed = versions.version(versions.current(folder))  # type: ignore[arg-type]
    data = state(folder)
    seen = data["seen"]
    on_stable = seen.get("version") if isinstance(seen, dict) else None
    waiting = _waiting(data, installed)
    if waiting is not None and waiting["version"] != on_stable:
        waiting = None  # a newer release is out since: what that one asks isn't known yet
    return Status(installed, enabled=enabled(config), last_look=data["last_look"],
                  last_result=data["last_result"],
                  newer=on_stable if isinstance(on_stable, str) and newer(on_stable, installed) else None,
                  waiting=waiting["version"] if waiting else None,
                  asks=waiting["asks"] if waiting else None)


def _program_version() -> str:
    """The running program's version, where it isn't an installed release's."""
    from . import failure
    return failure.about()["version"]


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


# ---------------------------------------------------------------- a release that needs the family

@dataclass(frozen=True)
class _Needs:
    """What a verified release zip says of needing the family: the lowest version that can take
    it in the background (`background_from`), or None when every version can, and what the
    family will be asked, by language."""
    background_from: str | None = None
    asks: dict[str, str] | None = None

    def family(self, installed: str) -> bool:
        """Whether a program at `installed` must wait for the family to take this release."""
        return self.background_from is not None and newer(self.background_from, installed)


def _needs(plugin: Path) -> _Needs:
    """What the unpacked, verified release in `plugin` says of needing the family. A release
    from before the file says nothing. One that can't be read raises Refused: scripts/release.py
    builds none, and what it would ask is unknown."""
    path = plugin / NEEDS_FAMILY
    if not path.exists():
        return _Needs()
    unread = Refused(f"its {NEEDS_FAMILY}, which says whether it needs you at the computer, "
                     "can't be read")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise unread from e
    if not isinstance(data, dict):
        raise unread
    value = data.get("background_from")
    if value is None:
        return _Needs()
    if not isinstance(value, str) or parsed(value) is None:
        raise unread
    return _Needs(value.strip(), _asks(data.get("asks")))


def _asks(asks: object) -> dict[str, str]:
    """The sentences of `asks` that are text, by language."""
    if not isinstance(asks, dict):
        return {}
    return {language: text.strip() for language, text in asks.items()
            if isinstance(language, str) and isinstance(text, str) and text.strip()}


def asked(asks: dict[str, str] | None, language: str) -> str:
    """What a release asks of the family, in `language` or else in English; empty when it didn't
    say."""
    return (asks or {}).get(language) or (asks or {}).get("en") or ""


def _waiting(data: dict, installed: str | None) -> dict | None:
    """`state`'s `waiting`, the release that waits for the family, while it still does: it is
    whole, and the `installed` version is lower than the one that takes it in the background
    (`from`). Once the family brought the program to that version or above, by this release or
    another, nothing waits."""
    waiting = data["waiting"]
    if not (isinstance(waiting, dict) and isinstance(waiting.get("version"), str)
            and isinstance(waiting.get("from"), str) and installed is not None
            and newer(waiting["version"], installed) and newer(waiting["from"], installed)):
        return None
    return {"version": waiting["version"], "asks": _asks(waiting.get("asks"))}


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

    def install(self, plugin: Path, version: str, spare: Path, dialogs: bool = False) -> bool:
        """Puts the release in `plugin/`, where the install line puts it, and runs its own
        installer, which installs it beside the running version and switches `app` to it. The
        plugin copy before goes in `spare` until the update is done. The installer is told it
        runs for an update, so nothing asks for a password, unless `dialogs`: a release that
        needs the family, which they started. Returns whether `app` now points at `version`."""
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
        env = {**os.environ, "PARENT_RECAP_HOME": str(self.folder)}
        if dialogs:
            env.pop(UPDATING, None)
        else:
            env[UPDATING] = "1"
        self.log("install", f"{version}: running its install.sh"
                 + (", with its dialogs, as the family started it" if dialogs else ""))
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
           wait: timedelta | None = None, by_family: bool = True,
           say: Callable[[str], None] | None = None) -> Outcome:
    """Looks for a newer release on `stable` and installs it, checks it with `doctor` and switches
    back when it fails a check the version before didn't. With `retry_failed` False, a version
    that failed before isn't tried again; with `wait`, a version is installed only once this Mac
    has seen it on `stable` that long (the evening run's, `evening`). With `by_family` False, as
    in the evening run, a release that needs the family isn't installed: it is downloaded once,
    and kept in the state as waiting. Started by the family, it installs with its dialogs, and
    `say` is first told what it will ask. Never raises for what the network or the release did:
    the Outcome says it."""
    if (reason := not_applicable(config)) is not None:
        return Outcome("not-applicable", reason)
    folder = own_node.running_from()
    assert folder is not None
    log = _Log(folder)
    installed = versions.version(versions.current(folder))  # type: ignore[arg-type]
    session = _session()
    wanted = _look(folder, log, session, installed)
    if isinstance(wanted, Outcome):
        return wanted
    seen = _seen(folder, wanted)
    # Before a version that failed: one the family started and that switched back still waits.
    waiting = _waiting(state(folder), installed)
    if not by_family and waiting is not None and waiting["version"] == wanted:
        log("look", f"{wanted} waits for the family; not downloaded again")
        _save(folder, last_result=f"{wanted} waits for the family to start it", seen=seen)
        return _left_for_the_family(installed, wanted)
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
    return _update_to(folder, config, log, session, installed, wanted, by_family, say)


def _left_for_the_family(installed: str, wanted: str) -> Outcome:
    """What the evening run's update comes to for a release that needs the family: nothing was
    installed, and nothing failed."""
    return Outcome("needs-family", f"Version {wanted} needs you at the computer, so it wasn't "
                   "installed by itself: parent-recap update installs it.", installed)


def _look(folder: Path, log: _Log, session: requests.Session, installed: str) -> str | Outcome:
    """The version `stable` holds, when it is higher than the `installed` one. Otherwise what
    came of the look, which is recorded: it couldn't be read, or nothing newer is out."""
    try:
        wanted = stable_version(session)
    except Refused as e:
        log("look", f"installed {installed}; stable couldn't be read: {e}")
        _save(folder, last_result=f"look failed: {e}")
        return Outcome("failed", f"Parent Recap couldn't look for a newer release: {e}. "
                       "Nothing was changed.", installed)
    log("look", f"installed {installed}, stable holds {wanted}")
    if not newer(wanted, installed):
        _save(folder, last_result=f"up to date at {installed}", seen=_seen(folder, wanted))
        return Outcome("up-to-date", f"Parent Recap is up to date: it runs version {installed}, "
                       "the newest release.", installed)
    return wanted


def look(config: str | None = None) -> Outcome:
    """The evening run's look with updates turned off: it reads the version `stable` holds, that
    one request, and downloads nothing. A newer version is kept for the next Brief to say is
    out, once per release (BriefNote), and for the setup page to offer."""
    if (reason := not_applicable(config)) is not None:
        return Outcome("not-applicable", reason)
    folder = own_node.running_from()
    assert folder is not None
    log = _Log(folder)
    installed = versions.version(versions.current(folder))  # type: ignore[arg-type]
    wanted = _look(folder, log, _session(), installed)
    if isinstance(wanted, Outcome):
        return wanted
    log("look", f"{wanted} is out; updates are off, so nothing was downloaded")
    tell = {} if state(folder)["told_available"] == wanted else {"tell_available": wanted}
    _save(folder, last_result=f"{wanted} is out; not installed, as updates are off",
          seen=_seen(folder, wanted), **tell)
    return Outcome("available", f"Version {wanted} is out. Updates are off, so it wasn't "
                   "installed: parent-recap update installs it.", installed)


def evening(config: str | None = None) -> Outcome:
    """The update the evening run starts once its Brief is sent, or it decided to send nothing
    (ADR 0047): `update`, for a release this Mac has seen on `stable` a day, and not for one that
    failed before. It runs in the background, so nothing it starts asks for a password
    (schedule_update.UPDATING), and inside the evening job, under its run lock, so the evening
    jobs aren't installed again: they start the program from the same path, which now points at
    the version switched to. The evening run catches and logs whatever it raises
    (run.run_unless_caught_up), so the run's result stays as it was. What came of it is kept for
    the next Brief to say (BriefNote). With updates turned off it only looks (`look`)."""
    if not enabled(config):
        return look(config)
    outcome = update(config, retry_failed=False, wait=EVENING_WAIT, by_family=False)
    if (folder := own_node.running_from()) is not None and outcome.tried is not None:
        _tell(folder, outcome)
    return outcome


def _tell(folder: Path, outcome: Outcome) -> None:
    """Keeps for the next Brief that the evening's update installed `outcome.tried`, or that it
    failed and the version before still runs. A failure is said once per version; one where the
    failed version still runs, as no version was left to switch back to, isn't said."""
    data = state(folder)
    if outcome.result == "updated":
        data.update(tell_updated=outcome.tried, tell_failed=None)
    elif outcome.version != outcome.tried and data["told_failed"] != outcome.tried:
        data.update(tell_failed={"version": outcome.tried, "running": outcome.version},
                    told_failed=outcome.tried)
    else:
        return
    _write_state(folder, data)


def brief_note(folder: Path, updates_on: bool = True) -> BriefNote | None:
    """What the next Brief says about the evening's update, or None. An `updated` line is dropped
    once that version no longer runs, as after `parent-recap switch-back`. That a newer release
    is `available` is said only while updates are off (`updates_on` False) and the running
    version is still older; that one is `waiting` for the family only while they are on, the
    running version is still older, and no Brief has said so of that version."""
    data = state(folder)
    updated, failed, available = data["tell_updated"], data["tell_failed"], data["tell_available"]
    current = versions.current(folder)
    running = versions.version(current) if current is not None else None
    if not isinstance(updated, str) or running != updated:
        updated = None
    if not (isinstance(failed, dict) and isinstance(failed.get("version"), str)
            and isinstance(failed.get("running"), str)):
        failed = None
    if updates_on or not isinstance(available, str) or running is None or not newer(available, running):
        available = None
    waiting = _waiting(data, running) if updates_on else None
    if waiting is not None and data["told_waiting"] == waiting["version"]:
        waiting = None
    if updated is None and failed is None and available is None and waiting is None:
        return None
    return BriefNote(updated=updated, first=updated is not None and not data["told_updates_itself"],
                     failed=failed["version"] if failed else None,
                     running=failed["running"] if failed else None, available=available,
                     waiting=waiting["version"] if waiting else None,
                     asks=waiting["asks"] if waiting else None)


def told(folder: Path, note: BriefNote | None) -> None:
    """Notes that a Brief went out saying `note` (brief_note's), so the next one doesn't say it
    again. An `updated` line it left out, as that version no longer runs, is dropped too, so a
    switch forward by hand later isn't said to be an update."""
    data = state(folder)
    before = dict(data)
    data.update(tell_updated=None)
    if note is not None and note.updated is not None:
        data.update(told_updates_itself=True)
    if note is not None and note.failed is not None:
        data.update(tell_failed=None)
    if note is not None and note.available is not None:
        data.update(tell_available=None, told_available=note.available)
    if note is not None and note.waiting is not None:
        data.update(told_waiting=note.waiting)
    if data != before:
        _write_state(folder, data)


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
               installed: str, wanted: str, by_family: bool = True,
               say: Callable[[str], None] | None = None) -> Outcome:
    (folder / UPDATE).mkdir(mode=0o700, parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=DOWNLOAD, dir=folder / UPDATE))
    updater = _Updater(folder, config, log)
    spare = work / "plugin-before"
    keep = False  # whether the plugin copy stays the new version's
    tried = None  # `wanted`, once it is downloaded
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
        tried = wanted
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
        needs = _needs(plugin)
        dialogs = needs.family(installed)
        waits = {"version": wanted, "from": needs.background_from, "asks": needs.asks}
        if dialogs and not by_family:
            log("verify", f"{wanted}: needs the family below {needs.background_from}; left for "
                "them to start")
            _save(folder, last_result=f"{wanted} waits for the family to start it", waiting=waits)
            return _left_for_the_family(installed, wanted)
        if dialogs:
            log("verify", f"{wanted}: needs the family below {needs.background_from}; they "
                "started it")
            # Kept also when they start it before any evening read it: if it fails or switches
            # back, it still waits for them, and the evening run doesn't download it to find out.
            _save(folder, waiting=waits)
            if say is not None:
                say(f"Version {wanted} needs you at the computer. "
                    f"{asked(needs.asks, 'en')}".rstrip())
        before = updater.doctor(installed)
        if not updater.install(plugin, wanted, spare, dialogs):
            updater.restore_plugin(spare)
            log("install", f"{wanted}: not installed; {installed} runs as it did")
            _save(folder, last_result=f"{wanted} didn't install", failed=wanted)
            return Outcome("failed", f"Version {wanted} couldn't be installed, so Parent Recap "
                           f"runs version {installed} as it did. The update log is "
                           f"{log.path}.", installed, tried=wanted)
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
                       f"{wanted}, and its checks pass as before.", wanted, tried=wanted)
    except Refused as e:
        _save(folder, last_result=f"{wanted} refused: {e}")
        return Outcome("failed", f"Version {wanted} wasn't installed: {e}. Nothing was "
                       "changed.", installed, tried=tried)
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
                       f"version to switch back to. The update log is {log.path}.", wanted, tried=wanted)
    updater.restore_plugin(spare)
    log("switch", f"{wanted} -> {running}: back, as {wanted} fails {failing}")
    _save(folder, last_result=f"{wanted} failed its check; back on {running}",
          failed=wanted)
    return Outcome("switched-back", f"Version {wanted} was installed, but its check failed where "
                   f"version {installed} passed ({failing}), so Parent Recap switched back to "
                   f"version {running}. The update log is {log.path}.", running, tried=wanted)


# ---------------------------------------------------------------- parent-recap update

ON = ("Updates are on: Parent Recap installs each new release by itself after the evening Brief. "
      "parent-recap update off turns that off.")
OFF = ("Updates are off: Parent Recap still looks once each evening whether a newer release is "
       "out, says so in the Brief, and downloads nothing. parent-recap update installs the newest "
       "release when you choose, and parent-recap update on turns updates on again.")


def cmd_update(args: argparse.Namespace) -> int:
    if args.setting is not None:
        return _cmd_setting(args.config, args.setting == "on")
    outcome = update(args.config, say=lambda text: print(text, flush=True))
    print(outcome.message)
    return 1 if outcome.result in ("failed", "switched-back") else 0


def _cmd_setting(config: str | None, on: bool) -> int:
    """`parent-recap update on|off`: saves the setting and says which it now is. Where the
    program doesn't update itself, it says why and saves nothing."""
    if (reason := not_applicable(config)) is not None:
        print(reason)
        return 0
    path = Path(os.path.expanduser(os.path.expandvars(config or "~/.family/config.yaml")))
    if not set_enabled(path, on):
        print(f"The setting couldn't be saved in {path}, so updates are still "
              f"{'on' if enabled(config) else 'off'}. Run parent-recap doctor and fix what it names.")
        return 1
    print(ON if on else OFF)
    return 0


def register(sub) -> None:
    p = sub.add_parser("update", help="Install the newest release, check it with doctor and "
                       "switch back if the check fails (on a Mac); with on or off, turn the "
                       "evening's own update on or off")
    p.add_argument("setting", nargs="?", choices=["on", "off"],
                   help="Turn updating by itself after the evening Brief on or off, instead of "
                        "updating now")
    p.set_defaults(func=cmd_update)
