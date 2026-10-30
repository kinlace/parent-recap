"""An in-memory fake of the sign-up service's household host (`family_brief.signup.host`,
ADR 0031): Linux users as folders under the test's own, Gateway Keys as a dict, and the commands
run as a Household's user run as the server would run them, without a root shell.

Each user gets a home folder, and secrets and jobs of its own (fake_system's FakeSecrets and
FakeJobs), as each Linux user has its own owner-only files and systemd user manager. A command run
as the user is one of two the service runs: the release's install.sh, which here lays out what
the install leaves, through `installs`, and the installed `parent-recap`, which runs this
process's program in-process with the user's HOME, stdin, secrets and jobs. Every command is in
`commands` and nothing is faked by patching the service's modules.
"""
from __future__ import annotations

import contextlib
import io
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import pytest

from family_brief.signup.host import USER, HostError
from family_brief.system import jobs, secrets
from fake_system import FakeJobs, FakeSecrets


@dataclass
class User:
    home: Path
    secrets: FakeSecrets = field(default_factory=FakeSecrets)
    jobs: FakeJobs = field(default_factory=FakeJobs)


class FakeHouseholdHost:
    def __init__(self, root: Path, installs: Callable[[Path], None]) -> None:
        self.root = root
        self.installs = installs        # what install.sh leaves in a user's home
        self.users: dict[str, User] = {}
        self.keys: dict[str, str] = {}  # each Gateway Key by its name
        self.removed: list[str] = []
        self.revoked: list[str] = []
        self.commands: list[list[str]] = []  # every command run as a user, with the user first
        self.install_exit = 0           # install.sh's exit code
        self.fails: str | None = None   # "create_user" or "mint_key": that step raises

    def create_user(self, name: str) -> Path:
        assert USER.fullmatch(name), name
        assert name not in self.users, f"{name} is there already"
        if self.fails == "create_user":
            raise HostError("useradd failed")
        home = self.root / name
        home.mkdir(parents=True, mode=0o700)
        self.users[name] = User(home)
        return home

    def remove_user(self, name: str) -> None:
        self.removed.append(name)
        self.users.pop(name, None)

    def mint_key(self, name: str) -> str:
        if self.fails == "mint_key":
            raise HostError("the gateway's admin API answered POST with HTTP 500")
        self.keys[name] = f"sk-bf-{name}-{len(self.keys)}-secret"
        return self.keys[name]

    def revoke_key(self, name: str) -> None:
        self.revoked.append(name)
        self.keys.pop(name, None)

    def run_as(self, name: str, argv: list[str], stdin: str, timeout: int) -> tuple[int, str]:
        self.commands.append([name, *argv])
        user = self.users[name]
        if argv[0] == "bash" and Path(argv[1]).name == "install.sh":
            if self.install_exit == 0:
                self.installs(user.home)
            return self.install_exit, "✅ Parent Recap installed\n"
        program = user.home / "ParentRecap" / "app" / ".venv" / "bin" / "parent-recap"
        assert argv[0] == str(program), argv
        return self._program(user, argv[1:], stdin)

    @staticmethod
    def _program(user: User, args: list[str], stdin: str) -> tuple[int, str]:
        from family_brief import __main__ as cli
        out = io.StringIO()
        with pytest.MonkeyPatch.context() as mp, contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(out):
            mp.setenv("HOME", str(user.home))
            mp.setattr(secrets, "_adapter", user.secrets)
            mp.setattr(jobs, "_adapter", user.jobs)
            mp.setattr(sys, "stdin", io.StringIO(stdin))
            mp.setattr(sys, "argv", ["parent-recap", *args])
            try:
                code = cli.main()
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) else 1
        return code, out.getvalue()
