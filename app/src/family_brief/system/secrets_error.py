"""The one error `secrets` and its adapters raise, apart from them so each adapter can raise it."""
from __future__ import annotations

from typing import Literal

Reason = Literal["not-reachable", "needs-prompt", "failed"]


class SecretsError(Exception):
    """The system didn't let Parent Recap read, store or delete a secret. `reason` is
    `not-reachable` when the store can't even be asked from here, as in tmux or over SSH on
    macOS, `needs-prompt` when a stored secret can't be read without asking the family, which
    the evening run can't answer, and otherwise `failed`. `code` is the system's own error code,
    when it names one, for the family to report."""

    def __init__(self, message: str, reason: Reason = "failed", code: int | None = None) -> None:
        super().__init__(message)
        self.reason: Reason = reason
        self.code = code
