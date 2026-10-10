"""The setup page's Wilma sign-in (ADR 0008): the town list, the wilma CLI's profile, and the check.

The wilma CLI signs in on screens of its own, but every command reads its saved profiles from
one JSON file. So the page asks for the town and the Wilma username and password
itself, this writes that profile the way the CLI does after its own sign-in, and the CLI's Kid
list checks that it works. The format isn't documented, so setup installs the CLI version below,
install.sh moves an installed one to it (update()), and tests/test_setup_page.py checks the
profile written against that version's way of reading it.

The CLI is installed and run with Parent Recap's own Node, in its own folder (`own_node`, ADR 0011).
The town list is Wilma's public tenant list, the copy the CLI ships inside its wilma-client.
The password goes only into the CLI's own owner-only file, lightly encoded as the CLI keeps it,
and for the check into an owner-only copy of it that goes right after, never onto a command
line, into a log or back to the page.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from . import install_record, own_node, private_files
from .collectors import wilma

WILMA_CLI_VERSION = "2.1.2"
PACKAGE = "@wilm-ai/wilma-cli"
INSTALL_SECONDS = 300
SALT = "wilmai::"  # the CLI's own, in front of the password before it's Base64-encoded
# What the CLI keeps next to its config: the Wilma sessions its commands carry on with (from 2.0),
# and its note of the newest version on npm.
SESSIONS = "wilmai-sessions.json"
VERSION_CHECK = "version-check.json"
MIN_QUERY = 2
MAX_TOWNS = 20


def installed() -> bool:
    return own_node.wilma() is not None


def installed_version(folder: Path | None = None) -> str | None:
    """The version of the wilma CLI in `folder` (Parent Recap's own, unless given), as its package
    says, or None."""
    package = (folder or own_node.wilma_folder()) / "lib" / "node_modules" / PACKAGE / "package.json"
    try:
        version = json.loads(package.read_text()).get("version")
    except (OSError, ValueError, AttributeError):
        return None
    return version if isinstance(version, str) else None


def install() -> str:
    """Installs the pinned wilma CLI with Parent Recap's own Node into its folder, unless it's
    there, and adds that folder to setup's record. Returns `installed`, `no-node` (the install
    didn't leave Parent Recap's own Node) or `install-failed`."""
    if installed():
        return "installed"
    npm = own_node.npm()
    if npm is None:
        return "no-node"
    # Before npm runs, so uninstall removes the folder however this install ends.
    install_record.add("wilma-cli", str(own_node.wilma_folder()))
    return "installed" if _npm_install(npm) else "install-failed"


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
    return "updated" if _npm_install(npm) else "update-failed"


def _half_there() -> bool:
    """Whether the CLI's folder is there, or setup's record has it, without a CLI that runs, while
    the CLI's config has a sign-in: Wilma was connected, and the CLI broke."""
    folder = own_node.wilma_folder()
    return (folder.exists() or str(folder) in install_record.entries("wilma-cli")) \
        and bool(profile_ids(config_path()))


def _npm_install(npm: list[str]) -> bool:
    """Whether `npm`, on Parent Recap's own Node, installed the pinned CLI. It installs into a new
    folder next to the CLI's, which takes the CLI's place only once npm has finished and left the
    pinned version, so an npm that fails, or is ended at the time limit, changes nothing."""
    folder = own_node.wilma_folder()
    folder.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    for left in folder.parent.glob(f".{folder.name}-*"):  # by an install that was itself ended
        shutil.rmtree(left, ignore_errors=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{folder.name}-", dir=folder.parent))
    try:
        # npm's cache in a temporary folder, so nothing is left outside Parent Recap's.
        with tempfile.TemporaryDirectory(prefix="parent-recap-npm-") as cache:
            # npm runs package scripts with the `node` its PATH finds: this one, not the Mac's.
            env = {**os.environ, "npm_config_cache": cache, "npm_config_update_notifier": "false",
                   "PATH": os.pathsep.join([str(Path(npm[0]).parent), os.environ.get("PATH", "")])}
            proc = subprocess.run([*npm, *install_args(staging)], capture_output=True, text=True,
                                  timeout=INSTALL_SECONDS, stdin=subprocess.DEVNULL, env=env)
        if proc.returncode != 0 or installed_version(staging) != WILMA_CLI_VERSION \
                or not (staging / "bin" / "wilma").is_file():
            return False
        _replace(folder, staging)
        return True
    except (OSError, subprocess.SubprocessError):
        return False
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _replace(folder: Path, new: Path) -> None:
    """Puts the CLI npm installed in `new` in `folder`'s place, and removes the one that was there.
    npm links bin/wilma to the package by a relative path, so the CLI runs from its new place."""
    old = new.with_name(new.name + "-old")
    if folder.exists():
        folder.rename(old)
    try:
        new.rename(folder)
    except OSError:
        if old.exists():
            old.rename(folder)
        raise
    shutil.rmtree(old, ignore_errors=True)


def install_args(folder: Path) -> list[str]:
    """npm's arguments that install the pinned CLI (ADR 0008) into `folder`, laid out as a global
    install: `bin/wilma`, and the package in `lib/node_modules`. `_npm_install` gives a new folder,
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
            listed = json.loads(path.read_text())["wilmat"]
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
    the town it's for: the one found, or the entry's first."""
    needle = _fold(query)
    if len(needle) < MIN_QUERY:
        return []
    by_town, by_name = [], []
    for t in listed:
        town = next((fi for fi, sv in t["towns"] if needle in _fold(fi) or needle in _fold(sv)),
                    None)
        if town is not None:
            by_town.append({"url": t["url"], "name": t["name"], "town": town})
        elif needle in _fold(t["name"]):
            by_name.append({"url": t["url"], "name": t["name"],
                            "town": t["towns"][0][0] if t["towns"] else None})
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


def sign_in(tenant: dict[str, Any], username: str, password: str) -> tuple[str, list[dict[str, Any]]]:
    """Writes the CLI's profile for `username` at the Wilma `tenant`, checks it with the CLI's Kid
    list, and once that works saves it in the CLI's config. Returns `signed-in` with the Kids, or
    `wrong-password`, `sign-in-failed`, `no-kids` or `not-installed`. Unless signed in, the CLI's
    config stays as it was, so a wrong password isn't kept and an earlier sign-in still works. A
    new profile goes in setup's record."""
    if not installed():
        return "not-installed", []
    # From 2.0 the CLI's commands read every profile in its config, so an earlier one that works
    # would hide a wrong password. The new one is checked alone, in a config of its own in an
    # owner-only folder, which also takes the session the CLI saves next to it and goes with it.
    with tempfile.TemporaryDirectory(prefix="parent-recap-wilma-") as folder:
        alone = Path(folder) / "config.json"
        checked_id = write_profile(alone, tenant, username, password)
        result, kids = "sign-in-failed", []
        try:
            kids = [k for k in wilma.list_kids(alone) if isinstance(k.get("name"), str) and k["name"]]
            result = "signed-in" if kids else "no-kids"  # no Kids: not a guardian's account
        except wilma.WilmaError as e:
            result = "wrong-password" if e.wrong_password else "sign-in-failed"
        checked = profile(alone, checked_id) or {}
    if result != "signed-in":
        return result, []
    path = config_path()
    profiles_before = profile_ids(path)
    # With the Kids the CLI saved in the profile it checked.
    profile_id = write_profile(path, tenant, username, password, checked.get("students"))
    record_profile(profile_id, profiles_before)
    return result, kids


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
    url = normalized(tenant["url"])

    def same(p: Any) -> bool:
        return isinstance(p, dict) and normalized(str(p.get("tenantUrl", ""))) == url \
            and str(p.get("username", "")).lower() == username.lower()
    at = next((i for i, p in enumerate(config["profiles"]) if same(p)), None)
    previous = config["profiles"][at] if at is not None else None
    kids = [{"studentNumber": s["studentNumber"], "name": s["name"]} for s in students or []
            if isinstance(s, dict) and isinstance(s.get("studentNumber"), str)
            and isinstance(s.get("name"), str)]
    last = next((s for s in kids
                 if previous and s["studentNumber"] == previous.get("lastStudentNumber")),
                kids[0] if kids else None)
    stored = {
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
    others = [p for p in config["profiles"] if not same(p)]
    config["profiles"] = [*others, stored] if at is None else [*others[:at], stored, *others[at:]]
    config["lastProfileId"] = stored["id"]
    _write_config(path, config)
    return stored["id"]


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
    names = [s.get("name") for s in profile.get("students") or [] if isinstance(s, dict)]
    names = [n.casefold() for n in names if isinstance(n, str) and n]
    return not names or not kids or any(n in kids for n in names)


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
    if not config["profiles"]:
        path.unlink(missing_ok=True)
        path.with_name(VERSION_CHECK).unlink(missing_ok=True)
        try:
            path.parent.rmdir()
        except OSError:
            pass
        return
    if config.get("lastProfileId") == profile_id:
        config["lastProfileId"] = next((p["id"] for p in config["profiles"] if isinstance(p, dict)
                                        and isinstance(p.get("id"), str)), None)
    _write_config(path, config)


def _read(path: Path) -> dict[str, Any]:
    try:
        config = json.loads(path.read_text())
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


def main(argv: list[str]) -> int:
    """`python -m family_brief.setup_wilma update`, which install.sh runs. It says what changed,
    if anything, and never fails the install: a CLI that couldn't be updated keeps working."""
    if argv != ["update"]:
        print("Usage: python -m family_brief.setup_wilma update", file=sys.stderr)
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
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
