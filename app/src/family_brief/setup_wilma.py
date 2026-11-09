"""The setup page's Wilma sign-in (ADR 0008): the town list, the wilma CLI's profile, and the check.

The wilma CLI signs in on screens of its own, but every command reads its saved profiles from
one JSON file. So the page asks for the town and the Wilma username and password
itself, this writes that profile the way the CLI does after its own sign-in, and the CLI's Kid
list checks that it works. The format isn't documented, so setup installs the CLI version below,
install.sh moves an installed one to it (update()), and tests/e2e/test_setup_wilma.py checks the
profile written against that version's way of reading it.

The CLI is installed and run with Parent Recap's own Node, in its own folder (`own_node`, ADR 0011).
The town list is Wilma's public tenant list, the copy the CLI ships inside its wilma-client.

From 2.5.0 the CLI keeps a saved sign-in's password and two-step key in the system's keychain
itself (`wilma config secrets auto`, #849): the macOS Keychain, Windows Credential Manager or a
Linux desktop's Secret Service, and its own owner-only file where there is none, as on the hosted
version's server. So setup's sign-ins are the CLI's own profiles again, and after each one setup
saves, and on every update, `secure` has the CLI move them there. The password is never put on a
command line, into a log or back to the page. Setup's sign-ins from 0.11 to 0.15 (#710) kept their
secrets in Parent Recap's own store and the rest in ~/.family/wilma-signins.json; `prepare` moves
each back into the CLI's config, and so to the keychain, without the family signing in again.

The Terminal sign-in window runs the CLI's own screens instead, with a guide in the family's
language: `setup wilma` opens it, and the page offers it when its own sign-in fails for any reason
but a wrong password.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import math
import os
import re
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
from contextlib import contextmanager, suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlsplit

from . import failure, install_record, own_node, private_files, run_lock
from .collectors import wilma
from .setup_report import outcome, program
from .system import desktop, secrets
from .system import profile as system_profile  # `profile` is the wilma CLI's here

try:  # the Wilma window's pseudo-terminal, which Windows doesn't have yet
    import fcntl
    import pty
    import termios
    import tty
except ImportError:
    pass

log = logging.getLogger(__name__)

WILMA_CLI_VERSION = "2.5.0"
# The first version that keeps saved passwords in the system's keychain (`wilma config secrets`).
SECRETS_VERSION = (2, 5, 0)
SECRETS_SECONDS = 120  # a keychain call may wait up to 15 seconds for each sign-in
MOVE_FAILED = 4  # `wilma config secrets auto`'s exit status when a sign-in couldn't be moved
KEYCHAIN_SERVICE = "ai.wilm.wilma-cli"  # the name of the CLI's keychain items, one per sign-in
PACKAGE = own_node.WILMA_PACKAGE
INSTALL_SECONDS = 300
SALT = "wilmai::"  # the CLI's own, in front of the password before it's Base64-encoded
# What the CLI keeps next to its config: the Wilma sessions its commands carry on with (from 2.0),
# and its note of the newest version on npm.
SESSIONS = "wilmai-sessions.json"
VERSION_CHECK = "version-check.json"
# What of a profile setup from 0.11 to 0.15 kept in the secret store (#710), as the CLI keeps it:
# the password and the two-step key, each Base64 after SALT. The secret is them in this order, with
# a space between, the key left out when there was none.
SECRET_FIELDS = ("passwordObfuscated", "totpSecretObfuscated")
# Each check's folder (`run_folder`) holds this file locked while the check lasts, so a later one
# removes only one whose check has ended, also when it was killed. One without it yet is
# being made, unless it's older than this.
RUN_LOCK = "run.lock"
UNLOCKED_SECONDS = 60
MIN_QUERY = 2
MAX_TOWNS = 20
# A town's own Wilma, which all its schools sign in through, is named after the town in its
# list entry: "Espoon kaupunki", "Esbo stad", "Sipoon kunta". A school's own Wilma isn't (#688).
TOWN_WILMA = re.compile(r"\b(kaupunki|kunta|stad|kommun)\b", re.IGNORECASE)
# The Wilma address of each city in "City presets" in docs/config.md.
WILMA_CITIES = {
    "espoo.inschool.fi": "Espoo",
    "helsinki.inschool.fi": "Helsinki",
    "yvkoulut.inschool.fi": "Helsinki",
    "vantaa.inschool.fi": "Vantaa",
    "kauniainen.inschool.fi": "Kauniainen",
}
WILMA_POLL_SECONDS = 2
WILMA_PAGE_POLL_SECONDS = 0.2  # in the window, so it ends the CLI as soon as it has signed in
# The Wilma window's own text, in the reviewed languages; any other language gets the English.
# Wilma comes before the AI login in setup, so there's no AI yet to translate it.
WILMA_WINDOW = {
    "en": {
        "guide": ["Parent Recap: sign in to Wilma",
                  "1. Type your town in Finnish, for example Espoo, Helsinki or Vantaa, and pick "
                  "it from the list.",
                  "2. Sign in with the same username and password as on the Wilma website or "
                  "app. If you don't remember them, look in your browser's saved passwords."],
        # In place of the guide's first step on Windows, where Wilma's own page signs in.
        "page": "1. Wilma's sign-in page opens in your browser. Type your town there in Finnish, "
                "for example Espoo, Helsinki or Vantaa, and pick it from the list.",
        "signed_in": "You're signed in to Wilma.",
        "close": "You can close this window.",
    },
    "zh": {
        "guide": ["Parent Recap：登录 Wilma",
                  "1. 用芬兰语输入你所在的城市，例如 Espoo、Helsinki 或 Vantaa，然后在列表里选中它。",
                  "2. 用你在 Wilma 网站或 App 上的用户名和密码登录。如果记不清，可以在浏览器保存的密码里找到。"],
        "page": "1. 浏览器会打开 Wilma 的登录页面。在那里用芬兰语输入你所在的城市，例如 Espoo、Helsinki 或 "
                "Vantaa，然后在列表里选中它。",
        "signed_in": "已登录 Wilma。",
        "close": "可以关闭这个窗口了。",
    },
    "fi": {
        "guide": ["Parent Recap: kirjaudu Wilmaan",
                  "1. Kirjoita kuntasi nimi suomeksi, esimerkiksi Espoo, Helsinki tai Vantaa, ja "
                  "valitse se listasta.",
                  "2. Kirjaudu samalla käyttäjätunnuksella ja salasanalla kuin Wilman "
                  "verkkosivulla tai sovelluksessa. Jos et muista niitä, katso ne selaimesi "
                  "tallennetuista salasanoista."],
        "page": "1. Wilman kirjautumissivu aukeaa selaimeesi. Kirjoita siellä kuntasi nimi "
                "suomeksi, esimerkiksi Espoo, Helsinki tai Vantaa, ja valitse se listasta.",
        "signed_in": "Olet kirjautunut Wilmaan.",
        "close": "Voit sulkea tämän ikkunan.",
    },
}
# Node's console.clear(), which the wilma CLI calls before each of its questions.
NODE_CLEAR = b"\x1b[1;1H\x1b[0J"
# What the wilma CLI asks once the sign-in has worked. Setup reads every Kid, so the family never
# sees either: the window answers the first itself and ends the CLI before the second.
WILMA_STUDENT_PICKER = b"Select student"
WILMA_MENU = b"What do you want to view?"


def installed() -> bool:
    return own_node.wilma() is not None


def installed_version(folder: Path | None = None) -> str | None:
    """The version of the wilma CLI in `folder` (Parent Recap's own, unless given), as its package
    says, or None."""
    package = own_node.modules(folder or own_node.wilma_folder()) / PACKAGE / "package.json"
    try:
        version = json.loads(package.read_text(encoding="utf-8")).get("version")
    except (OSError, ValueError, AttributeError):
        return None
    return version if isinstance(version, str) else None


def install() -> dict[str, Any]:
    """Installs the pinned wilma CLI with Parent Recap's own Node into its folder, unless it's
    there, and adds that folder to setup's record. Returns its `result`: `installed`, `no-node`
    (the install didn't leave Parent Recap's own Node) or `install-failed`, which comes with the
    `reason` npm failed, for the setup page to show."""
    if installed():
        return {"result": "installed"}
    npm = own_node.npm()
    if npm is None:
        return {"result": "no-node"}
    # Before npm runs, so uninstall removes the folder however this install ends.
    install_record.add("wilma-cli", str(own_node.wilma_folder()))
    reason = _npm_install(npm)
    return {"result": "install-failed", "reason": reason} if reason else {"result": "installed"}


def update() -> str:
    """Moves the wilma CLI in Parent Recap's folder to the pinned version, which install.sh asks
    for on every update, so a Household that connected Wilma gets the version a release pins. A
    CLI left half there, as by an npm ended partway before the install was staged, is installed
    again when the CLI's config has a sign-in. Returns `not-installed` (no CLI to update, so
    nothing is installed), `up-to-date`, `updated`, `no-node` or `update-failed`, which leaves the
    CLI that was there as it was."""
    if installed() and installed_version() == WILMA_CLI_VERSION:
        return "up-to-date"
    if not (installed() or _half_there()):
        return "not-installed"
    npm = own_node.npm()
    if npm is None:
        return "no-node"
    return "updated" if _npm_install(npm) is None else "update-failed"


def _half_there() -> bool:
    """Whether the CLI's folder is there, or setup's record has it, without a CLI that runs, while
    there is a sign-in: Wilma was connected, and the CLI broke."""
    folder = own_node.wilma_folder()
    return (folder.exists() or str(folder) in install_record.entries("wilma-cli")) \
        and bool(signins())


def _npm_install(npm: list[str]) -> str | None:
    """Has `npm`, on Parent Recap's own Node, install the pinned CLI. Returns why it didn't, which
    is also logged, or None once it did. It installs into a new folder next to the CLI's, which
    takes the CLI's place only once npm has finished and left the pinned version, so an npm that
    fails, or is ended at the time limit, changes nothing."""
    return own_node.install_staged(own_node.wilma_folder(), lambda staging: _npm_run(npm, staging),
                                   "The wilma CLI didn't install", INSTALL_SECONDS)


def _npm_run(npm: list[str], staging: Path) -> str | None:
    """Runs `npm` to install the pinned CLI into `staging`. Returns why that didn't leave the CLI
    there, in words a photo of the setup page carries, or None."""
    proc = own_node.run_npm(npm, install_args(staging), INSTALL_SECONDS)
    if proc.returncode != 0:
        return failure.exited("npm", proc.returncode, proc.stderr, proc.stdout)
    version = installed_version(staging)
    if version is None:
        return "npm finished without installing the wilma program."
    if version != WILMA_CLI_VERSION:
        return f"npm installed version {version} of the wilma program, not {WILMA_CLI_VERSION}."
    if own_node.wilma_cli(staging) is None:
        return "npm installed the wilma program without the file that runs it."
    return None


def install_args(folder: Path) -> list[str]:
    """npm's arguments that install the pinned CLI (ADR 0008) into `folder`, laid out as a global
    install: `bin/wilma`, and the package in `lib/node_modules` (`own_node` has Windows' layout).
    `_npm_install` gives a new folder,
    which then takes the place of Parent Recap's own."""
    return ["install", "-g", "--prefix", str(folder), f"{PACKAGE}@{WILMA_CLI_VERSION}"]


def tenants() -> list[dict[str, Any]] | None:
    """Each Wilma in the tenant list the installed CLI ships, as its address, its name and its
    towns (Finnish, Swedish), or None without the CLI or the list."""
    command = own_node.wilma()
    if command is None:
        return None
    package = Path(os.path.realpath(command[-1])).parent.parent  # past dist/index.js
    for path in (package / "node_modules" / "@wilm-ai" / "wilma-client" / "tenant_list.json",
                 package.parent / "wilma-client" / "tenant_list.json"):  # where npm hoists it
        try:
            listed = json.loads(path.read_text(encoding="utf-8"))["wilmat"]
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if isinstance(listed, list):
            return [t for t in map(_tenant, listed) if t is not None]
    return None


def _tenant(raw: Any) -> dict[str, Any] | None:
    if not (isinstance(raw, dict) and isinstance(raw.get("url"), str) and raw["url"]):
        return None
    towns = []
    for m in raw.get("municipalities") or []:
        if isinstance(m, dict):
            fi = m.get("name_fi") or m.get("nameFi")
            sv = m.get("name_sv") or m.get("nameSv") or fi
            if isinstance(fi, str) and fi and (fi, sv) not in towns:
                towns.append((fi, sv))
    name = raw.get("name")
    return {"url": raw["url"], "name": name if isinstance(name, str) and name else raw["url"],
            "towns": towns}


def search(listed: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    """The entries `query` finds, by town in Finnish or Swedish first, then by name, each with
    the town it's for: the one found, or the entry's first. The towns' own Wilmas come first
    among those found by town, marked `town_wide`, so nobody takes them for a single school."""
    needle = _fold(query)
    if len(needle) < MIN_QUERY:
        return []
    by_town, by_name = [], []
    for t in listed:
        town = next((fi for fi, sv in t["towns"] if needle in _fold(fi) or needle in _fold(sv)),
                    None)
        if town is not None:
            by_town.append({"url": t["url"], "name": t["name"], "town": town,
                            "town_wide": bool(TOWN_WILMA.search(t["name"]))})
        elif needle in _fold(t["name"]):
            by_name.append({"url": t["url"], "name": t["name"],
                            "town": t["towns"][0][0] if t["towns"] else None,
                            "town_wide": bool(TOWN_WILMA.search(t["name"]))})
    by_town.sort(key=lambda t: not t["town_wide"])  # stable: the list's own order otherwise
    return (by_town + by_name)[:MAX_TOWNS]


def _fold(text: str) -> str:
    """For searching: no case, and no accents, so jarvenpaa finds Järvenpää."""
    return "".join(c for c in unicodedata.normalize("NFKD", text.strip().casefold())
                   if not unicodedata.combining(c))


def entry(listed: list[dict[str, Any]], url: str) -> dict[str, Any] | None:
    return next((t for t in listed if t["url"] == url), None)


def towns_of(t: dict[str, Any]) -> list[str]:
    return [fi for fi, _ in t["towns"]]


def obfuscate(password: str) -> str:
    """The password as the CLI keeps it: Base64, not encrypted (ADR 0008)."""
    return base64.b64encode((SALT + password).encode()).decode()


def sign_in(tenant: dict[str, Any], username: str, password: str) -> dict[str, Any]:
    """Writes the CLI's profile for `username` at the Wilma `tenant`, checks it with the CLI's Kid
    list, and once that works saves it as one of setup's sign-ins (`save_signin`). Returns its
    `result`: `signed-in` with the `kids`, or `wrong-password`, `sign-in-failed`, which comes with
    the `reason` the CLI gave, for the setup page to show, `no-kids` or `not-installed`. Unless
    signed in, nothing is saved, so a wrong password isn't kept and an earlier sign-in still works.
    A new profile goes in setup's record. Signed in, it comes with the saved `profile`'s id."""
    out, students = check(tenant, username, password)
    if out["result"] != "signed-in":
        return out
    profiles_before = [p["id"] for p in signins() if isinstance(p.get("id"), str)]
    # With the Kids the CLI saved in the profile it checked.
    try:
        profile_id = save_signin(tenant, username, password, students)
    except OSError as e:
        return {"result": "sign-in-failed", "reason": failure.logged(
            "The Wilma sign-in wasn't saved", str(e))}
    record_profile(profile_id, profiles_before)
    return {**out, "profile": profile_id}


def check(tenant: dict[str, Any], username: str,
          password: str) -> tuple[dict[str, Any], list[dict[str, Any]] | None]:
    """Signs in to the Wilma `tenant` as `username` with the CLI's Kid list, saving nothing in
    the CLI's config. Returns sign_in's result, and the Kids as the CLI saves them in a profile
    (`write_profile`'s `students`), which the hosted sign-up writes under the Household's own
    user (ADR 0031)."""
    if not installed():
        return {"result": "not-installed"}, None
    # From 2.0 the CLI's commands read every profile in its config, so an earlier one that works
    # would hide a wrong password. The new one is checked alone, in a config of its own in an
    # owner-only folder, which also takes the session the CLI saves next to it and goes with it.
    with run_folder() as folder:
        alone = folder / "config.json"
        checked_id = write_profile(alone, tenant, username, password)
        out: dict[str, Any]
        try:
            kids = [k for k in wilma.list_kids(alone) if isinstance(k.get("name"), str) and k["name"]]
            # No Kids: not a guardian's account.
            out = {"result": "signed-in", "kids": kids} if kids else {"result": "no-kids"}
        except wilma.WilmaError as e:
            out = {"result": "wrong-password"} if e.wrong_password else {
                "result": "sign-in-failed",
                "reason": failure.logged("Wilma didn't sign in", failure.raised(e, "Signing in"))}
        checked = profile(alone, checked_id) or {}
    return out, checked.get("students") if out["result"] == "signed-in" else None


def record_profile(profile_id: str | None, before: list[str]) -> None:
    """Adds the profile signed in with to setup's record, unless it was there before setup."""
    if profile_id is not None and profile_id not in before:
        install_record.add("wilma-profile", profile_id)


def write_profile(path: Path, tenant: dict[str, Any], username: str, password: str,
                  students: Any = None) -> str:
    """Saves the profile as the CLI saves one after its own sign-in (saveLogin from 2.0): in place
    of the profile for the same account, the same Wilma and username in any case, keeping its id,
    its place and its two-step key, or else after the others, and the one its commands use first.
    `students` are the Kids the CLI listed for it, as it saves them. Returns its id."""
    config = _read(path)
    stored = _profile(config["profiles"], tenant, username, password, students)
    _put_in_place(config, stored)
    config["lastProfileId"] = stored["id"]
    _write_config(path, config)
    return stored["id"]


def _profile(listed: list[Any], tenant: dict[str, Any], username: str, password: str,
             students: Any) -> dict[str, Any]:
    """The profile the CLI would save for `username` at `tenant`, keeping the id, the two-step key
    and the last Kid of the one for the same account among `listed`, if any."""
    url = normalized(tenant["url"])
    probe = {"tenantUrl": url, "username": username}
    previous = next((p for p in listed if same_account(p, probe)), None)
    kids = [{"studentNumber": s["studentNumber"], "name": s["name"]} for s in students or []
            if isinstance(s, dict) and isinstance(s.get("studentNumber"), str)
            and isinstance(s.get("name"), str)]
    last = next((s for s in kids
                 if previous and s["studentNumber"] == previous.get("lastStudentNumber")),
                kids[0] if kids else None)
    return {
        "id": previous["id"] if previous and isinstance(previous.get("id"), str)
        else f"{url}|{username}",
        "tenantUrl": url,
        "tenantName": tenant["name"],
        "username": username,
        "passwordObfuscated": obfuscate(password),
        "totpSecretObfuscated": previous.get("totpSecretObfuscated") if previous else None,
        "students": kids,
        "lastStudentNumber": last["studentNumber"] if last else None,
        "lastStudentName": last["name"] if last else None,
        "lastUsedAt": datetime.now(timezone.utc).isoformat(timespec="milliseconds")
                              .replace("+00:00", "Z"),
    }


def same_account(a: Any, b: Any) -> bool:
    """Whether two profiles are one account, as the CLI tells (from 2.0): the same Wilma, and the
    same username in any case."""
    return isinstance(a, dict) and isinstance(b, dict) \
        and normalized(str(a.get("tenantUrl", ""))) == normalized(str(b.get("tenantUrl", ""))) \
        and str(a.get("username", "")).lower() == str(b.get("username", "")).lower()


def _put_in_place(config: dict[str, Any], stored: dict[str, Any]) -> None:
    """Puts `stored` in `config`'s profiles in place of the one for the same account, or after
    the others."""
    at = next((i for i, p in enumerate(config["profiles"]) if same_account(p, stored)), None)
    others = [p for p in config["profiles"] if not same_account(p, stored)]
    config["profiles"] = [*others, stored] if at is None else [*others[:at], stored, *others[at:]]


def normalized(url: str) -> str:
    """A Wilma address as the CLI compares two (from 2.0): its scheme and host in lower case,
    with no slash at the end."""
    parts = urlsplit(url.strip())
    if parts.scheme and parts.netloc:
        return f"{parts.scheme.lower()}://{parts.netloc.lower()}{parts.path}".rstrip("/")
    return url.strip().rstrip("/")


def profiles(path: Path) -> list[dict[str, Any]]:
    """The CLI's saved profiles, passwords included."""
    return [p for p in _read(path)["profiles"] if isinstance(p, dict)]


def for_the_household(profile: dict[str, Any], kids: set[str]) -> bool:
    """Whether the CLI's `profile` is a sign-in the Household's Brief depends on: one setup made,
    one the CLI saved one of `kids` for (their names as Wilma spells them, case folded), or one
    whose Kids aren't known. From 2.0 the CLI signs in with every profile, also one left from an
    earlier use of it, for none of the Kids."""
    if profile.get("id") in install_record.entries("wilma-profile"):
        return True
    names = [n.casefold() for n in student_names(profile)]
    return not names or not kids or any(n in kids for n in names)


def student_names(profile: dict[str, Any]) -> list[str]:
    """The Kids the CLI saved for its `profile`, as Wilma spells them: those it listed when it
    last signed in with it."""
    names = [s.get("name") for s in profile.get("students") or [] if isinstance(s, dict)]
    return [n for n in names if isinstance(n, str) and n]


def kids_alone(profile: dict[str, Any]) -> list[dict[str, Any]]:
    """The Kids the CLI lists signed in with the `profile` alone, as `check` does, so another
    sign-in that works can't answer for it. Raises WilmaError when it doesn't sign in."""
    with config_alone([profile]) as alone:
        return wilma.list_kids(alone)


def works_alone(profile: dict[str, Any]) -> str | None:
    """Why the CLI's saved `profile` doesn't sign in on its own, or None when it does."""
    try:
        kids_alone(profile)
    except wilma.WilmaError as e:
        return str(e)
    return None


def profile_ids(path: Path) -> list[str]:
    return [p["id"] for p in _read(path)["profiles"]
            if isinstance(p, dict) and isinstance(p.get("id"), str)]


def last_profile_id(path: Path) -> str | None:
    """The profile the CLI's commands use first: the one signed in with last."""
    last = _read(path).get("lastProfileId")
    return last if isinstance(last, str) else None


def profile(path: Path, profile_id: str) -> dict[str, Any] | None:
    """The CLI's saved profile with that id, password included, or None."""
    return next((p for p in _read(path)["profiles"]
                 if isinstance(p, dict) and p.get("id") == profile_id), None)


def remove_profile(path: Path, profile_id: str) -> None:
    """Removes one profile, with its password, and keeps the CLI's others, whose first its
    commands then start with. The Wilma sessions the CLI saved go too, as when it removes a sign-in
    itself, so none outlasts its password: the others sign in again. Without other profiles, the
    file goes, with the CLI's note of the newest version, and its folder when that is empty."""
    config = _read(path)
    config["profiles"] = [p for p in config["profiles"]
                          if not (isinstance(p, dict) and p.get("id") == profile_id)]
    path.with_name(SESSIONS).unlink(missing_ok=True)
    if not config["profiles"] and not config.get("keychainLogins"):
        _remove_config(path)
        return
    if config.get("lastProfileId") == profile_id:
        config["lastProfileId"] = next((p["id"] for p in config["profiles"] if isinstance(p, dict)
                                        and isinstance(p.get("id"), str)), None)
    _write_config(path, config)


def _remove_config(path: Path) -> None:
    """Removes the CLI's config `path`, which names no sign-in and no keychain entry, with the
    sessions and the note of the newest version the CLI keeps beside it, and their folder when that
    is empty."""
    for gone in (path, path.with_name(SESSIONS), path.with_name(VERSION_CHECK)):
        gone.unlink(missing_ok=True)
    with suppress(OSError):
        path.parent.rmdir()


def _read(path: Path) -> dict[str, Any]:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        config = {}
    if not (isinstance(config, dict) and isinstance(config.get("profiles"), list)):
        config = {"profiles": []}
    return config


def _write_config(path: Path, config: dict[str, Any]) -> None:
    _write(path, (json.dumps(config, indent=2, ensure_ascii=False) + "\n").encode())


def config_path() -> Path:
    """Where the wilma CLI keeps its config, found the way the CLI finds it."""
    if os.environ.get("WILMAI_CONFIG_PATH"):
        return Path(os.environ["WILMAI_CONFIG_PATH"])
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "wilmai" / "config.json"


def _write(path: Path, data: bytes) -> None:
    """Owner-only, as the CLI writes it, in one step, so the CLI never reads half of it."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.chmod(tmp, 0o600)
    tmp.replace(path)


# ---------------------------------------------------------------- setup's sign-ins

def signins() -> list[dict[str, Any]]:
    """Every sign-in the CLI reads Wilma with: the profiles in its own config, without the password
    of one whose password is in the keychain."""
    return profiles(config_path())


def signin(profile_id: str) -> dict[str, Any] | None:
    """The sign-in `signins` lists with that id, or None."""
    return next((p for p in signins() if p.get("id") == profile_id), None)


def last_signin_id() -> str | None:
    """The sign-in the CLI's commands use first: the one signed in with last."""
    last = last_profile_id(config_path())
    return last if signin(last or "") is not None else None


def save_signin(tenant: dict[str, Any], username: str, password: str, students: Any = None) -> str:
    """Saves the sign-in in the CLI's own config, as `write_profile` saves it, in place of the one
    for the same account, as the one its commands use first, then has the CLI move its password to
    the keychain (`secure`). Returns its id."""
    profile_id = write_profile(config_path(), tenant, username, password, students)
    _forget_legacy({"tenantUrl": tenant["url"], "username": username})
    secure()
    return profile_id


def remove_signin(profile_id: str) -> None:
    """Removes the sign-in `profile_id` from the CLI's config with its password. One whose password
    is in the keychain goes with the CLI's own `wilma accounts remove`, which deletes its keychain
    entry too; raises SignInNotRemoved when it couldn't, keeping the sign-in."""
    config = config_path()
    raw = _read(config)
    at = next((i for i, p in enumerate(raw["profiles"])
               if isinstance(p, dict) and p.get("id") == profile_id), None)
    if at is None:
        return
    if not (raw["profiles"][at].get("secrets") or (raw.get("keychainLogins") or {}).get(profile_id)):
        remove_profile(config, profile_id)
        return
    command = own_node.wilma()
    if command is None:
        raise SignInNotRemoved("its password is in the keychain, and the wilma CLI that removes it "
                               "isn't installed")
    try:
        proc = subprocess.run([*command, "accounts", "remove", str(at + 1), "--json"],
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=SECRETS_SECONDS, env=own_node.wilma_env(config),
                              stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise SignInNotRemoved(f"the wilma CLI didn't remove it: {e}") from None
    said = _json(proc.stdout)
    if proc.returncode != 0:
        raise SignInNotRemoved(failure.exited("wilma accounts remove", proc.returncode,
                                              proc.stderr, proc.stdout))
    if said.get("keychainEntryLeft"):
        raise SignInNotRemoved(f"its password couldn't be deleted from the keychain: "
                               f"{said['keychainEntryLeft']}")
    left = _read(config)
    if not left["profiles"] and not left.get("keychainLogins"):  # as remove_profile leaves it
        _remove_config(config)


class SignInNotRemoved(Exception):
    """A Wilma sign-in that couldn't be removed, with why."""


def secure() -> str:
    """Has the CLI keep the saved sign-ins' passwords in the system's keychain where there is one,
    and in its owner-only file where there isn't, such as a Linux server: `wilma config secrets
    auto` (from 2.5.0), which saves that setting, so the CLI also moves one saved later, and moves
    those saved before without signing in again. It's safe to run again: what is there already
    stays. Returns `secured`, `move-failed` (a sign-in stayed in the file, logged, and the CLI
    tries again later), `failed` (logged), or `not-run`: no CLI of that version, or no sign-in.
    Never raises: the sign-in still works from the file."""
    command = own_node.wilma()
    if command is None or not keeps_secrets() or not signins():
        return "not-run"
    try:
        proc = subprocess.run([*command, "config", "secrets", "auto", "--json"],
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=SECRETS_SECONDS, env=own_node.wilma_env(config_path()),
                              stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("The wilma CLI couldn't move the Wilma password to the keychain: %s", e)
        return "failed"
    if proc.returncode == 0:
        return "secured"
    said = _json(proc.stdout)
    if proc.returncode == MOVE_FAILED:
        for m in said.get("moved") or []:
            if isinstance(m, dict) and m.get("outcome") == "failed":
                log.warning("The Wilma password of %s at %s stays in the wilma CLI's file for now, "
                            "and the CLI tries again later: %s", m.get("username"), m.get("wilma"),
                            m.get("message"))
        return "move-failed"
    log.warning("The wilma CLI couldn't move the Wilma password to the keychain: %s",
                failure.exited("wilma config secrets", proc.returncode, proc.stderr, proc.stdout))
    return "failed"


def keeps_secrets() -> bool:
    """Whether the installed CLI can keep the passwords in the keychain (2.5.0 and later)."""
    version = installed_version() or ""
    try:
        return tuple(int(n) for n in version.split(".")[:3]) >= SECRETS_VERSION
    except ValueError:
        return False


def _json(text: str) -> dict[str, Any]:
    try:
        said = json.loads(text)
    except ValueError:
        return {}
    return said if isinstance(said, dict) else {}


def prepare() -> None:
    """Before a reading of Wilma, and on every update: moves setup's sign-ins from 0.11 to 0.15
    into the CLI's config (`move_legacy`), removes a run's config one of those versions left, and
    has the CLI keep the passwords in the keychain (`secure`) once that's new, since the CLI moves
    one saved later itself."""
    moved = move_legacy()
    runs = runs_folder()
    if runs.is_dir() and not runs.is_symlink():
        _remove_left_behind(runs)
    if moved or _read(config_path()).get("secretStore") not in ("auto", "keychain"):
        secure()


# ------------------------------------------------- setup's sign-ins from 0.11 to 0.15 (#710)

def signins_path() -> Path:
    """Setup's sign-ins from 0.11 to 0.15, as the CLI's config holds profiles but without their
    secrets, which were in the secret store."""
    return Path.home() / ".family" / "wilma-signins.json"


def secret_name(profile_id: str) -> str:
    """The name of the secret that held the password and two-step key of such a sign-in. A
    profile's id has the Wilma's address in it, which no store's name may have."""
    return "wilma-signin-" + hashlib.sha256(profile_id.encode()).hexdigest()[:16]


def move_legacy() -> bool:
    """Moves each of setup's sign-ins from 0.11 to 0.15 into the CLI's own config, with its
    password and two-step key from the secret store, in place of a sign-in there for the same
    account, then deletes its secret: the family doesn't sign in again. One the store doesn't hand
    over now stays, and is moved by a later run. One whose secret is gone can't work, and goes,
    logged: the family signs in again. Returns whether any moved."""
    path = signins_path()
    legacy = _read(path)
    if not legacy["profiles"]:
        return False
    config = config_path()
    cli = _read(config)
    last_before = profile(config, last_profile_id(config) or "")
    moved = False
    for p in [p for p in legacy["profiles"] if isinstance(p, dict)]:
        if not (isinstance(p.get("id"), str) and isinstance(p.get("tenantUrl"), str)
                and isinstance(p.get("username"), str)):
            continue
        try:
            raw = secrets.get(secret_name(p["id"]))
        except secrets.SecretsError as e:
            log.warning("The Wilma sign-in of %s stays where it is for now: the %s didn't hand over "
                        "its password: %s", p.get("username"), secrets.STORE, e)
            continue
        parts = (raw or "").split()
        if parts:
            _put_in_place(cli, {**{k: v for k, v in p.items() if k not in SECRET_FIELDS},
                                "passwordObfuscated": parts[0],
                                "totpSecretObfuscated": parts[1] if len(parts) > 1 else None})
            if p["id"] == legacy.get("lastProfileId") and str(p.get("lastUsedAt") or "") >= \
                    str((last_before or {}).get("lastUsedAt") or ""):
                cli["lastProfileId"] = p["id"]
            moved = True
        else:
            log.error("The %s has no Wilma password for %s: sign in to Wilma again",
                      secrets.STORE, p.get("username"))
        p["moved"] = True
    if not any(p.get("moved") for p in legacy["profiles"] if isinstance(p, dict)):
        return False
    if moved:
        if not cli.get("lastProfileId"):
            cli["lastProfileId"] = next((p["id"] for p in cli["profiles"] if isinstance(p, dict)
                                         and isinstance(p.get("id"), str)), None)
        _write_config(config, cli)  # before the secrets go, so a stop between loses nothing
    for p in [p for p in legacy["profiles"] if isinstance(p, dict) and p.pop("moved", False)]:
        _forget_legacy(p)
    return moved


def _forget_legacy(account: dict[str, Any]) -> None:
    """Deletes the sign-in from 0.11 to 0.15 for the same account as `account`, if there is one,
    with its secret; the file goes once it lists none."""
    path = signins_path()
    legacy = _read(path)
    gone = [p for p in legacy["profiles"] if same_account(p, account)]
    if not gone:
        return
    for p in gone:
        if isinstance(p.get("id"), str):
            try:
                secrets.delete(secret_name(p["id"]))
            except secrets.SecretsError as e:  # uninstall deletes it, as one setup stored
                install_record.add("keychain", secret_name(p["id"]))
                log.warning("The old copy of the Wilma password of %s stays in the %s until "
                            "uninstall: %s", p.get("username"), secrets.STORE, e)
    legacy["profiles"] = [p for p in legacy["profiles"] if not same_account(p, account)]
    if not legacy["profiles"]:
        path.unlink(missing_ok=True)
        return
    if legacy.get("lastProfileId") in {p.get("id") for p in gone}:
        legacy["lastProfileId"] = None
    _write_config(path, legacy)


def remove_legacy(profile_id: str) -> None:
    """Removes the sign-in `profile_id` from 0.11 to 0.15 that no run has moved yet, as uninstall
    does: its secret from the store, then the rest. Raises secrets.SecretsError when the store
    didn't let the secret go, keeping the sign-in."""
    secrets.delete(secret_name(profile_id))
    path = signins_path()
    legacy = _read(path)
    legacy["profiles"] = [p for p in legacy["profiles"]
                          if not (isinstance(p, dict) and p.get("id") == profile_id)]
    if not legacy["profiles"]:
        path.unlink(missing_ok=True)
        return
    _write_config(path, legacy)


# ---------------------------------------------------------------- a check of one sign-in alone

@contextmanager
def config_alone(listed: list[dict[str, Any]]) -> Iterator[Path]:
    """A config of the CLI's with only the sign-ins `listed`, the first signed in with last, at the
    path given for the block, so a sign-in is checked alone: from 2.0 the CLI's commands sign in
    with every profile in its config. It's in an owner-only folder of its own (`run_folder`), which
    takes the Wilma session the CLI saves beside it and goes when the block ends. A sign-in whose
    password is in the keychain names its entry, which the CLI reads from there."""
    last = listed[0].get("id") if listed else None
    with run_folder() as folder:
        path = folder / "config.json"
        _write_config(path, {"profiles": listed, "lastProfileId": last})
        yield path


def runs_folder() -> Path:
    """Where each check of a sign-in alone gets a folder; from 0.11 to 0.15 each reading did too."""
    return Path.home() / ".family" / "wilma-runs"


@contextmanager
def run_folder() -> Iterator[Path]:
    """An owner-only folder of its own for a check of a sign-in alone (`config_alone`), in
    ~/.family/wilma-runs, removed when the block ends, also by an error or a stop (SIGTERM). One
    left by a check killed outright is removed by the next check or reading: the folder's RUN_LOCK,
    locked while the check lasts, tells them apart from another check's."""
    runs = runs_folder()
    runs.mkdir(mode=0o700, parents=True, exist_ok=True)
    _remove_left_behind(runs)
    folder = Path(tempfile.mkdtemp(prefix="run-", dir=runs))  # owner-only
    lock = open(folder / RUN_LOCK, "a", encoding="utf-8")
    try:
        run_lock.lock_file(lock)
        with _stopping_ends_the_block():
            yield folder
    finally:
        lock.close()
        shutil.rmtree(folder, ignore_errors=True)


def _remove_left_behind(runs: Path) -> None:
    """Removes the run folders in `runs` whose run has ended without removing them."""
    for folder in runs.iterdir():
        if not folder.is_dir() or folder.is_symlink():
            continue
        ended = False
        try:
            with open(folder / RUN_LOCK, "r+", encoding="utf-8") as lock:
                ended = run_lock.lock_file(lock)
        except FileNotFoundError:  # being made, or made by a run killed right then
            with suppress(OSError):
                ended = time.time() - folder.stat().st_mtime > UNLOCKED_SECONDS
        except OSError:
            continue
        if ended:
            shutil.rmtree(folder, ignore_errors=True)


@contextmanager
def _stopping_ends_the_block() -> Iterator[None]:
    """Python skips `finally` on SIGTERM (and SIGHUP), so while the block runs they end the program
    as an error would, and the block's cleanup runs. Only the main thread can catch them, so the
    setup page's reads, from its own threads, leave them be."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    stops = [getattr(signal, name) for name in ("SIGTERM", "SIGHUP") if hasattr(signal, name)]

    def stop(signum: int, _frame: Any) -> None:
        raise SystemExit(128 + signum)
    before = {s: signal.signal(s, stop) for s in stops}
    try:
        yield
    finally:
        for s, handler in before.items():
            signal.signal(s, signal.SIG_DFL if handler is None else handler)  # None: set outside Python


# ---------------------------------------------------------------- the Terminal sign-in window

def sign_in_in_terminal(language: str, timeout: int, *, no_open: bool = False) -> dict[str, Any]:
    """The wilma CLI signs in only on its own interactive screen, so this runs that screen in a
    Terminal window and waits for its result: the CLI saves its config once the sign-in has
    worked, and the window's script records the screen's exit status when it closes. The
    password stays between the family and the CLI; this reads only the Wilma address from the
    CLI's config. Returns the step's outcome as `setup wilma` prints it; the setup page offers
    this window when its own sign-in fails for any reason but a wrong password (ADR 0008).
    Without the CLI it installs the pinned one first, as the page does. The CLI it installed
    and a profile new to the CLI go in setup's record."""
    again = f"{program()} setup wilma"
    got = install()
    if got["result"] == "no-node":
        return outcome("not-installed", "Parent Recap's own Node, which the wilma CLI runs on, "
                       "is missing. The family pastes the Parent Recap install line into "
                       f"Terminal again, then run: {again}")
    if got["result"] != "installed":
        return outcome("not-installed", "The wilma CLI didn't install. The family checks that "
                       f"the Mac is online, then run: {again}", reason=got["reason"])
    config = config_path()
    profiles_before = profile_ids(config)
    with tempfile.TemporaryDirectory(prefix="parent-recap-wilma-") as tmp:
        status = Path(tmp) / "exit-status"
        seen = opened = _mtime(config)
        if not no_open:
            if not desktop.open_terminal(sign_in_window(language, status), {}, "Wilma sign-in"):
                return outcome("no-terminal", "Terminal didn't open. The family runs wilma in "
                               f"Terminal and signs in there, then run: {again} --no-open")

        polls = max(1, math.ceil(timeout / WILMA_POLL_SECONDS))
        check_now = no_open  # already signed in, perhaps
        for poll in range(polls + 1):
            exit_status = _exit_status(status)
            config_mtime = _mtime(config)
            if check_now or config_mtime != seen or exit_status is not None:
                check_now, seen = False, config_mtime
                # The sign-in the CLI saved last, checked alone: with another Wilma signed in to
                # already (#647), that one would answer for a window closed without signing in.
                # From the window, it counts only once the CLI has saved it since it opened.
                last = profile(config, last_profile_id(config) or "")
                kids = None
                if last is not None and (no_open or config_mtime != opened):
                    try:
                        kids = kids_alone(last)
                    except wilma.WilmaError:
                        pass
                if kids is not None:
                    record_profile(last_profile_id(config), profiles_before)
                    address = _wilma_address(config)
                    secure()  # the password the CLI saved, into the keychain
                    return outcome("signed-in", city=WILMA_CITIES.get(address or ""),
                                   wilma_address=address, kids=kids)
                if exit_status == 0:
                    return outcome("not-signed-in", "The Wilma screen was closed before signing "
                                   f"in. Run this again when the family is ready: {again}")
                if exit_status is not None:
                    why = failure.exited("The Wilma screen", exit_status, None, None)
                    return outcome("sign-in-failed", "Wilma didn't sign in. The family checks "
                                   "the city and the parent account's username and password, "
                                   f"then signs in again: {again}",
                                   reason=failure.logged("Wilma didn't sign in", why))
            if poll < polls:
                time.sleep(WILMA_POLL_SECONDS)
    return outcome("timeout", f"The family didn't sign in to Wilma within {timeout} "
                   "seconds. If the Wilma window is still open, they finish there, then run: "
                   f"{again} --no-open. Otherwise run: {again}")


def sign_in_window(language: str, status: Path) -> list[str]:
    """The Wilma window's command, which records at `status` how its screen ended. This Python,
    since the window's PATH may not lead to this program."""
    return [sys.executable, "-m", "family_brief", "setup", "wilma", "--screen",
            "--language", language, "--status", str(status)]


def sign_in_screen(language: str, status: Path | None) -> int:
    """In the window: the CLI's sign-in under the guide in `language`. Returns 0 once signed in,
    otherwise the CLI's exit status, and records it at `status` for the step waiting on it."""
    exit_status = 1
    try:
        if system_profile.current().pseudo_terminal:
            exit_status = _sign_in_terminal_screen(language)
        else:
            exit_status = _sign_in_page(language)
    finally:
        if status is not None:
            with suppress(OSError):  # the step stopped waiting and took its folder away
                status.write_text(f"{exit_status}\n", encoding="utf-8")
    return exit_status


def _sign_in_page(language: str) -> int:
    """On Windows, which has no pseudo-terminal for the CLI's own screens: runs the wilma CLI's
    sign-in page (`wilma login`, from 2.1), which the CLI opens in the browser, on Parent Recap's
    own Node under the guide in this window. Once the CLI has saved its profile it's ended, before
    it waits for more Wilmas. Ctrl+C ends it too. Returns 0 once signed in, otherwise the CLI's
    exit status."""
    text = WILMA_WINDOW.get(language.lower(), WILMA_WINDOW["en"])
    guide = [text["guide"][0], text["page"], *text["guide"][2:]]
    print("\n".join(guide) + "\n", flush=True)
    config = config_path()
    seen = _mtime(config)
    command = own_node.wilma()
    if command is None:
        return 127

    def saved() -> bool:  # the CLI renames its config into place, so it reads whole
        return _mtime(config) != seen and _wilma_address(config) is not None
    cli = subprocess.Popen([*command, "login"], env=own_node.wilma_env())
    try:
        while not saved() and cli.poll() is None:
            time.sleep(WILMA_PAGE_POLL_SECONDS)
    except KeyboardInterrupt:  # the CLI has it too, and ends
        pass
    signed_in = saved()
    if cli.poll() is None:
        cli.terminate()
    exit_status = cli.wait()
    print(f"\n{text['signed_in']} {text['close']}" if signed_in else f"\n{text['close']}",
          flush=True)
    return 0 if signed_in else exit_status


def _sign_in_terminal_screen(language: str) -> int:
    """Runs the wilma CLI's sign-in on Parent Recap's own Node in this window, through a
    pseudo-terminal so this sees what it shows: the guide stays on top of each question the CLI
    clears the screen for, the student picker is answered (setup reads every Kid, so which one
    doesn't matter) and hidden, and once the CLI has saved its profile it's ended, before its
    menu, whose default choice fails with a 403 for some accounts. Returns 0 once signed in,
    otherwise the CLI's exit status."""
    text = WILMA_WINDOW.get(language.lower(), WILMA_WINDOW["en"])
    guide = "\r\n".join(text["guide"]).encode() + b"\r\n\r\n"
    out = sys.stdout.buffer
    out.write(guide)
    out.flush()
    config = config_path()
    seen = _mtime(config)
    command = own_node.wilma()
    if command is None:
        return 127
    env = own_node.wilma_env()  # before the fork: the child only runs the CLI
    pid, fd = pty.fork()
    if pid == 0:
        try:
            os.execve(command[0], command, env)
        finally:
            os._exit(127)
    # The CLI truncates its config before writing it, so a new one counts once it reads whole.
    signed_in = _show_sign_in(
        fd, guide, lambda: _mtime(config) != seen and _wilma_address(config) is not None)
    if signed_in:
        os.kill(pid, signal.SIGTERM)
    # Closed before the wait, since nothing reads the CLI any more: macOS may not let it finish
    # exiting while its terminal holds output nobody has read, such as the menu it shows just as
    # it's ended (#216).
    os.close(fd)
    status = os.waitstatus_to_exitcode(os.waitpid(pid, 0)[1])
    if signed_in:
        out.write(b"\x1b[H\x1b[2J\x1b[3J")  # the screen and the scrollback
        out.write(f"{text['signed_in']} {text['close']}\n".encode())
    else:
        out.write(f"\n{text['close']}\n".encode())
    out.flush()
    return 0 if signed_in else status


def _show_sign_in(fd: int, guide: bytes, saved) -> bool:
    """Passes the family's typing to the CLI on `fd` and what it shows to the window, until the
    CLI has saved its profile (True) or has ended without (False)."""
    stdin, out = sys.stdin.fileno(), sys.stdout.buffer
    terminal = os.isatty(stdin)
    if terminal:
        def same_size(*_a: Any) -> None:
            fcntl.ioctl(fd, termios.TIOCSWINSZ, fcntl.ioctl(stdin, termios.TIOCGWINSZ, b"\0" * 8))
        same_size()
        signal.signal(signal.SIGWINCH, same_size)
        before = termios.tcgetattr(stdin)
        tty.setraw(stdin)
    reading, hidden, last = [fd, stdin], False, b""
    try:
        while not saved():
            ready = select.select(reading, [], [], 0.1)[0]
            if stdin in ready:
                typed = os.read(stdin, 1024)
                if typed:
                    os.write(fd, typed)
                else:
                    reading.remove(stdin)
            if fd in ready:
                try:
                    shown = os.read(fd, 65536)
                except OSError:  # the CLI has ended
                    shown = b""
                if not shown:
                    return saved()
                recent, last = last[-len(WILMA_MENU):] + shown, shown  # a question split in two
                if not hidden and WILMA_STUDENT_PICKER in recent:
                    os.write(fd, b"\r")  # once: the CLI shows the question again with the answer
                hidden = hidden or WILMA_STUDENT_PICKER in recent or WILMA_MENU in recent
                if not hidden:
                    out.write(shown.replace(NODE_CLEAR, NODE_CLEAR + guide))
                    out.flush()
        return True
    finally:
        if terminal:
            termios.tcsetattr(stdin, termios.TCSAFLUSH, before)
            signal.signal(signal.SIGWINCH, signal.SIG_DFL)


def _mtime(path: Path) -> int | None:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def _exit_status(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _wilma_address(config: Path) -> str | None:
    """The host of the Wilma the family signed in to last, such as espoo.inschool.fi."""
    try:
        data = json.loads(config.read_text(encoding="utf-8"))
        last = next(p for p in data["profiles"] if p.get("id") == data.get("lastProfileId"))
        url = str(last["tenantUrl"])
    except (OSError, ValueError, KeyError, TypeError, StopIteration):
        return None
    return urlsplit(url if "//" in url else f"//{url}").hostname


def main(argv: list[str]) -> int:
    """`python -m family_brief.setup_wilma update`, which install.sh runs. It says what changed,
    if anything, and never fails the install: a CLI that couldn't be updated keeps working. Then
    the sign-ins are made ready for the CLI (`prepare`): those of 0.11 to 0.15 moved into its
    config, and the passwords into the keychain.
    `install` installs the CLI whether or not Wilma was connected, as the gateway's setup does
    for the program every Hosted Household runs (ADR 0035), and fails when it couldn't."""
    if argv == ["install"]:
        got = install()
        if got["result"] != "installed":
            print(f"❌ The wilma CLI couldn't be installed: {got.get('reason') or got['result']}",
                  file=sys.stderr)
            return 1
        print(f"✅ The wilma CLI {WILMA_CLI_VERSION} is installed.")
        return 0
    if argv != ["update"]:
        print("Usage: python -m family_brief.setup_wilma update|install", file=sys.stderr)
        return 2
    private_files.restrict_new_files()  # install.sh runs this with the shell's umask
    try:
        result = update()
    except Exception:  # noqa: BLE001 - the install goes on, and the next one tries again
        result = "update-failed"
    said = {"updated": f"✅ The wilma CLI is updated to {WILMA_CLI_VERSION}.",
            "update-failed": f"⚠️  The wilma CLI couldn't be updated to {WILMA_CLI_VERSION}, so "
                             "Wilma is read with the one already there. Run the install again "
                             "later.",
            "no-node": f"⚠️  The wilma CLI couldn't be updated to {WILMA_CLI_VERSION}: Parent "
                       "Recap's own Node is missing. Run the install again."}.get(result)
    if said:
        print(said)
    try:
        prepare()
    except Exception as e:  # noqa: BLE001 - each reading of Wilma tries again
        log.warning("The Wilma sign-ins weren't made ready for the wilma CLI: %s", e)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
