"""Parent Recap's secrets in the macOS Keychain, service family-brief, all written and read with
/usr/bin/security.

Each item trusts /usr/bin/security, so the evening job reads it without macOS asking for the
Keychain password: a prompt there waits for an answer nobody gives. An item trusting the venv's
Python instead would stop trusting it whenever Homebrew or uv moves Python (#20), while
/usr/bin/security never moves."""
from __future__ import annotations

import os
import re
import subprocess

SERVICE = "family-brief"
SECURITY = "/usr/bin/security"
# How long a read may take. A readable item takes a fraction of a second; one that makes macOS
# ask for the Keychain password would wait for the answer.
READ_SECONDS = 5
# How long a write may take: replacing an item an earlier version stored can make macOS ask the
# family to allow it.
WRITE_SECONDS = 120
NOT_FOUND = 44  # security's exit status when there is no such item
# macOS's errSecInteractionNotAllowed: the Keychain can't be written from a process outside the
# logged-in user's desktop session, such as a tmux server's or an SSH session's, and macOS shows
# no prompt there.
INTERACTION_NOT_ALLOWED = -25308
# security names macOS's error only in words. The codes of those setup tells apart or reports.
_CODES = {
    "User interaction is not allowed.": INTERACTION_NOT_ALLOWED,
    "The user name or passphrase you entered is not correct.": -25293,  # errSecAuthFailed
    "User canceled the operation.": -128,  # errSecUserCanceled
}


class KeychainError(Exception):
    """macOS didn't let Parent Recap write or read a secret. `code` is macOS's OSStatus code,
    when security's message names a known one."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.code = next((c for text, c in _CODES.items() if text in message), None)


class NeedsPrompt(KeychainError):
    """The item is there, but macOS asks for the Keychain password before security may read it,
    or refused it: an item an earlier version stored, which trusts only that version's Python."""


def get(key: str) -> str | None:
    """The secret stored under `key`, or None when there is none. Raises NeedsPrompt when there
    is one security can't read without a prompt."""
    try:
        r = subprocess.run(_item("find-generic-password", key) + ["-w"], capture_output=True,
                           text=True, timeout=READ_SECONDS)
    except subprocess.TimeoutExpired:
        raise NeedsPrompt(f"macOS asks for the Keychain password before {key} can be read")
    except OSError as e:
        raise KeychainError(f"couldn't run {SECURITY}: {e}")
    if r.returncode == NOT_FOUND:
        return None
    if r.returncode != 0:
        raise NeedsPrompt(f"macOS didn't let {key} be read: {_said(r)}")
    return r.stdout.rstrip("\n") or None


def set_(key: str, value: str) -> None:
    """Stores `value` under `key` so security may read it without a prompt. Raises KeychainError
    when macOS refused it."""
    if "\n" in value or "\r" in value:
        raise ValueError("a secret on one line only")
    if _exists(key):
        # An item an earlier version stored trusts only that version's Python. Deleting it gives
        # the new one this access list rather than the old one's.
        r = _run(_item("delete-generic-password", key))
        if r.returncode not in (0, NOT_FOUND):
            raise KeychainError(_said(r))
    # Typed into security's own prompt, so the secret is on no command line, where `ps` would
    # show it to every account on the Mac (ADR 0005).
    command = (f"add-generic-password -U -s {_quote(SERVICE)} -a {_quote(key)} "
               f"-w {_quote(value)} -T {SECURITY}\n")
    r = _run([SECURITY, "-i"], input=command)
    # security -i goes on after a command fails, so reading it back tells whether it's stored.
    try:
        stored = get(key)
    except NeedsPrompt as e:
        raise KeychainError(_said(r) or str(e))
    if stored != value:
        raise KeychainError(_said(r) or f"{key} wasn't stored")


def unreachable_here() -> bool:
    """Whether this runs inside tmux or an SSH session, where macOS won't let it write the
    Keychain."""
    return bool(os.environ.get("TMUX") or os.environ.get("SSH_CONNECTION"))


def _item(verb: str, key: str) -> list[str]:
    return [SECURITY, verb, "-s", SERVICE, "-a", key]


def _exists(key: str) -> bool:
    # Without -w, security only says whether the item is there and never asks.
    return _run(_item("find-generic-password", key)).returncode == 0


def _run(cmd: list[str], input: str | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, input=input, capture_output=True, text=True,
                              timeout=WRITE_SECONDS)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise KeychainError(f"{SECURITY} didn't finish: {e}")


def _quote(arg: str) -> str:
    """`arg` as one word on security -i's command line. The secrets and accounts setup stores
    need no quotes; anything else is quoted as security reads it."""
    if re.fullmatch(r"[A-Za-z0-9_.@+-]+", arg):
        return arg
    return '"' + re.sub(r'(["\\])', r"\\\1", arg) + '"'


def _said(r: subprocess.CompletedProcess) -> str:
    return (r.stderr or "").strip()[:300]
