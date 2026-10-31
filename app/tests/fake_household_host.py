"""An in-memory fake of the sign-up service's household host (`family_brief.signup.host`,
ADR 0031): Linux users as folders under the test's own, Gateway Keys as a dict, and the commands
run as a Household's user run as the server would run them, without a root shell.

Each user gets a home folder, and secrets and jobs of its own (fake_system's FakeSecrets and
FakeJobs), as each Linux user has its own owner-only files and systemd user manager. A command run
as the user is one of two the service runs: the release's install.sh, which here lays out what
the install leaves, through `installs`, and the installed `parent-recap`, which runs this
process's program in-process with the user's HOME, stdin, secrets and jobs. Every command is in
`commands` and nothing is faked by patching the service's modules. `log` keeps the order of the
steps a removal takes, and a removed user's home goes with it.

A command the service runs as a user must be one ops/gateway/signup.sudoers lets it run, with its
home and the release where they are on the server, so a new call site fails here and not only
there (#645). What the user runs itself, as its evening timer does, goes through `run_own`.
"""
from __future__ import annotations

import contextlib
import io
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import pytest

from family_brief.signup.host import BASH, USER, HostError
from family_brief.system import jobs, secrets
from fake_system import FakeJobs, FakeSecrets


SUDOERS = Path(__file__).resolve().parents[2] / "ops" / "gateway" / "signup.sudoers"
HOMES = "/home"  # where household-user makes each home, and the release gateway-setup.sh unpacks
RELEASE = "/usr/local/lib/parent-recap-signup/release"
AS_HOUSEHOLD = re.compile(r"parent-recap-signup\s+ALL\s*=\s*\(%parent-recap-households\)\s+"
                          r"NOPASSWD:\s*(.+)")


def household_rules(text: str) -> list[list[str]]:
    """The commands a sudoers file lets the service's user run as a Household, each its path
    and its arguments, one line a rule."""
    return [spec.split() for line in text.splitlines()
            if (m := AS_HOUSEHOLD.fullmatch(line.strip())) for spec in m[1].split(",")]


def allowed(argv: list[str], rules: list[list[str]]) -> bool:
    """Whether sudo runs `argv` under one of `rules`: ALL is anything, a path alone takes any
    arguments, and otherwise they must be exactly the rule's. A `*` in a path is any part of one
    folder's name, as sudo's matching keeps it within the folder."""
    def path(pattern: str) -> re.Pattern:
        return re.compile("[^/]*".join(map(re.escape, pattern.split("*"))))
    return any(rule == ["ALL"]
               or (path(rule[0]).fullmatch(argv[0]) and (len(rule) == 1 or rule[1:] == argv[1:]))
               for rule in rules)


@dataclass
class User:
    home: Path
    secrets: FakeSecrets = field(default_factory=FakeSecrets)
    jobs: FakeJobs = field(default_factory=FakeJobs)


class FakeHouseholdHost:
    def __init__(self, root: Path, installs: Callable[[Path], None], release: Path) -> None:
        self.root = root
        self.release = release          # the unpacked release whose install.sh is run
        self.installs = installs        # what install.sh leaves in a user's home
        self.users: dict[str, User] = {}
        self.keys: dict[str, str] = {}  # each Gateway Key by its name
        self.removed: list[str] = []
        self.revoked: list[str] = []
        self.commands: list[list[str]] = []  # every command run as a user, with the user first
        self.log: list[tuple[str, ...]] = []  # revoke_key, remove_user and run_as, in order
        self.install_exit = 0           # install.sh's exit code
        # A step's name, such as "mint_key" or "revoke_key": that step raises; "jobs": the
        # user's systemd doesn't take the evening jobs
        self.fails: str | None = None

    def create_user(self, name: str) -> Path:
        assert USER.fullmatch(name), name
        assert name not in self.users, f"{name} is there already"
        if self.fails == "create_user":
            raise HostError("useradd failed")
        home = self.root / name
        home.mkdir(parents=True, mode=0o700)
        self.users[name] = User(home)
        if self.fails == "jobs":
            self.users[name].jobs.install_fails = "Failed to connect to bus: No medium found"
        return home

    def home(self, name: str) -> Path | None:
        assert USER.fullmatch(name), name
        return self.users[name].home if name in self.users else None

    def remove_user(self, name: str) -> None:
        """As `userdel --remove`: the user's home with it, and its systemd user manager with
        its jobs."""
        self.removed.append(name)
        self.log.append(("remove_user", name))
        if self.fails == "remove_user":
            raise HostError("userdel failed")
        self.users.pop(name, None)
        shutil.rmtree(self.root / name, ignore_errors=True)

    def mint_key(self, name: str) -> str:
        if self.fails == "mint_key":
            raise HostError("the gateway's admin API answered POST with HTTP 500")
        self.keys[name] = f"sk-bf-{name}-{len(self.keys)}-secret"
        return self.keys[name]

    def revoke_key(self, name: str) -> bool:
        self.revoked.append(name)
        self.log.append(("revoke_key", name))
        if self.fails == "revoke_key":
            raise HostError("the gateway's admin API couldn't be reached (ConnectionError)")
        return self.keys.pop(name, None) is not None

    def run_as(self, name: str, argv: list[str], stdin: str, timeout: int) -> tuple[int, str]:
        on_server = [self._on_server(a) for a in argv]
        assert allowed(on_server, household_rules(SUDOERS.read_text())), \
            f"signup.sudoers doesn't let the service run {on_server} as a Household"
        self.commands.append([name, *argv])
        self.log.append(("run_as", name, *argv))
        return self.run_own(name, argv, stdin)

    def run_own(self, name: str, argv: list[str], stdin: str = "") -> tuple[int, str]:
        """A command run as the user without the service's sudo, as its evening timer runs."""
        user = self.users[name]
        if argv[:2] == [BASH, str(self.release / "install.sh")]:
            if self.install_exit == 0:
                self.installs(user.home)
            return self.install_exit, "✅ Parent Recap installed\n"
        program = user.home / "ParentRecap" / "app" / ".venv" / "bin" / "parent-recap"
        assert argv[0] == str(program), argv
        return self._program(user, argv[1:], stdin)

    def _on_server(self, arg: str) -> str:
        for here, there in ((self.release, RELEASE), (self.root, HOMES)):
            if arg == str(here) or arg.startswith(str(here) + "/"):
                return there + arg[len(str(here)):]
        return arg

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
