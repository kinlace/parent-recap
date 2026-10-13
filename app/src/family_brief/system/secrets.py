"""Reading, storing and deleting Parent Recap's secrets: the Gmail App Password, the Claude token
and the Gateway Key (ADR 0020). Each system has its adapter; only macOS's, the Keychain, exists
so far."""
from __future__ import annotations

from . import secrets_macos as _adapter
from .secrets_error import Reason, SecretsError  # noqa: F401  callers catch secrets.SecretsError

STORE: str = _adapter.STORE  # the store's name for messages


def get(name: str) -> str | None:
    """The secret stored under `name`, or None when there is none."""
    return _adapter.get(name)


def put(name: str, value: str) -> None:
    """Stores `value`, one line, under `name`, so the evening run can read it without asking the
    family."""
    _adapter.put(name, value)


def has(name: str) -> bool:
    """Whether a secret is stored under `name`, without reading it, so the family is never
    asked."""
    return _adapter.has(name)


def delete(name: str) -> None:
    """Deletes the secret stored under `name`; one that isn't there is fine."""
    _adapter.delete(name)


def where(name: str) -> str:
    """Where the family finds the secret stored under `name` in the store's own app."""
    return _adapter.where(name)


def reachable() -> bool:
    """Whether the store may be written from here: False in tmux or over SSH on macOS, where
    macOS shows no prompt."""
    return _adapter.reachable()
