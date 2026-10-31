"""The household host: what the sign-up service asks of the server and the gateway to create a
Hosted Household, behind one interface (ADR 0031), so the service is tested with an in-memory
fake (tests/fake_household_host.py) and without a root shell. `host_linux` is the real one.

A Household is its own Linux user (ADR 0031); every name is one `user_name` makes. Nothing here
takes a password or a key on a command line: what a command run as the Household needs to know
goes on its stdin.
"""
from __future__ import annotations

import re
import secrets
from pathlib import Path
from typing import Protocol

PREFIX = "ph-"  # a Hosted Household's user, as `ps` and `ls /home` show it
USER = re.compile(rf"{PREFIX}[0-9a-f]{{8}}")
# install.sh runs with bash by its absolute path, as the sudoers rule names it (#645).
BASH = "/usr/bin/bash"


class HostError(RuntimeError):
    """A step the host didn't do; the message says why, with no secret in it."""


class HouseholdHost(Protocol):
    def create_user(self, name: str) -> Path:
        """Creates the Household's Linux user `name`, its home owner-only and its own systemd
        user manager running with nobody signed in. Returns its home."""

    def home(self, name: str) -> Path | None:
        """The home of the user `name`, or None when there is no such user."""

    def remove_user(self, name: str) -> None:
        """Removes the user `name` with its home and everything running as it; one that isn't
        there is fine."""

    def run_as(self, name: str, argv: list[str], stdin: str, timeout: int) -> tuple[int, str]:
        """Runs `argv` as the user `name` in its home, with `stdin` as its input, and returns its
        exit code and output. Raises HostError when it couldn't be run or didn't end in
        `timeout` seconds."""

    def mint_key(self, name: str) -> str:
        """A new Gateway Key named `name` on the gateway's admin API, with a Household's daily
        budget (ops/gateway/README.md)."""

    def revoke_key(self, name: str) -> bool:
        """Deletes the Gateway Key named `name`, and says whether there was one; one that isn't
        there is fine."""


def program(home: Path) -> Path:
    """The `parent-recap` the release's install.sh puts in a Household's home."""
    return home / "ParentRecap" / "app" / ".venv" / "bin" / "parent-recap"


def user_name() -> str:
    """A new Household's user: random, so it says nothing about the family."""
    return f"{PREFIX}{secrets.token_hex(4)}"
