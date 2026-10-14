"""The Windows adapter of `secrets`: Credential Manager, one generic credential per secret, named
`Parent Recap/<name>`, called through ctypes.

Windows reads a credential back for any program run as the same user without asking, so the
evening run never waits on a prompt, and nothing like the Keychain's per-program trust (ADR 0009)
keeps another of the family's programs out (ADR 0021). The secret is written as UTF-16, as
Windows' own `cmdkey` and Credential Manager write it."""
from __future__ import annotations

import ctypes
import functools
import subprocess
from ctypes import wintypes
from typing import Any

from .secrets_error import SecretsError

STORE = "Credential Manager"
TARGET = "Parent Recap/{name}"
CRED_TYPE_GENERIC = 1
# Kept on this PC for this Windows user, across sign-ins, and never roamed to another PC.
CRED_PERSIST_LOCAL_MACHINE = 2
ERROR_NOT_FOUND = 1168
# A session without the user's sign-in, such as an SSH one signed in with a key: Windows has no
# Credential Manager there.
ERROR_NO_SUCH_LOGON_SESSION = 1312


class _Credential(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


@functools.cache
def _api() -> Any:
    """Credential Manager's functions in advapi32, loaded on first use, so the module imports on
    any system."""
    api = ctypes.WinDLL("advapi32", use_last_error=True)  # type: ignore[attr-defined]
    api.CredWriteW.argtypes = [ctypes.POINTER(_Credential), wintypes.DWORD]
    api.CredWriteW.restype = wintypes.BOOL
    api.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                              ctypes.POINTER(ctypes.POINTER(_Credential))]
    api.CredReadW.restype = wintypes.BOOL
    api.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    api.CredDeleteW.restype = wintypes.BOOL
    api.CredFree.argtypes = [ctypes.c_void_p]
    api.CredFree.restype = None
    return api


def get(key: str) -> str | None:
    found = ctypes.POINTER(_Credential)()
    if not _api().CredReadW(where(key), CRED_TYPE_GENERIC, 0, ctypes.byref(found)):
        code = ctypes.get_last_error()
        if code == ERROR_NOT_FOUND:
            return None
        raise _error(f"Windows didn't let {key} be read", code)
    try:
        cred = found.contents
        blob = ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize)
    finally:
        _api().CredFree(found)
    return blob.decode("utf-16-le", errors="replace") or None


def put(key: str, value: str) -> None:
    if "\n" in value or "\r" in value:
        raise ValueError("a secret on one line only")
    blob = value.encode("utf-16-le")
    buffer = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
    cred = _Credential(Type=CRED_TYPE_GENERIC, TargetName=where(key), UserName=key,
                       CredentialBlobSize=len(blob),
                       CredentialBlob=ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)),
                       Persist=CRED_PERSIST_LOCAL_MACHINE)
    if not _api().CredWriteW(ctypes.byref(cred), 0):
        raise _error(f"Windows didn't let {key} be stored", ctypes.get_last_error())


def put_typed(key: str) -> None:
    # With nothing after /pass, cmdkey asks for the secret on the terminal itself, hidden. It
    # keeps the credential with its own persistence, which follows the user only to another PC
    # with a roaming profile, as an office network has and a family PC doesn't.
    try:
        r = subprocess.run(["cmdkey", f"/generic:{where(key)}", f"/user:{key}", "/pass"])
    except OSError as e:
        raise SecretsError(f"couldn't run cmdkey: {e}")
    if r.returncode != 0:
        raise SecretsError(f"{key} wasn't stored")


def has(key: str) -> bool:
    # Reading never asks the family on Windows, so this is a read that keeps the value to itself.
    return get(key) is not None


def delete(key: str) -> None:
    if not _api().CredDeleteW(where(key), CRED_TYPE_GENERIC, 0):
        code = ctypes.get_last_error()
        if code != ERROR_NOT_FOUND:
            raise _error(f"Windows didn't allow it. Delete it in Credential Manager instead, under "
                         f"Windows Credentials: {where(key)}", code)


def where(key: str) -> str:
    return TARGET.format(name=key)


def reachable() -> bool:
    # An SSH session signed in with a key has no Credential Manager, but Windows says so only
    # when a secret is written: `_error` makes that `not-reachable`.
    return True


def _error(message: str, code: int) -> SecretsError:
    """A refused read, write or delete, with Windows' own words for `code`, never the secret."""
    reason = "not-reachable" if code == ERROR_NO_SUCH_LOGON_SESSION else "failed"
    return SecretsError(f"{message}: {ctypes.FormatError(code).strip()} ({code})", reason, code)
