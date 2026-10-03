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

from .collectors import wilma

WILMA_CLI_VERSION = "1.6.2"
INSTALL_ARGS = ["install", "-g", f"@wilm-ai/wilma-cli@{WILMA_CLI_VERSION}"]
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
    """Installs the pinned wilma CLI with npm unless one is installed. Returns `installed`,
    `no-npm` (Node isn't on this Mac) or `install-failed`."""
    if installed():
        return "installed"
    npm = shutil.which("npm") or next((p for p in NPM_PLACES if os.access(p, os.X_OK)), None)
    if npm is None:
        return "no-npm"
    try:
        proc = subprocess.run([npm, *INSTALL_ARGS], capture_output=True, text=True,
                              timeout=INSTALL_SECONDS, stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        return "no-npm"
    except (OSError, subprocess.SubprocessError):
        return "install-failed"
    return "installed" if proc.returncode == 0 and installed() else "install-failed"


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
    password isn't kept and an earlier sign-in still works."""
    if not installed():
        return "not-installed", []
    path = config_path()
    try:
        before: bytes | None = path.read_bytes()
    except OSError:
        before = None
    write_profile(path, tenant, username, password)
    result, kids = "sign-in-failed", []
    try:
        kids = [k for k in wilma.list_kids() if isinstance(k.get("name"), str) and k["name"]]
        result = "signed-in" if kids else "no-kids"  # no Kids: not a guardian's account
    except wilma.WilmaError as e:
        result = "wrong-password" if WRONG_PASSWORD in str(e) else "sign-in-failed"
    finally:  # also when the CLI fails in a way it doesn't report
        if result != "signed-in":
            _put_back(path, before)
    return result, kids if result == "signed-in" else []


def write_profile(path: Path, tenant: dict[str, Any], username: str, password: str) -> None:
    """Saves the profile as the CLI saves one after its own sign-in: added after the others, in
    place of one for the same Wilma and username, and the one its commands use."""
    try:
        config = json.loads(path.read_text())
    except (OSError, ValueError):
        config = {}
    if not (isinstance(config, dict) and isinstance(config.get("profiles"), list)):
        config = {"profiles": []}
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
