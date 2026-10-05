"""The setup page's Wilma sign-in (ADR 0008): the town list, the wilma CLI's profile, and the check.

The wilma CLI signs in only on its own interactive screen, but every command reads its saved
profile from one JSON file. So the page asks for the town and the Wilma username and password
itself, this writes that profile the way the CLI does after its own sign-in, and the CLI's Kid
list checks that it works. The format isn't documented, so setup installs the CLI version below,
and tests/test_setup_page.py checks the profile written against that version's way of reading it.

The town list is Wilma's public tenant list, the copy the CLI ships inside its wilma-client.
The password goes only into the CLI's own owner-only file, lightly encoded as the CLI keeps it,
never onto a command line, into a log or back to the page.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import install_record
from .collectors import wilma

WILMA_CLI_VERSION = "1.6.2"
PACKAGE = "@wilm-ai/wilma-cli"
INSTALL_ARGS = ["install", "-g", f"{PACKAGE}@{WILMA_CLI_VERSION}"]
NODE_INSTALL = "brew install node"
INSTALL_SECONDS = 300
# Where Homebrew puts npm, for a server started without Homebrew on its PATH.
NPM_PLACES = ("/opt/homebrew/bin/npm", "/usr/local/bin/npm")
# What the CLI says when Wilma turns the username and password down, and nothing else does.
WRONG_PASSWORD = "Wilma login failed"
SALT = "wilmai::"  # the CLI's own, in front of the password before it's Base64-encoded
MIN_QUERY = 2
MAX_TOWNS = 20


def installed() -> bool:
    return shutil.which(wilma.WILMA) is not None


def install() -> str:
    """Installs the pinned wilma CLI with npm unless one is installed, and adds it to setup's
    record. Returns `installed`, `no-npm` (Node isn't on this Mac) or `install-failed`."""
    if installed():
        return "installed"
    program = npm()
    if program is None:
        return "no-npm"
    try:
        proc = subprocess.run([program, *INSTALL_ARGS], capture_output=True, text=True,
                              timeout=INSTALL_SECONDS, stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        return "no-npm"
    except (OSError, subprocess.SubprocessError):
        return "install-failed"
    if proc.returncode != 0 or not installed():
        return "install-failed"
    install_record.add("wilma-cli", PACKAGE)
    return "installed"


def npm() -> str | None:
    return shutil.which("npm") or next((p for p in NPM_PLACES if os.access(p, os.X_OK)), None)


def uninstall() -> bool:
    """Removes the wilma CLI setup installed, with npm. Returns whether npm did."""
    program = npm()
    if program is None:
        return False
    try:
        proc = subprocess.run([program, "uninstall", "-g", PACKAGE], capture_output=True,
                              text=True, timeout=INSTALL_SECONDS, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def tenants() -> list[dict[str, Any]] | None:
    """Each Wilma in the tenant list the installed CLI ships, as its address, its name and its
    towns (Finnish, Swedish), or None without the CLI or the list."""
    program = shutil.which(wilma.WILMA)
    if program is None:
        return None
    package = Path(os.path.realpath(program)).parent.parent  # past dist/index.js
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
    """Writes the CLI's profile for `username` at the Wilma `tenant` and checks it with the CLI's
    Kid list. Returns `signed-in` with the Kids, or `wrong-password`, `sign-in-failed`, `no-kids`
    or `not-installed`. Unless signed in, the CLI's config is put back as it was, so a wrong
    password isn't kept and an earlier sign-in still works. A new profile goes in setup's record."""
    if not installed():
        return "not-installed", []
    path = config_path()
    try:
        before: bytes | None = path.read_bytes()
    except OSError:
        before = None
    profiles_before = profile_ids(path)
    profile_id = write_profile(path, tenant, username, password)
    result, kids = "sign-in-failed", []
    try:
        kids = [k for k in wilma.list_kids() if isinstance(k.get("name"), str) and k["name"]]
        result = "signed-in" if kids else "no-kids"  # no Kids: not a guardian's account
    except wilma.WilmaError as e:
        result = "wrong-password" if WRONG_PASSWORD in str(e) else "sign-in-failed"
    finally:  # also when the CLI fails in a way it doesn't report
        if result != "signed-in":
            _put_back(path, before)
    if result != "signed-in":
        return result, []
    record_profile(profile_id, profiles_before)
    return result, kids


def record_profile(profile_id: str | None, before: list[str]) -> None:
    """Adds the profile signed in with to setup's record, unless it was there before setup."""
    if profile_id is not None and profile_id not in before:
        install_record.add("wilma-profile", profile_id)


def write_profile(path: Path, tenant: dict[str, Any], username: str, password: str) -> str:
    """Saves the profile as the CLI saves one after its own sign-in: added after the others, in
    place of one for the same Wilma and username, and the one its commands use. Returns its id."""
    config = _read(path)
    stored = {
        "id": f"{tenant['url']}|{username}",
        "tenantUrl": tenant["url"],
        "tenantName": tenant["name"],
        "username": username,
        "passwordObfuscated": obfuscate(password),
        "students": [],  # the CLI's Kid list fills them in
        "lastStudentNumber": None,
        "lastStudentName": None,
        "lastUsedAt": datetime.now(timezone.utc).isoformat(timespec="milliseconds")
                              .replace("+00:00", "Z"),
    }
    config["profiles"] = [p for p in config["profiles"]
                          if not (isinstance(p, dict) and p.get("id") == stored["id"])] + [stored]
    config["lastProfileId"] = stored["id"]
    _write_config(path, config)
    return stored["id"]


def profile_ids(path: Path) -> list[str]:
    return [p["id"] for p in _read(path)["profiles"]
            if isinstance(p, dict) and isinstance(p.get("id"), str)]


def last_profile_id(path: Path) -> str | None:
    """The profile the CLI's commands use: the one signed in with last."""
    last = _read(path).get("lastProfileId")
    return last if isinstance(last, str) else None


def profile(path: Path, profile_id: str) -> dict[str, Any] | None:
    """The CLI's saved profile with that id, password included, or None."""
    return next((p for p in _read(path)["profiles"]
                 if isinstance(p, dict) and p.get("id") == profile_id), None)


def remove_profile(path: Path, profile_id: str) -> None:
    """Removes one profile, with its password, keeping the CLI's others; the CLI's commands then
    use the last of them. Without others, the file goes, and its folder when that is empty."""
    config = _read(path)
    config["profiles"] = [p for p in config["profiles"]
                          if not (isinstance(p, dict) and p.get("id") == profile_id)]
    if not config["profiles"]:
        path.unlink(missing_ok=True)
        try:
            path.parent.rmdir()
        except OSError:
            pass
        return
    if config.get("lastProfileId") == profile_id:
        others = profile_ids(path)
        config["lastProfileId"] = next((p for p in reversed(others) if p != profile_id), None)
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


def _put_back(path: Path, before: bytes | None) -> None:
    if before is None:
        path.unlink(missing_ok=True)
    else:
        _write(path, before)


def _write(path: Path, data: bytes) -> None:
    """Owner-only, as the CLI writes it, in one step, so the CLI never reads half of it."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.chmod(tmp, 0o600)
    tmp.replace(path)
