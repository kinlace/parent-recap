"""`parent-recap update`: the program updates itself to the newest release (ADR 0047).

What it downloads, from where, how it is verified and what it never does:

- It reads `release.json` on the `stable` branch, which says the version `stable` holds and the
  last version that changed each part of it (ADR 0051), and downloads that version's release zip
  and its signature, from this repository's releases on GitHub. The repository is REPOSITORY, a
  constant: nothing read from the network changes where it downloads from. Its requests go only
  to the hosts in HOSTS, all GitHub's for this repository, and carry nothing about the Household:
  no cookie, no sign-in, no name, no version of its own.
- It downloads and unpacks inside the program, without `curl`, `unzip` or a shell.
- Nothing it downloads is run before its signature verified (release_signature.py), and nothing
  is piped from the network into a shell. What runs is the verified release's own installer, run
  as a file: on a Mac its `install.sh`, with `/bin/bash`; on Windows its `app/windows_install.py`,
  with the installed version's Python (ADR 0052), so no `powershell.exe` and no `.ps1` file.
- The file `stable` holds isn't signed. After verifying the zip it reads the version inside
  it: it must be the one asked for and higher than the installed one.
- A release that changes nothing the Household uses isn't installed, and nothing of it is
  downloaded (ADR 0051): one whose core, and the part of each Source the Household has connected,
  was last changed in a version no higher than the installed one. A Source whose code has no
  folder of its own has no part: its changes are the core's. A file without `parts`, or without
  the core's or a connected Source's, counts as changed, as does a Household whose config can't
  be read. So the unsigned file can make a computer skip a release, which holding `stable` back
  already can, or download one, whose signature is then checked as before.
- It reads nothing from the maintainers' gateway, so nothing the gateway answers (to a Run
  Report, ADR 0048, or anything else) can start an update or choose a release.
- In the background the wake's password dialog never starts: the installer is told it runs for
  an update (schedule_update.UPDATING), and leaves the wake, which would ask for the Mac
  password, as it is. That is the one dialog this code stops; a release that would open any
  other is covered by the next point.
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
didn't (its `doctor` runs first) switches back to that version (versions.py). So does an update
that didn't finish: an installer that ends with a non-zero exit status, or is ended at its time
limit, once it has switched, and a `doctor` that ends with a non-zero exit status without
printing a failing check, as one that crashes does.

The verified installer makes requests of its own, to the hosts an install already uses (Python
packages, the Linked Device's npm tree, a new Python or Node, the wilma CLI). Those aren't this
module's; SECURITY.md, "How Parent Recap updates itself", lists them and how each is checked.

One update runs at a time, and never beside a run (#977): two at once would both move the plugin
copy, and one could remove the version the other is installing. The evening's update runs inside
the evening job, under its run lock (run_lock.py). The two the family starts, `parent-recap
update` and the setup page's Update now, take that lock themselves before the look and hold it
until the update ended, a switch back included (`alone`). While a run or another update holds it
they say Parent Recap is busy and change nothing, and a run that starts during one stops as it
does beside another run.

A Household that skipped releases may hold an old copy of a Source it never connected. So adding
a Source, with its `setup` step in a terminal or on the setup page, first reads the file `stable`
holds, that one request, and when the Source's part changed in a version higher than the
installed one, installs the newest release as an update the family started does, before anything
is typed or sent to the Source (`needed_to_add`, `before_adding`). The step then goes on in the
new version: a terminal's step is run again with it (`run_again`), and the setup page starts its
program again (setup_server). An update that can't be done stops the step, so a family never
signs in to a Source with code older than the newest. A Source without a part, and a program
that doesn't update itself, go on at once and ask no host.

On a Mac and on a Windows PC (ADR 0052), only for a program run from an installed release, and
only on a computer with its own `ssh-keygen` to check a signature: on the hosted version the server
is deployed by its maintainers (ADR 0045). On Windows `app` is a junction, which isn't replaced in
one step, so the switch and the switch back happen under the run lock the update holds, with the
Linked Device's task stopped before them and started after (versions.py). A step the system stops,
as a company's Defender rule may (the download, the unpacking, the link, the installer, `doctor`),
is named in the update log with the system's error word for word, and leaves the installed version
running and the scheduled tasks as they were.

How to turn it off: the config's `updates.enabled`, which the setup page's switch and
`parent-recap update off` save (`set_enabled`). It is on unless the family turned it off. Turned
off, the evening run still reads the file `stable` holds, that one request and no other, so
the next Brief can say once per release that a newer one is out, when it changes something the
Household uses; it downloads nothing.
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
import sys
import tempfile
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Iterator
from urllib.parse import urljoin, urlsplit

import requests

from . import install_record, own_node, private_files, release_signature, run_lock, versions
from .schedule_update import HOLDS_RUN_LOCK, UPDATING
from .system import profile

if TYPE_CHECKING:
    from .config import Config

REPOSITORY = "kinlace/parent-recap"
# Every host the update asks, and the only ones a redirect may lead to: the `stable` branch's
# files, the releases page, and where GitHub keeps a release's files.
HOSTS = ("raw.githubusercontent.com", "github.com", "objects.githubusercontent.com",
         "release-assets.githubusercontent.com")
STABLE_RELEASE = f"https://raw.githubusercontent.com/{REPOSITORY}/stable/release.json"
RELEASES = f"https://github.com/{REPOSITORY}/releases/download"

UPDATE = "update"           # in Parent Recap's folder: STATE and the downloads
STATE = "state.json"
DOWNLOAD = "download."      # a download's folder in UPDATE starts with this
FILES = (STATE, f".{STATE}.incoming")  # what else UPDATE holds
LOG = "update.log"          # in Parent Recap's logs folder
PLUGIN = "plugin"           # the plugin copy the install line puts in Parent Recap's folder
PREFIX = "parent-recap"     # the folder a release zip unpacks into (scripts/release.py)
NEEDS_FAMILY = "release.json"  # in a release zip: whether it needs the family (scripts/release.py)
CORE = "core"               # in release.json's `parts`: all that isn't a Source's own folder

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
    run left alone, as updates are off), skipped (a newer release that changes nothing the
    Household uses, ADR 0051: `skipped` is its version), updated, switched-back, failed.
    `version` is the one it now runs, where it got that far, and `tried` the release it
    downloaded to install, where it got that far."""
    result: str
    message: str
    version: str | None = None
    tried: str | None = None
    skipped: str | None = None


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
    the evening run's day's wait counts from, and `skipped` (the newer version `stable` held at
    the last look, when it changes nothing the Household uses, or None, ADR 0051): a skipped
    release isn't `seen`, so the day's wait counts from the look that first found a release this
    Mac would install. For the next Brief (BriefNote): `tell_updated` (the
    version an evening update installed, until a Brief says so), `tell_failed` (the version whose
    evening update failed and the one still running, until a Brief says so), `told_failed` (the
    last failed version a Brief was to say so of, so each is said once) and `told_updates_itself`
    (whether a Brief has said that Parent Recap updates itself), and with updates off
    `tell_available` (the newer version on `stable`, until a Brief says so) and `told_available`
    (the last version a Brief was to say so of, so each is said once). For a release that needs
    the family: `waiting` (its `version`, the lowest version that takes it in the background,
    `from`, and what it `asks` by language, as its verified zip said; or None), which keeps the
    evening run from downloading that version again, and `told_waiting` (the last version a Brief
    said is waiting, so each is said once). `last_failure`: the last update that failed, its
    `version`, the `step` that stopped it, the `error` in the update log's words (the system's
    word for word, where the system stopped it) and `at` when, for `doctor` and so a Problem
    Report (ADR 0052); None once an update installs. It names versions, steps, programs and
    paths, never a check's item, which may name a kid. Kept in Parent Recap's folder outside the
    versions, so a switch and a switch back keep it."""
    path = (folder or own_node.home()) / UPDATE / STATE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    return {"last_look": None, "last_result": None, "failed": None, "seen": None, "skipped": None,
            "tell_updated": None, "tell_failed": None, "told_failed": None,
            "told_updates_itself": False, "tell_available": None, "told_available": None,
            "waiting": None, "told_waiting": None, "last_failure": None,
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
        # The step that made this update fail and what stopped it, as `failed` logged it last.
        self.failure: tuple[str, str] | None = None

    def __call__(self, step: str, text: str) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(f"{_now()} {step}: {text}\n")

    def failed(self, step: str, text: str, error: str | None = None) -> None:
        """Logs the line of the `step` that made the update fail, and keeps the step and `error`
        (the line's `text` unless given) for `doctor` (state's `last_failure`)."""
        self(step, text)
        self.failure = (step, text if error is None else error)

    def output(self, what: str, text: str) -> None:
        """A program's own output, such as the installer's, indented under its step."""
        if text.strip():
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(f"  {what}:\n" + "".join(f"    {line}\n" for line in text.splitlines()))


# ---------------------------------------------------------------- whether it applies

def not_applicable(config: str | None = None) -> str | None:
    """Why the update doesn't apply to this program, or None when it does. Asks no host."""
    here = profile.current()
    if here.system not in ("macos", "windows") or _hosted(config):
        return ("This is the hosted version: the Parent Recap team updates the server, so there "
                "is nothing to update here.")
    folder = own_node.running_from()
    if folder is None:
        return ("This Parent Recap doesn't run from an installed release (it runs from a copy of "
                "its code), so it doesn't update itself.")
    if versions.current(folder) is None:
        return ("This Parent Recap was installed before it could update itself. Paste the install "
                f"line into {here.terminal} once; from then on it updates itself.")
    if parsed(versions.version(versions.current(folder))) is None:
        return ("The installed version can't be told, so Parent Recap doesn't update itself. Paste "
                f"the install line into {here.terminal} to update it.")
    if not release_signature.ssh_keygen().is_file():
        return no_ssh_keygen(f"there is no ssh-keygen at {release_signature.ssh_keygen()}")
    return None


def no_ssh_keygen(why: str) -> str:
    """What the update says on a computer that can't check a release's signature, `why` being
    what it lacks (ADR 0052): it can't update itself, and the family pastes the install line."""
    here = profile.current()
    return (f"This {here.computer} can't update Parent Recap by itself: it can't check that a "
            f"release is Parent Recap's, as {why}. To update Parent Recap, paste the install line "
            f"into {here.terminal}.")


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
    what came of it; the `newer` version this computer has seen on `stable`, if any; when
    that version is `waiting` for the family to start it, what it `asks` of them by language;
    and the newer version it `skipped` at its last look, as it changes nothing the Household
    uses (ADR 0051), which is neither `newer` nor `waiting`; and the last update that `failed`,
    while the version it tried is newer than the installed one: the step that stopped it and
    the error, as the update log has them (ADR 0052)."""
    version: str
    unavailable: str | None = None
    enabled: bool = True
    last_look: str | None = None
    last_result: str | None = None
    newer: str | None = None
    waiting: str | None = None
    asks: dict[str, str] | None = None
    skipped: str | None = None
    failed: dict[str, str] | None = None

    def line(self) -> str:
        """All of it on one line, in `doctor`'s words."""
        if self.unavailable is not None:
            return f"version {self.version}; updates aren't available here: {self.unavailable}"
        setting = ("updates are on (parent-recap update off turns them off)" if self.enabled else
                   "updates are off: the evening run still looks for a newer release and "
                   "downloads nothing (parent-recap update on turns them on)")
        looked = (f"last looked {self.last_look}: {self.last_result}" if self.last_look
                  else "hasn't looked for a newer release yet")
        failed = "" if self.failed is None else (
            f"; the last update, to version {self.failed['version']} on {self.failed['at']}, "
            f"failed at its {self.failed['step']} step: {self.failed['error']}")
        if self.skipped is not None:
            return (f"version {self.version}; {setting}; {looked}; nothing to install: version "
                    f"{self.skipped} changes nothing this Household uses{failed}")
        if self.waiting is None:
            return f"version {self.version}; {setting}; {looked}{failed}"
        asks = asked(self.asks, "en")
        return (f"version {self.version}; {setting}; {looked}; an update is waiting: version "
                f"{self.waiting} needs you at the computer{f' ({asks})' if asks else ''}, so it "
                f"isn't installed by itself (parent-recap update installs it){failed}")


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
    skipped = _skipped(data, installed)  # a look that skips keeps no `seen`, so nothing is newer
    waiting = _waiting(data, installed)
    if waiting is not None and waiting["version"] != on_stable:
        waiting = None  # a newer release is out since: what that one asks isn't known yet
    return Status(installed, enabled=enabled(config), last_look=data["last_look"],
                  last_result=data["last_result"],
                  newer=on_stable if isinstance(on_stable, str) and newer(on_stable, installed) else None,
                  waiting=waiting["version"] if waiting else None,
                  asks=waiting["asks"] if waiting else None, skipped=skipped,
                  failed=_last_failure(data, installed))


def _last_failure(data: dict, installed: str | None) -> dict[str, str] | None:
    """`state`'s `last_failure`, while the version it tried is still newer than the `installed`
    one: once a newer one runs, as after the install line pasted again, it is over."""
    failure = data["last_failure"]
    fields = ("version", "step", "error", "at")
    if not (isinstance(failure, dict) and all(isinstance(failure.get(f), str) for f in fields)):
        return None
    return failure if installed is not None and newer(failure["version"], installed) else None


def _skipped(data: dict, installed: str | None) -> str | None:
    """`state`'s `skipped`, the release the last look skipped, while it is still newer than the
    `installed` version."""
    skipped = data["skipped"]
    return skipped if isinstance(skipped, str) and installed is not None \
        and newer(skipped, installed) else None


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
            raise Refused(f"GitHub couldn't be reached ({type(e).__name__}): is this "
                          f"{profile.current().computer} online?") from e
        if r.is_redirect:
            url = urljoin(url, r.headers.get("location", ""))
            r.close()
            continue
        if r.status_code != 200:
            r.close()
            raise Refused(f"GitHub answered {r.status_code} for {parts.path}")
        return r
    raise Refused("GitHub redirected the download too many times")


@dataclass(frozen=True)
class _Release:
    """What `release.json` on `stable` says of the release there (ADR 0051): its `version`, and
    its `parts`, the last version that changed the core and each Source with a folder of its own,
    or None when the file has none."""
    version: str
    parts: dict | None = None

    def changes(self, installed: str, used: list[str] | None) -> bool:
        """Whether the release changes something a Household at `installed` uses, whose
        connected Sources' parts are `used` (None when they can't be told): the core, or one of
        those parts, was last changed in a version higher than `installed`."""
        return used is None or any(self.changed(part, installed) for part in (CORE, *used))

    def changed(self, part: str, installed: str) -> bool:
        """Whether `part` was last changed in a version higher than `installed`. What the file
        doesn't say counts as changed: a file without `parts`, and a part it doesn't name or
        gives no version for."""
        if self.parts is None:
            return True
        changed = self.parts.get(part)
        return not isinstance(changed, str) or parsed(changed) is None or newer(changed, installed)


def stable_release(session: requests.Session | None = None) -> _Release:
    """What `release.json` on the `stable` branch says: the one request a look makes. Raises
    Refused when it, or the version in it, can't be read."""
    r = _get(session or _session(), STABLE_RELEASE)
    try:
        data = json.loads(r.content)
        version = data["version"]
    except (requests.RequestException, OSError, ValueError, KeyError, TypeError) as e:
        raise Refused("the version on stable couldn't be read") from e
    finally:
        r.close()
    if not isinstance(version, str) or parsed(version) is None:
        raise Refused("the version on stable isn't one Parent Recap knows how to read")
    parts = data.get("parts")
    return _Release(version.strip(), parts if isinstance(parts, dict) else None)


def _used_parts(config: str | None, adding: str | None = None) -> list[str] | None:
    """The parts of a release that are the Household's connected Sources' (ADR 0051): the folder
    of each Source its config has read that has one, and `adding`, the part of a Source the
    family is adding now. A Source without a folder has no part, as its changes are the core's.
    None when the config can't be read, so nothing is skipped."""
    from . import sources
    from .config import Config
    try:
        cfg = Config.load(config)
        used = [s.part for s in sources.ALL if s.part is not None and s.connected(cfg)]
    except Exception:  # noqa: BLE001 - no config yet, or one doctor names
        return None
    return used if adding is None or adding in used else [*used, adding]


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
        self.why = ""  # why the last `doctor` didn't run, in its log line's words, or ""
        self.said = ""  # the installer's own line on why it stopped, or ""

    def program(self) -> list[str]:
        """The `parent-recap` `app` points at, with the config the update was given."""
        command = installed_program(self.folder)
        return command + (["-c", self.config] if self.config else [])

    def doctor(self, version: str) -> set[str]:
        """The checks the version `app` points at fails, by its own `doctor`. One that couldn't
        be started, was ended at its time limit, or ended with a non-zero exit status without
        printing a failing check, as one that ends with a traceback does, fails
        DOCTOR_DIDNT_RUN. What it wrote to stderr isn't kept: a traceback may name what is the
        Household's. One the system didn't start is logged with the system's words."""
        why = ""
        try:
            r = subprocess.run([*self.program(), "doctor"], stdin=subprocess.DEVNULL,
                               capture_output=True, timeout=DOCTOR_TIMEOUT, **_quiet())
            failing = doctor_failures(r.stdout)
            if r.returncode != 0 and not failing:
                failing = {DOCTOR_DIDNT_RUN}
                why = f" (it ended with exit status {r.returncode} and printed no failing check)"
        except subprocess.TimeoutExpired as e:
            failing = {DOCTOR_DIDNT_RUN}
            why = f" ({type(e).__name__})"
        except OSError as e:
            failing = {DOCTOR_DIDNT_RUN}
            why = f" ({_system_said(e)})"
        self.log("check", f"{version}'s doctor: "
                 + (f"{len(failing)} failing: {', '.join(sorted(failing))}{why}" if failing
                    else "no failures"))
        self.why = why
        return failing

    def install(self, plugin: Path, version: str, spare: Path, dialogs: bool,
                by_family: bool) -> tuple[bool, str | None]:
        """Puts the release in `plugin/`, where the install line puts it, and runs its own
        installer, which installs it beside the running version and switches `app` to it. The
        plugin copy before goes in `spare` until the update is done. The installer is told it
        runs for an update, so the wake's password dialog doesn't start, unless `dialogs`: a release that
        needs the family, which they started. Started `by_family`, it is also told that the run
        lock it finds held is this update's (`steps_env`). Returns whether `app` now points at `version`, and
        how the installer ended when it didn't finish (a non-zero exit status, or ended at its
        time limit), or None when it did: it runs more steps once it has switched, so `app` can
        point at a version whose install didn't finish."""
        target = self.folder / PLUGIN
        if target.exists() and not _is_parent_recap(target):
            raise Refused(f"{target} holds something that isn't Parent Recap, so it was left as it is")
        self.had_plugin = target.exists()
        self.moved = True
        try:
            if self.had_plugin:
                os.rename(target, spare)
            os.rename(plugin, target)
        except OSError as e:
            self.log.failed("install", f"{version}: the release couldn't be put in {target} "
                            f"({_system_said(e)})")
            raise Refused(f"the release couldn't be put in {target} ({e.strerror or e})") from e
        name, command = installer(self.folder, target)
        env = {**steps_env(self.config, by_family), "PARENT_RECAP_HOME": str(self.folder)}
        if dialogs:
            env.pop(UPDATING, None)
        else:
            env[UPDATING] = "1"
        self.log("install", f"{version}: running its {name}"
                 + (", with its dialogs, as the family started it" if dialogs else ""))
        try:
            r = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True,
                               timeout=INSTALL_TIMEOUT, **_quiet(env))
            self.log.output(name, r.stdout + r.stderr)
            finished = r.returncode == 0
            ended = f"ended with exit status {r.returncode}"
            self.said = _why_it_stopped(r.stdout + r.stderr)
        except subprocess.TimeoutExpired as e:
            self.log.output(name, _text(e.stdout) + _text(e.stderr))
            finished = False
            ended = f"was ended at its time limit of {INSTALL_TIMEOUT // 60} minutes"
            self.said = _why_it_stopped(_text(e.stdout) + _text(e.stderr))
        except OSError as e:
            finished = False
            ended = f"couldn't be started ({_system_said(e)})"
        now = versions.current(self.folder)
        switched = now is not None and versions.version(now) == version
        line = (f"{version}: {name} {ended}; app points at "
                f"{versions.version(now) if now else 'nothing'}")
        if finished and switched:
            self.log("install", line)
        else:
            self.log.failed("install", line, line + (f"; it said: {self.said}" if self.said else ""))
        return switched, None if finished else f"{name} {ended}"

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


def installer(folder: Path, release: Path) -> tuple[str, list[str]]:
    """The verified `release`'s own installer, by its name, and the command that runs it as a
    file. On a Mac its `install.sh`, with `/bin/bash`, as the install line runs it. On Windows
    its `app/windows_install.py`, which `install.ps1` hands over to, run with the Python of the
    version `app` in Parent Recap's `folder` points at, by that version's own folder, with `-I`
    as install.ps1 runs it: no PowerShell and no `.ps1` file (ADR 0052). `--downloaded`, as the
    pasted line says it, since the release is in Parent Recap's `plugin` folder, which setup's
    record then lists so uninstall removes it."""
    if profile.current().system == "windows":
        running = versions.current(folder)
        assert running is not None  # not_applicable: an install from before versions isn't updated
        python = running / ".venv" / profile.current().python
        return "windows_install.py", [str(python), "-I", str(release / "app" / "windows_install.py"),
                                      "--downloaded"]
    command = ["/bin/bash", str(release / "install.sh")]
    if install_record.entries("codex-skill"):
        command.append("--codex")  # the Codex skills come with the release, as the line does
    return "install.sh", command


def _quiet(env: dict[str, str] | None = None) -> dict:
    """What a program the update starts runs with, besides its arguments: its output is read as
    UTF-8, which it is told to write (piped, Python on Windows would write the PC's code page,
    which has no ❌, #596), and on Windows it opens no console window, as the evening's update
    runs from pythonw.exe."""
    return {"text": True, "encoding": "utf-8", "errors": "replace",
            "env": {**(os.environ if env is None else env), "PYTHONUTF8": "1"},
            "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}


def _system_said(error: BaseException) -> str:
    """The system's own words for what stopped a step, word for word, for the update log: the
    OSError `error` is or was raised from (a requests error is one), with its kind, as a
    Defender rule's "Operation did not complete successfully because the file contains a virus"
    comes. Empty when there is none."""
    for e in (error, error.__cause__):
        if isinstance(e, OSError):
            return f"{type(e).__name__}: {e}"
    return ""


# The installer's own lines on why it stopped, which carry the system's words where the system
# stopped a step (windows_install.py's `stopped`, `not_started` and `download`): only these of
# its output go to `doctor`, as its other lines are the steps' own.
STOPPED = re.compile(r"^(Parent Recap wasn't installed\. |Windows didn't start |Downloading \S+ failed: )")


def _why_it_stopped(output: str) -> str:
    """The installer's last line on why it stopped (STOPPED), or ""."""
    lines = [line.strip() for line in output.splitlines() if STOPPED.match(line.strip())]
    return lines[-1] if lines else ""


def steps_env(config: str | None, by_family: bool) -> dict[str, str]:
    """The environment of the steps an update runs that look at the run lock: a release's
    installer, and a switch back's steps (schedule_update). In an update the family started, the
    lock is held by the update itself (`alone`) and no run is going on it, which HOLDS_RUN_LOCK
    tells them by naming that lock's file, so the evening jobs are installed again and a wake
    that waits for the family can move. A step that finds another lock held, as when the update
    was given another config than the one the step reads, still takes it for a run. In the
    evening's update the lock is the run's, and nothing says otherwise."""
    env = dict(os.environ)
    if by_family:
        env[HOLDS_RUN_LOCK] = str(run_lock.lock_path(_lock_config(config)))
    else:
        env.pop(HOLDS_RUN_LOCK, None)
    return env


def _lock_config(config: str | None) -> Config:
    """The config whose run lock an update the family started holds: the one at `config`, or
    where it can't be read, when no run can be going on it, one that names no other place."""
    from .config import Config
    try:
        return Config.load(config)
    except Exception:  # noqa: BLE001 - doctor says what's wrong with it
        return Config(kids=[])


def _text(output: str | bytes | None) -> str:
    """What a program ended at its time limit had written: subprocess gives it as bytes."""
    if isinstance(output, bytes):
        return output.decode("utf-8", "replace")
    return output or ""


@dataclass(frozen=True)
class _Failed:
    """Why a version `app` was switched to is taken as failed: in the `message` to the family
    (what follows "Version X"), in the state's `last_result`, and in the update log's line."""
    message: str
    result: str
    log_line: str


def update(config: str | None = None, *, retry_failed: bool = True,
           wait: timedelta | None = None, by_family: bool = True,
           say: Callable[[str], None] | None = None, adding: str | None = None) -> Outcome:
    """Looks for a newer release on `stable` and installs it, checks it with `doctor` and switches
    back when it fails a check the version before didn't. With `retry_failed` False, a version
    that failed before isn't tried again; with `wait`, a version is installed only once this Mac
    has seen it on `stable` that long (the evening run's, `evening`). With `by_family` False, as
    in the evening run, a release that needs the family isn't installed: it is downloaded once,
    and kept in the state as waiting. Started by the family, it installs with its dialogs, and
    `say` is first told what it will ask. With `adding`, the part of a Source the family is
    adding now, that part counts as one the Household uses. Never raises for what the network or the release did:
    the Outcome says it. It takes no lock itself: the evening run holds its own, and what the
    family starts calls it inside `alone`."""
    if (reason := not_applicable(config)) is not None:
        return Outcome("not-applicable", reason)
    folder = own_node.running_from()
    assert folder is not None
    log = _Log(folder)
    installed = versions.version(versions.current(folder))  # type: ignore[arg-type]
    session = _session()
    wanted = _look(folder, config, log, session, installed, adding)
    if isinstance(wanted, Outcome):
        return wanted
    seen = _seen(folder, wanted)
    found = {"seen": seen, "skipped": None}  # a release to install: none is skipped any more
    # Before a version that failed: one the family started and that switched back still waits.
    waiting = _waiting(state(folder), installed)
    if not by_family and waiting is not None and waiting["version"] == wanted:
        log("look", f"{wanted} waits for the family; not downloaded again")
        _save(folder, last_result=f"{wanted} waits for the family to start it", **found)
        return _left_for_the_family(installed, wanted)
    if not retry_failed and state(folder)["failed"] == wanted:
        log("look", f"{wanted} failed before; not tried again")
        _save(folder, last_result=f"{wanted} failed before; not tried again", **found)
        return Outcome("failed", f"Version {wanted} failed its check before, so it wasn't tried "
                       "again. Nothing was changed.", installed)
    if wait is not None:
        first = datetime.fromisoformat(seen["at"])
        if datetime.now(timezone.utc) - first < wait:
            log("look", f"{wanted} first seen {seen['at']}; installed on an evening a day later")
            _save(folder, last_result=f"{wanted} waits until it is a day old", **found)
            return Outcome("waiting", f"Version {wanted} is installed once it has been out a "
                           "day.", installed)
    _save(folder, **found)
    return _update_to(folder, config, log, session, installed, wanted, by_family, say)


@contextmanager
def alone(config: str | None = None) -> Iterator[None]:
    """Holds the evening run's lock (run_lock.py) for the block, as an update the family starts
    does from before its look until it ended, so no run and no other update goes beside it.
    Raises run_lock.Busy when a run or another update holds it."""
    with run_lock.exclusive(_lock_config(config)):
        yield


def _left_for_the_family(installed: str, wanted: str) -> Outcome:
    """What the evening run's update comes to for a release that needs the family: nothing was
    installed, and nothing failed."""
    return Outcome("needs-family", f"Version {wanted} needs you at the computer, so it wasn't "
                   "installed by itself: parent-recap update installs it.", installed)


def _look(folder: Path, config: str | None, log: _Log, session: requests.Session,
          installed: str, adding: str | None = None) -> str | Outcome:
    """The version `stable` holds, when it is higher than the `installed` one and changes
    something the Household uses (ADR 0051), the Source it is `adding` included. Otherwise what came of the look, which is recorded:
    it couldn't be read, nothing newer is out, or the newer release is skipped. A skipped release
    isn't kept as `seen`: the day's wait counts from the look that first finds a release this Mac
    would install."""
    try:
        release = stable_release(session)
    except Refused as e:
        log("look", f"installed {installed}; stable couldn't be read: {e}")
        _save(folder, last_result=f"look failed: {e}")
        return Outcome("failed", f"Parent Recap couldn't look for a newer release: {e}. "
                       "Nothing was changed.", installed)
    wanted = release.version
    log("look", f"installed {installed}, stable holds {wanted}")
    if not newer(wanted, installed):
        _save(folder, last_result=f"up to date at {installed}", seen=_seen(folder, wanted),
              skipped=None)
        return Outcome("up-to-date", f"Parent Recap is up to date: it runs version {installed}, "
                       "the newest release.", installed)
    if not release.changes(installed, _used_parts(config, adding)):
        log("look", f"{wanted} changes nothing this Household uses; not downloaded")
        # What `status` says of `skipped` follows this on doctor's line, so it isn't said twice.
        _save(folder, last_result=f"{wanted} is out", seen=None, skipped=wanted)
        return Outcome("skipped", SKIPPED[0].format(version=wanted, installed=installed),
                       installed, skipped=wanted)
    return wanted


def look(config: str | None = None) -> Outcome:
    """The evening run's look with updates turned off: it reads the file `stable` holds, that
    one request, and downloads nothing. A newer version that changes something the Household
    uses is kept for the next Brief to say is out, once per release (BriefNote), and for the
    setup page to offer; one that changes nothing it uses is skipped, and no Brief names it."""
    if (reason := not_applicable(config)) is not None:
        return Outcome("not-applicable", reason)
    folder = own_node.running_from()
    assert folder is not None
    log = _Log(folder)
    installed = versions.version(versions.current(folder))  # type: ignore[arg-type]
    wanted = _look(folder, config, log, _session(), installed)
    if isinstance(wanted, Outcome):
        return wanted
    log("look", f"{wanted} is out; updates are off, so nothing was downloaded")
    tell = {} if state(folder)["told_available"] == wanted else {"tell_available": wanted}
    _save(folder, last_result=f"{wanted} is out; not installed, as updates are off",
          seen=_seen(folder, wanted), skipped=None, **tell)
    return Outcome("available", f"Version {wanted} is out. Updates are off, so it wasn't "
                   "installed: parent-recap update installs it.", installed)


def evening(config: str | None = None) -> Outcome:
    """The update the evening run starts once its Brief is sent, or it decided to send nothing
    (ADR 0047): `update`, for a release this Mac has seen on `stable` a day, and not for one that
    failed before. It runs in the background, so the wake's password dialog doesn't start
    (schedule_update.UPDATING), and inside the evening job, under its run lock, which it doesn't
    take a second time, so the evening jobs aren't installed again: they start the program from
    the same path, which now points at the version switched to. The evening run catches and logs whatever it raises
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
    running version is still older, and no Brief has said so of that version. Neither is said
    once the last look skipped what `stable` holds, as it changes nothing the Household uses
    (ADR 0051)."""
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
    skips = _skipped(data, running) is not None
    if skips:
        available = None
    waiting = _waiting(data, running) if updates_on and not skips else None
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
    try:
        (folder / UPDATE).mkdir(mode=0o700, parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix=DOWNLOAD, dir=folder / UPDATE))
    except OSError as e:
        log.failed("download", f"{wanted}: its folder couldn't be made ({_system_said(e)})")
        _save(folder, last_result=f"{wanted} refused: its download folder couldn't be made",
              last_failure=_failure(log, wanted))
        return Outcome("failed", f"Version {wanted} wasn't installed: its download folder "
                       f"couldn't be made ({e.strerror or e}). Nothing was changed.", installed)
    outcome = _install(folder, config, log, session, installed, wanted, work, by_family, say)
    if outcome.result == "updated":
        _write_state(folder, {**state(folder), "last_failure": None})
    elif outcome.result in ("failed", "switched-back") and log.failure is not None:
        _write_state(folder, {**state(folder), "last_failure": _failure(log, wanted)})
    return outcome


def _failure(log: _Log, version: str) -> dict[str, str] | None:
    """`state`'s `last_failure` for the update to `version` that `log` logged, or None."""
    if log.failure is None:
        return None
    step, error = log.failure
    return {"version": version, "step": step, "error": error, "at": _now()}


def _install(folder: Path, config: str | None, log: _Log, session: requests.Session,
             installed: str, wanted: str, work: Path, by_family: bool,
             say: Callable[[str], None] | None) -> Outcome:
    """`_update_to` once it has the folder `work` to download into, which it removes."""
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
            said = _system_said(e)
            log.failed("download", f"{wanted}: failed: {e}" + (f" ({said})" if said else ""))
            raise
        log("download", f"{wanted}: {name} ({zip_path.stat().st_size} bytes) and its signature")
        tried = wanted
        try:
            good = release_signature.verified(zip_path, signature)
        except release_signature.NoSshKeygen as e:
            log.failed("verify", f"{wanted}: couldn't be checked: {_system_said(e)}")
            # Kept as failed, so the evening run doesn't download it again to find the same.
            _save(folder, failed=wanted)
            raise Refused(f"its signature couldn't be checked, so it was deleted. "
                          f"{no_ssh_keygen(str(e))}") from e
        except subprocess.TimeoutExpired as e:
            log.failed("verify", f"{wanted}: couldn't be checked: {type(e).__name__}")
            raise Refused("its signature couldn't be checked, so it was deleted") from e
        except OSError as e:
            log.failed("verify", f"{wanted}: couldn't be checked: {_system_said(e)}")
            raise Refused("its signature couldn't be checked, so it was deleted") from e
        if not good:
            log.failed("verify", f"{wanted}: the signature doesn't verify; deleted")
            raise Refused("its signature doesn't verify against Parent Recap's release keys, so "
                          "it isn't a release of Parent Recap's and was deleted")
        log("verify", f"{wanted}: the signature verifies")
        try:
            plugin = _unpack(zip_path, work / "unpacked")
        except Refused as e:
            said = _system_said(e)
            log.failed("unpack", f"{wanted}: failed: {e}" + (f" ({said})" if said else ""))
            raise
        inside = _zip_version(plugin)
        if inside != wanted or not newer(inside, installed):
            log.failed("verify", f"{wanted}: the zip holds version {inside}; refused and deleted")
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
        switched, unfinished = updater.install(plugin, wanted, spare, dialogs, by_family)
        if not switched:
            updater.restore_plugin(spare)
            log("install", f"{wanted}: not installed; {installed} runs as it did")
            _save(folder, last_result=f"{wanted} didn't install", failed=wanted)
            return Outcome("failed", f"Version {wanted} couldn't be installed, so Parent Recap "
                           f"runs version {installed} as it did. The update log is "
                           f"{log.path}.", installed, tried=wanted)
        log("switch", f"{installed} -> {wanted}")
        failed = None
        if unfinished is not None:
            # The installer switched and then didn't finish, so a step of its after the switch
            # (the record, the command, the Linked Device's restart, the evening jobs, the
            # Codex skills) didn't run or didn't end: the version isn't whole, whatever its
            # doctor would say.
            failed = _Failed(f"was switched to, but its installer didn't finish ({unfinished})",
                             f"{wanted}'s installer didn't finish",
                             f"{wanted}'s {unfinished} after the switch")
        else:
            after = updater.doctor(wanted)
            # A doctor that didn't run is a failure even where the one before didn't run either.
            new = ", ".join(sorted((after - before) | (after & {DOCTOR_DIDNT_RUN})))
            if new:
                failed = _Failed("was installed, but its check failed where version "
                                 f"{installed} passed ({new})",
                                 f"{wanted} failed its check", f"{wanted} fails {new}")
                # For doctor without the checks' items, which may name a kid: how many, and
                # why its doctor didn't run, in the system's words where the system stopped it.
                count = len((after - before) | (after & {DOCTOR_DIDNT_RUN}))
                log.failure = ("check", f"{wanted}'s doctor didn't run{updater.why}"
                               if DOCTOR_DIDNT_RUN in after else
                               f"{wanted}'s doctor fails {count} check{'s' * (count != 1)} "
                               f"that version {installed} passed")
        if failed is not None:
            outcome = _switch_back(folder, log, updater, spare, wanted, failed, by_family)
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
    except OSError as e:
        # A step the system stopped that nothing above names, such as a downloaded zip a
        # Defender rule took away: in the log in the system's words (ADR 0052).
        now = versions.current(folder)
        running = versions.version(now) if now is not None else installed
        log.failed("update", f"{wanted}: stopped ({_system_said(e)}); app points at {running}")
        _save(folder, last_result=f"{wanted} stopped: {e.strerror or e}")
        return Outcome("failed", f"Version {wanted} wasn't installed: the system stopped the "
                       f"update ({e.strerror or e}). Parent Recap runs version {running}. The "
                       f"update log is {log.path}.", running, tried=tried)
    finally:
        if not keep:  # whatever ended the update, the plugin copy is the running version's
            updater.restore_plugin(spare)
        shutil.rmtree(work, ignore_errors=True)


def _switch_back(folder: Path, log: _Log, updater: _Updater, spare: Path, wanted: str,
                 failed: _Failed, by_family: bool) -> Outcome:
    """Switches back from `wanted`, which `app` points at and which `failed`, as
    `parent-recap switch-back` does, puts the plugin copy before back and keeps the version as
    failed. Its steps run with the environment the installer had (`steps_env`)."""
    try:
        with open(log.path, "a", encoding="utf-8") as out:
            # The update holds the run lock already, the evening run's or its own (`alone`).
            running, _ = versions.switch_back_whole(folder, updater.config, stdout=out, stderr=out,
                                                    env=steps_env(updater.config, by_family),
                                                    locked=True,
                                                    creationflags=_quiet()["creationflags"])
    except OSError as e:  # a link the system didn't let it change (its steps never raise)
        now = versions.current(folder)
        points = f"app points at {versions.version(now) if now else 'nothing'}"
        # doctor's copy leaves out the failing checks, which may name a kid.
        log.failed("switch", f"{wanted}: couldn't switch back ({_system_said(e)}), though "
                   f"{failed.log_line}; {points}",
                   f"{wanted}: couldn't switch back ({_system_said(e)}); {points}")
        _save(folder, last_result=f"{failed.result}; the switch back failed", failed=wanted)
        return Outcome("failed", f"Version {wanted} {failed.message}, and switching back to "
                       f"the version before failed ({e.strerror or e}). The update log is "
                       f"{log.path}.", versions.version(now) if now else None, tried=wanted)
    except versions.NoPrevious:  # install.sh keeps the version before, so only if it was removed
        log.failed("switch", f"{wanted}: no previous version to switch back to, though "
                   f"{failed.log_line}", f"{wanted}: no previous version to switch back to")
        _save(folder, last_result=f"{failed.result}; no version to switch back to",
              failed=wanted)
        return Outcome("failed", f"Version {wanted} {failed.message}, and "
                       "there is no previous version to switch back to. The update log is "
                       f"{log.path}.", wanted, tried=wanted)
    updater.restore_plugin(spare)
    log("switch", f"{wanted} -> {running}: back, as {failed.log_line}")
    _save(folder, last_result=f"{failed.result}; back on {running}", failed=wanted)
    return Outcome("switched-back", f"Version {wanted} {failed.message}, so "
                   f"Parent Recap switched back to version {running}. The update log is "
                   f"{log.path}.", running, tried=wanted)


# ---------------------------------------------------------------- adding a Source

def needed_to_add(part: str | None, config: str | None = None) -> str | None:
    """The version to install before the family adds the Source whose part is `part` (ADR
    0051): the one `stable` holds, when that part changed in a version higher than the installed
    one, as it can for a Household that skipped releases while it didn't have the Source.
    Otherwise None: the part isn't newer, or the Source has no part (`part` is None) or the
    program doesn't update itself here, which both ask no host. One request, for the file
    `stable` holds; it downloads nothing and leaves the update's state as it is. Raises Refused
    when that file can't be read, as the installed copy then can't be told from an old one."""
    if part is None or not_applicable(config) is not None:
        return None
    folder = own_node.running_from()
    assert folder is not None
    log = _Log(folder)
    installed = versions.version(versions.current(folder))  # type: ignore[arg-type]
    try:
        release = stable_release()
    except Refused as e:
        log("look", f"installed {installed}, adding {part}; stable couldn't be read: {e}")
        raise
    needed = newer(release.version, installed) and release.changed(part, installed)
    log("look", f"installed {installed}, stable holds {release.version}; adding {part}, which "
        + ("changed since: updating first" if needed else "didn't change since"))
    return release.version if needed else None


def before_adding(part: str | None, config: str | None = None,
                  say: Callable[[str], None] | None = None) -> Outcome | None:
    """What a Source's `setup` step in a terminal does first (ADR 0051): when the Source's part
    changed after the installed version (`needed_to_add`), it installs the newest release, under
    the rules of an update the family started: alone (`alone`), at once, with its dialogs, and a
    version that failed before tried again. `say` is told that it updates first, and what a
    release that needs the family will ask. None when the step goes on as it is; an Outcome
    whose result is `updated` when it goes on in the new version (`run_again`); any other
    Outcome is why the update couldn't be done, `busy` while a run or another update holds the
    lock, and the step doesn't go on with the old code."""
    try:
        wanted = needed_to_add(part, config)
    except Refused as e:
        return Outcome("failed", f"Parent Recap couldn't look for a newer release: {e}. "
                       "Nothing was changed.")
    if wanted is None:
        return None
    if say is not None:
        say(f"Updating Parent Recap to version {wanted} first, as this part of it changed since "
            "the installed version. This can take a few minutes.")
    try:
        with alone(config):
            outcome = update(config, say=say, adding=part)
    except run_lock.Busy:
        return Outcome("busy", BUSY[0])
    # `stable` went back between the two looks: there is nothing newer to add it with.
    return None if outcome.result in ("up-to-date", "skipped", "not-applicable") else outcome


def installed_version() -> str | None:
    """The version `app` points at where the running program was installed, or None for a
    program that doesn't run from an installed release. Reads two files and asks nothing else."""
    folder = own_node.running_from()
    current = versions.current(folder) if folder is not None else None
    return versions.version(current) if current is not None else None


def installed_program(folder: Path) -> list[str]:
    """The command that runs `parent-recap` of the version `app` points at in Parent Recap's
    `folder`: on a Mac its venv's `parent-recap`; on Windows its venv's Python with
    `-m family_brief`, as the PATH's `parent-recap.cmd` runs it, not pip's launcher, which
    Defender can block (#591)."""
    here = profile.current()
    venv = folder / versions.APP / ".venv"
    if here.system == "windows":
        return [str(venv / here.python), "-m", "family_brief"]
    return [str(venv / "bin" / "parent-recap")]


def run_again(arguments: list[str]) -> int:
    """Runs the installed program, the version `app` now points at, with `arguments`, in this
    terminal, and returns its exit status: how a step goes on once `before_adding` updated."""
    folder = own_node.running_from()
    assert folder is not None
    program = installed_program(folder)
    try:
        return subprocess.run([*program, *arguments], check=False).returncode
    except OSError as e:
        print(f"Parent Recap was updated, but {program[0]} couldn't be started "
              f"({type(e).__name__}). Run the command again.", file=sys.stderr)
        return 1


# What a Source's step says, in the family's language, when Parent Recap had to update itself
# before the Source is added and couldn't (ADR 0051). `{source}` is the Source's name.
NOT_ADDED = {
    "en": "Parent Recap has to update itself before {source} is added, as its code for {source} "
          "changed since the installed version, and the update couldn't be done. So {source} "
          "wasn't added and nothing was changed. Check that this computer is online and try "
          "again.",
    "fi": "Parent Recapin on päivitettävä itsensä ennen kuin {source} lisätään, koska sen "
          "{source}-osa on muuttunut asennetun version jälkeen, eikä päivitys onnistunut. Siksi "
          "{source} jäi lisäämättä eikä mitään muutettu. Tarkista, että tietokone on verkossa, ja "
          "yritä uudelleen.",
    "zh": "添加 {source} 之前，Parent Recap 需要先更新自己，因为其中 {source} 的部分在已安装的版本之后"
          "有过改动，但这次更新没有成功。所以 {source} 没有添加，也没有改动任何东西。请确认这台电脑已联网，"
          "然后再试一次。",
}
NOT_ADDED_BUSY = {
    "en": "Parent Recap has to update itself before {source} is added, and it is busy right now: "
          "a Brief is being made, or another update is running. So {source} wasn't added and "
          "nothing was changed. Try again in a few minutes.",
    "fi": "Parent Recapin on päivitettävä itsensä ennen kuin {source} lisätään, ja sillä on juuri "
          "nyt muuta tekeillä: koostetta tehdään tai toinen päivitys on käynnissä. Siksi {source} "
          "jäi lisäämättä eikä mitään muutettu. Yritä uudelleen muutaman minuutin kuluttua.",
    "zh": "添加 {source} 之前，Parent Recap 需要先更新自己，但它现在正忙：正在生成日报，或者另一个更新"
          "正在进行。所以 {source} 没有添加，也没有改动任何东西。请过几分钟再试。",
}


def not_added(outcome: Outcome, source: str, config: str | None = None) -> str:
    """What the step for `source` says of `outcome`, an update `before_adding` couldn't do, in
    the Household's language where the program has reviewed text in it, or else in English."""
    from .config import Config
    try:
        language = Config.load(config).summary_language
    except Exception:  # noqa: BLE001 - no config yet, or one doctor names
        language = "en"
    texts = NOT_ADDED_BUSY if outcome.result == "busy" else NOT_ADDED
    return texts.get(language, texts["en"]).format(source=source)


# ---------------------------------------------------------------- parent-recap update

ON = ("Updates are on: Parent Recap installs each new release by itself after the evening Brief. "
      "parent-recap update off turns that off.")
OFF = ("Updates are off: Parent Recap still looks once each evening whether a newer release is "
       "out, says so in the Brief, and downloads nothing. parent-recap update installs the newest "
       "release when you choose, and parent-recap update on turns updates on again.")


# What `parent-recap update` says of a newer release it didn't install, as it changes nothing the
# Household uses (ADR 0051), in each of the program's languages, as BUSY is. The first is the
# Outcome's message.
SKIPPED = ("Version {version} of Parent Recap is out, but it changes nothing your family uses, so "
           "it wasn't installed and nothing was downloaded. Parent Recap stays on version "
           "{installed}.",
           "Parent Recapista on ilmestynyt versio {version}, mutta se ei muuta mitään, mitä "
           "perheesi käyttää, joten sitä ei asennettu eikä mitään ladattu. Parent Recap pysyy "
           "versiossa {installed}.",
           "Parent Recap 有新版本 {version} 了，但它没有改动你家用到的任何部分，所以没有安装，也没有下载"
           "任何东西。Parent Recap 仍然是 {installed} 版。")


# What `parent-recap update` says when a run or another update holds the run lock, in each of
# the program's languages: it can't tell which one the family reads.
BUSY = ("Parent Recap is busy right now: a Brief is being made, or another update is running, so "
        "this update wasn't started and nothing was changed. Try again in a few minutes.",
        "Parent Recapilla on juuri nyt muuta tekeillä: koostetta tehdään tai toinen päivitys on "
        "käynnissä, joten tätä päivitystä ei aloitettu eikä mitään muutettu. Yritä uudelleen "
        "muutaman minuutin kuluttua.",
        "Parent Recap 正忙：正在生成日报，或者另一个更新正在进行，所以这次更新没有开始，也没有改动任何东西。"
        "请过几分钟再试。")


def cmd_update(args: argparse.Namespace) -> int:
    if args.setting is not None:
        return _cmd_setting(args.config, args.setting == "on")
    if (reason := not_applicable(args.config)) is not None:
        print(reason)  # before the lock: there is nothing to update here, whatever else is going
        return 0
    try:
        with alone(args.config):
            outcome = update(args.config, say=lambda text: print(text, flush=True))
    except run_lock.Busy:
        print("\n".join(BUSY))
        return run_lock.BUSY
    if outcome.result == "skipped":
        print("\n".join(text.format(version=outcome.skipped, installed=outcome.version)
                        for text in SKIPPED))
    else:
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
                       "switch back if the check fails (on a Mac or a PC); with on or off, turn the "
                       "evening's own update on or off")
    p.add_argument("setting", nargs="?", choices=["on", "off"],
                   help="Turn updating by itself after the evening Brief on or off, instead of "
                        "updating now")
    p.set_defaults(func=cmd_update)
