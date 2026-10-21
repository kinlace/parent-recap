"""What a setup step reports: one line of JSON for the assistant, which the chat setup's commands,
`setup save` and `discover gmail-senders --json` print, and the outcome the setup page returns.

`result` says what happened and, when something went wrong, `next` says what the family does about
it. A secret is never in it (ADR 0005).
"""
from __future__ import annotations

import json
import shlex
import sys
from typing import Any

from .system import secrets

OK = ("saved", "signed-in", "readable", "read")
# Where macOS lets setup save a password in the Keychain: a Terminal window in the family's own
# desktop session. Shell → New Command… starts a command outside tmux, even when each new window
# starts tmux.
PLAIN_TERMINAL = ("in a plain Terminal window outside tmux or SSH (in Terminal, Shell → New "
                  "Command… works)")
KEYCHAIN_WARNING = ("This is running inside tmux or SSH, where macOS doesn't let Parent Recap "
                    f"save passwords in the Keychain and shows no prompt. Run it {PLAIN_TERMINAL}.")


def outcome(result: str, next_: str | None = None, **extra: Any) -> dict[str, Any]:
    """A step's outcome as its command prints it, for the setup page to return too."""
    return {"result": result, **extra, **({"next": next_} if next_ else {})}


def report(result: str, next_: str | None = None, **extra: Any) -> int:
    """Prints a step's outcome as its one line of JSON, and returns the command's exit status."""
    out = {"result": result, **extra}
    if next_:
        out["next"] = next_
    print(json.dumps(out, ensure_ascii=False))
    return 0 if result in OK else 1


def keychain_failure(e: secrets.SecretsError) -> tuple[str, dict[str, Any]]:
    """A step's result when macOS refused to save a secret in the Keychain, with macOS's code
    when there is one: `keychain-not-reachable` when it can't even ask here, as in tmux or over
    SSH, otherwise `keychain-failed`."""
    result = "keychain-not-reachable" if e.reason == "not-reachable" else "keychain-failed"
    return result, ({"code": e.code} if e.code is not None else {})


def program() -> str:
    """How to run this program again from Terminal, where it isn't on the PATH."""
    if sys.argv[0].endswith("__main__.py"):
        return f"{shlex.quote(sys.executable)} -m family_brief"
    return shlex.quote(sys.argv[0]) if "/" in sys.argv[0] else "parent-recap"
