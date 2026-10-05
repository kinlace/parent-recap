from __future__ import annotations

import os
import re

import keyring
import keyring.errors

SERVICE = "family-brief"
# macOS's errSecInteractionNotAllowed: the Keychain can't be written from a process outside the
# logged-in user's desktop session, such as a tmux server's or an SSH session's, and macOS shows
# no prompt there.
INTERACTION_NOT_ALLOWED = -25308


def get(key: str) -> str | None:
    return keyring.get_password(SERVICE, key)


def set_(key: str, value: str) -> None:
    keyring.set_password(SERVICE, key, value)


def delete(key: str) -> None:
    try:
        keyring.delete_password(SERVICE, key)
    except keyring.errors.PasswordDeleteError:
        pass


def error_code(e: keyring.errors.KeyringError) -> int | None:
    """macOS's OSStatus code for a Keychain error, when it has one. keyring's macOS backend raises
    its error from the Security API's, whose first argument is the code, and names it in the
    message as well."""
    cause = e.__cause__
    if cause is not None and cause.args and isinstance(cause.args[0], int):
        return cause.args[0]
    m = re.search(r"\((-\d+),", str(e))
    return int(m.group(1)) if m else None


def unreachable_here() -> bool:
    """Whether this runs inside tmux or an SSH session, where macOS won't let it write the
    Keychain."""
    return bool(os.environ.get("TMUX") or os.environ.get("SSH_CONNECTION"))
