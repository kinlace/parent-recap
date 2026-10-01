from __future__ import annotations

import keyring

SERVICE = "family-brief"


def get(key: str) -> str | None:
    return keyring.get_password(SERVICE, key)


def set_(key: str, value: str) -> None:
    keyring.set_password(SERVICE, key, value)


def delete(key: str) -> None:
    try:
        keyring.delete_password(SERVICE, key)
    except keyring.errors.PasswordDeleteError:
        pass
