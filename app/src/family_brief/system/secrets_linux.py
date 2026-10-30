"""The Linux adapter of `secrets`: one file per secret in `~/.family/secrets`, the Household's
config folder, which only its own user may read (ADR 0030). A server has no Keychain or Credential
Manager, and libsecret needs a desktop session the evening job's user doesn't have.

The files get the protection the wilma CLI's sign-in already has, no more: whoever reads the disk
as root reads them too. Each secret is written whole to a new file and then moved into place, so a
run cut off halfway leaves the old secret, never half of a new one."""
from __future__ import annotations

import contextlib
import getpass
import os
import re
from pathlib import Path

from .secrets_error import SecretsError

STORE = "owner-only file"
DELETING_ASKS = ""  # a file is deleted without asking


def _folder() -> Path:
    return Path.home() / ".family" / "secrets"


def _path(key: str) -> Path:
    # The names are Parent Recap's own; one that would leave the folder is a bug, not a secret.
    if not re.fullmatch(r"[A-Za-z0-9_.@+-]+", key) or key.strip(".") == "":
        raise ValueError(f"not a secret's name: {key!r}")
    return _folder() / key


def get(key: str) -> str | None:
    try:
        return _path(key).read_text(encoding="utf-8").rstrip("\n") or None
    except FileNotFoundError:
        return None
    except OSError as e:
        raise SecretsError(f"{key} couldn't be read from {_path(key)}: {e.strerror}",
                           code=e.errno)


def put(key: str, value: str) -> None:
    if "\n" in value or "\r" in value:
        raise ValueError("a secret on one line only")
    path = _path(key)
    incoming = path.with_name(f".{key}.incoming")
    try:
        for folder in (path.parent.parent, path.parent):  # the config folder, then its own
            folder.mkdir(mode=0o700, exist_ok=True)
        path.parent.chmod(0o700)  # also a folder an older install left open to others
        incoming.unlink(missing_ok=True)
        fd = os.open(incoming, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with open(fd, "w", encoding="utf-8") as f:
            os.fchmod(f.fileno(), 0o600)  # whatever the umask
            f.write(value + "\n")
            f.flush()
            os.fsync(f.fileno())  # on disk before it replaces the old one
        os.replace(incoming, path)
    except OSError as e:
        with contextlib.suppress(OSError):
            incoming.unlink(missing_ok=True)
        raise SecretsError(f"{key} couldn't be stored in {path}: {e.strerror}", code=e.errno)


def put_typed(key: str) -> None:
    try:
        value = getpass.getpass(f"Paste or type {key} (hidden input): ").strip()
    except (KeyboardInterrupt, EOFError, OSError):
        value = ""
    if not value:
        raise SecretsError(f"{key} wasn't stored")
    put(key, value)


def has(key: str) -> bool:
    return _path(key).is_file()


def delete(key: str) -> None:
    try:
        _path(key).unlink(missing_ok=True)
    except OSError as e:
        raise SecretsError(f"{key} couldn't be deleted. Delete the file yourself: {_path(key)}",
                           code=e.errno)


def where(key: str) -> str:
    return str(_path(key))


def reachable() -> bool:
    return True  # a file in the user's own home needs no session
