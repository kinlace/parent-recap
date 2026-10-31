"""The sign-up service's household host on Linux (ADR 0031): the commands it gives sudo, and its
calls to Bifrost's admin API, each faked where the adapter meets it; and what ops/gateway's
signup.sudoers lets the service run as a Household, read as sudo reads it (#645)."""
from __future__ import annotations

import json as jsonlib
import subprocess
from pathlib import Path
from typing import Any

import pytest
import requests

from family_brief.setup_server.first_brief import BRIEF_LOOKBACK_HOURS
from family_brief.signup import host_linux
from family_brief.signup.host import BASH, HostError, user_name
from family_brief.signup.host_linux import LinuxHost
from fake_household_host import RELEASE, SUDOERS, allowed, household_rules

NAME = "ph-0123abcd"


class Sudo:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[dict[str, Any]] = []
        self.answer = subprocess.CompletedProcess([], 0, f"/home/{NAME}\n", "")
        monkeypatch.setattr(subprocess, "run", self.run)

    def run(self, cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        self.calls.append({"cmd": cmd, **kwargs})
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


class Admin:
    """Bifrost's admin API: its virtual keys, by id."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.keys: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, str, Any]] = []
        self.status = 200
        monkeypatch.setattr(requests, "request", self.request)

    def request(self, method: str, url: str, *, auth: Any, timeout: float, json: Any = None):
        assert auth == ("admin", "admin-password")
        self.calls.append((method, url, json))
        r = requests.Response()
        r.status_code = self.status
        body: Any = {}
        if self.status == 200 and method == "POST":
            key_id = str(len(self.keys) + 1)
            self.keys[key_id] = {"id": key_id, "name": json["name"], "value": f"sk-bf-{key_id}"}
            body = {"virtual_key": self.keys[key_id]}
        elif self.status == 200 and method == "GET":
            body = {"virtual_keys": list(self.keys.values())}
        elif self.status == 200 and method == "DELETE":
            self.keys.pop(url.rsplit("/", 1)[1])
        r._content = jsonlib.dumps(body).encode()
        return r


@pytest.fixture
def sudo(monkeypatch) -> Sudo:
    return Sudo(monkeypatch)


@pytest.fixture
def admin(monkeypatch) -> Admin:
    return Admin(monkeypatch)


HOST = LinuxHost("admin", "admin-password")


def test_a_user_is_made_and_removed_by_the_helper_through_sudo(sudo):
    assert HOST.create_user(NAME) == Path(f"/home/{NAME}")
    HOST.remove_user(NAME)

    assert [c["cmd"] for c in sudo.calls] == [["sudo", "-n", host_linux.HELPER, "add", NAME],
                                              ["sudo", "-n", host_linux.HELPER, "remove", NAME]]
    assert all(c["cwd"] == "/" for c in sudo.calls)


@pytest.mark.posix  # no pwd on Windows
def test_a_household_s_home_is_its_user_s_and_none_when_there_is_no_user(sudo, monkeypatch):
    import pwd
    users = {NAME: f"/home/{NAME}"}

    def getpwnam(name: str):
        if name not in users:
            raise KeyError(name)
        return type("Entry", (), {"pw_dir": users[name]})()
    monkeypatch.setattr(pwd, "getpwnam", getpwnam)

    assert HOST.home(NAME) == Path(f"/home/{NAME}")
    assert HOST.home("ph-ffffffff") is None
    with pytest.raises(ValueError):
        HOST.home("root")
    assert sudo.calls == []  # anyone may look a user up


def test_a_command_runs_as_the_household_with_its_input_on_stdin(sudo):
    sudo.answer = subprocess.CompletedProcess([], 3, "out\n", "err\n")

    code, out = HOST.run_as(NAME, ["/home/x/parent-recap", "signup", "household"],
                            '{"gateway_key": "sk-bf-secret"}', 60)

    assert (code, out) == (3, "out\nerr\n")
    [call] = sudo.calls
    assert call["cmd"] == ["sudo", "-n", "-u", NAME, "-H", "--", "/home/x/parent-recap",
                           "signup", "household"]
    assert call["input"] == '{"gateway_key": "sk-bf-secret"}' and call["timeout"] == 60


@pytest.mark.parametrize("name", ["root", "ph-../../x", "ph-0123ABCD", "ph-0123abcd9"])
def test_only_a_household_s_user_is_ever_given_to_sudo(sudo, name):
    for step in (HOST.create_user, HOST.remove_user,
                 lambda n: HOST.run_as(n, ["true"], "", 5)):
        with pytest.raises(ValueError):
            step(name)
    assert sudo.calls == []
    assert host_linux.USER.fullmatch(user_name())


def test_a_helper_that_fails_or_hangs_says_so(sudo):
    sudo.answer = subprocess.CompletedProcess([], 9, "", "useradd: user exists\n")
    with pytest.raises(HostError, match="useradd: user exists"):
        HOST.create_user(NAME)
    sudo.answer = subprocess.TimeoutExpired(["sudo"], 120)
    with pytest.raises(HostError, match="didn't end within 120 seconds"):
        HOST.remove_user(NAME)


def test_a_gateway_key_is_minted_with_a_household_s_budget_and_revoked_by_its_name(admin):
    other = HOST.mint_key("ph-ffffffff")

    key = HOST.mint_key(NAME)

    assert (key, other) == ("sk-bf-2", "sk-bf-1")
    method, url, body = admin.calls[-1]
    assert (method, url) == ("POST", host_linux.ADMIN)
    assert body["name"] == NAME and body["is_active"] is True
    assert body["budgets"] == [{"max_limit": 0.10, "reset_duration": "1d"}]
    assert body["provider_configs"][0]["allowed_models"] == ["parent-recap"]

    assert HOST.revoke_key(NAME) is True
    assert HOST.revoke_key(NAME) is False  # one that isn't there is fine

    assert [k["name"] for k in admin.keys.values()] == ["ph-ffffffff"]


def test_an_admin_api_that_refuses_says_only_its_status(admin):
    admin.status = 401
    with pytest.raises(HostError, match=r"^the gateway's admin API answered POST with HTTP 401$"):
        HOST.mint_key(NAME)


def test_the_admin_login_comes_from_the_environment(monkeypatch):
    monkeypatch.delenv("BIFROST_ADMIN_USERNAME", raising=False)
    monkeypatch.setenv("BIFROST_ADMIN_PASSWORD", "x")
    with pytest.raises(HostError, match="BIFROST_ADMIN_USERNAME not set"):
        LinuxHost.from_env()


# What the service runs as a Household, where it is on the server (#645).
HOME = f"/home/{NAME}/ParentRecap/app/.venv/bin/parent-recap"
RUNS = [[BASH, f"{RELEASE}/install.sh"],
        [HOME, "signup", "household"],
        [HOME, "run", "--first-brief", "--lookback-hours", str(BRIEF_LOOKBACK_HOURS)],
        [HOME, "signup", "health"],
        [HOME, "schedule", "uninstall"]]


def test_the_sudoers_rules_let_the_service_run_its_own_commands_as_a_household():
    rules = household_rules(SUDOERS.read_text())

    assert all(allowed(argv, rules) for argv in RUNS)


@pytest.mark.parametrize("argv", [
    ["/usr/bin/cat", f"/home/{NAME}/.config/wilmai/config.json"],
    [BASH, "-c", "cat ~/.family/secrets/gateway-key"],
    [BASH, f"{RELEASE}/install.sh", "--anything"],
    ["bash", f"{RELEASE}/install.sh"],
    [HOME, "run"],
    [HOME, "run", "--first-brief", "--lookback-hours", "720000"],
    [HOME, "signup", "household", "--extra"],
    [HOME, "doctor"],
    [f"/home/{NAME}/ParentRecap/app/.venv/bin/python", "-c", "print(1)"],
    ["/tmp/ParentRecap/app/.venv/bin/parent-recap", "signup", "health"],
])
def test_the_sudoers_rules_let_the_service_run_nothing_else_as_a_household(argv):
    assert not allowed(argv, household_rules(SUDOERS.read_text()))


def test_the_sudoers_rules_are_read_as_sudo_reads_them():
    rules = household_rules("parent-recap-signup ALL=(root) NOPASSWD: /x\n"
                            "parent-recap-signup ALL=(%parent-recap-households) NOPASSWD: /a/*/b c")

    assert rules == [["/a/*/b", "c"]]
    assert allowed(["/a/z/b", "c"], rules)
    assert not allowed(["/a/z/y/b", "c"], rules)  # a `*` stays within one folder
    assert not allowed(["/a/z/b"], rules) and not allowed(["/a/z/b", "c", "d"], rules)
    assert allowed(["/anything"], [["ALL"]]) and allowed(["/a", "any"], [["/a"]])
