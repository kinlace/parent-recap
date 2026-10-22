"""The macOS adapter of `family_brief.system.secrets`: the Keychain through `/usr/bin/security`
(ADR 0020). Callers' tests use fake_system.FakeSecrets instead.

The outside edge is faked: `/usr/bin/security`, with the Keychain it reads and writes."""
from __future__ import annotations

import shlex
import subprocess
from typing import Any

import pytest

from family_brief.system import secrets, secrets_macos

# The Keychain adapter.
pytestmark = pytest.mark.macos

# What `security` says for macOS's Keychain errors, by OSStatus code.
KEYCHAIN_MESSAGES = {
    -25308: "User interaction is not allowed.",  # errSecInteractionNotAllowed
    -25293: "The user name or passphrase you entered is not correct.",  # errSecAuthFailed
    -128: "User canceled the operation.",  # errSecUserCanceled
}


class FakeKeychain:
    """The Keychain, service family-brief, as /usr/bin/security gives it."""

    def __init__(self) -> None:
        self.items: dict[str, str] = {}  # account -> secret
        # Accounts whose item makes /usr/bin/security ask for the Keychain password before it
        # reads the secret, as an item an earlier version stored with keyring does. Nobody answers.
        self.asks: set[str] = set()
        self.refuses: int | None = None  # macOS's code when the Keychain refuses writes
        self.typed: str | None = None    # what the family types at security's own prompt
        self.input: list[str] = []       # what `security -i` was given on stdin
        self.commands: list[list[str]] = []

    def run(self, cmd: list[str], *_a: Any, input: str | None = None, timeout: float | None = None,
            **_k: Any) -> subprocess.CompletedProcess:
        assert cmd[0] == "/usr/bin/security", cmd
        self.commands.append(list(cmd))
        return self._security(cmd, input, timeout)

    def _security(self, cmd: list[str], input: str | None,
                  timeout: float | None) -> subprocess.CompletedProcess:
        if cmd[1:] == ["-i"]:
            # Each line is one command, as on security's own command line. It goes on reading
            # after a command fails; what failed shows on stderr.
            self.input.append(input or "")
            out = [self._security([cmd[0], *shlex.split(line)], None, timeout)
                   for line in (input or "").splitlines() if line.strip()]
            return subprocess.CompletedProcess(cmd, 0, "".join(r.stdout for r in out),
                                               "".join(r.stderr for r in out))
        verb, args = cmd[1], cmd[2:]
        assert args[args.index("-s") + 1] == "family-brief", cmd
        account = args[args.index("-a") + 1]
        if verb == "add-generic-password":
            assert args[args.index("-T") + 1] == "/usr/bin/security", cmd
            if self.refuses is not None:
                message = KEYCHAIN_MESSAGES[self.refuses]
                return subprocess.CompletedProcess(
                    cmd, 1, "", f"security: SecKeychainItemCreateFromContent (<default>): {message}\n")
            if account in self.items and "-U" not in args:
                return subprocess.CompletedProcess(cmd, 45, "", "security: SecKeychainItemCreate"
                                                   "FromContent (<default>): The specified item "
                                                   "already exists in the keychain.\n")
            if account not in self.items:
                self.asks.discard(account)
            if args[-1] == "-w":  # nothing after -w: security asks on the terminal itself
                if self.typed is None:
                    return subprocess.CompletedProcess(cmd, 1, "", "")
                value = self.typed
            else:
                value = args[args.index("-w") + 1]
            # -U updates the secret of an item already there but keeps its access list.
            self.items[account] = value
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if account not in self.items:
            return subprocess.CompletedProcess(
                cmd, 44, "", "security: SecKeychainSearchCopyNext: The specified item could not "
                "be found in the keychain.\n")
        if verb == "delete-generic-password":
            if self.refuses is not None:
                return subprocess.CompletedProcess(
                    cmd, 1, "", f"security: SecKeychainItemDelete: {KEYCHAIN_MESSAGES[self.refuses]}\n")
            del self.items[account]
            self.asks.discard(account)
            return subprocess.CompletedProcess(cmd, 0, "password has been deleted.\n", "")
        assert verb == "find-generic-password", cmd
        if "-w" not in args:
            return subprocess.CompletedProcess(cmd, 0, 'keychain: "login.keychain-db"\n', "")
        if account in self.asks:  # macOS's prompt waits for an answer
            assert timeout is not None, "a read the Keychain asks about would wait forever"
            raise subprocess.TimeoutExpired(cmd, timeout)
        return subprocess.CompletedProcess(cmd, 0, self.items[account] + "\n", "")


@pytest.fixture
def keychain(monkeypatch) -> FakeKeychain:
    for name in ("TMUX", "SSH_CONNECTION"):
        monkeypatch.delenv(name, raising=False)
    fake = FakeKeychain()
    monkeypatch.setattr(secrets, "_adapter", secrets_macos)
    monkeypatch.setattr(subprocess, "run", fake.run)
    return fake


def test_a_stored_secret_reads_back_and_is_gone_once_deleted(keychain):
    secrets.put("gateway-key", "sk-bf-key")
    assert secrets.get("gateway-key") == "sk-bf-key"

    secrets.delete("gateway-key")

    assert secrets.get("gateway-key") is None and keychain.items == {}


def test_deleting_a_secret_that_is_not_there_is_fine(keychain):
    secrets.delete("gateway-key")

    assert keychain.items == {}


def test_a_secret_goes_on_securitys_stdin_for_it_to_read_and_on_no_command_line(keychain):
    secrets.put("gateway-key", "sk-bf-key")

    # Stored so /usr/bin/security, which the evening job reads it with, may read it without a
    # prompt nobody answers, and typed into security -i so no command line holds it (ADR 0005).
    assert ["/usr/bin/security", "-i"] in keychain.commands
    [stdin] = keychain.input
    assert shlex.split(stdin) == ["add-generic-password", "-U", "-s", "family-brief", "-a",
                                  "gateway-key", "-w", "sk-bf-key", "-T", "/usr/bin/security"]
    assert not any("sk-bf-key" in arg for cmd in keychain.commands for arg in cmd)


def test_a_secret_that_asks_before_it_is_read_needs_a_prompt(keychain):
    keychain.items["gateway-key"] = "sk-bf-key"
    keychain.asks.add("gateway-key")

    with pytest.raises(secrets.SecretsError) as e:
        secrets.get("gateway-key")

    assert e.value.reason == "needs-prompt"
    assert secrets.has("gateway-key")  # without reading it, so nothing asks


def test_storing_it_again_replaces_a_secret_that_asks_before_it_is_read(keychain):
    # An earlier version stored it with keyring, so only that Python may read it without a prompt.
    keychain.items["gateway-key"] = "sk-bf-old"
    keychain.asks.add("gateway-key")

    secrets.put("gateway-key", "sk-bf-key")

    assert keychain.items == {"gateway-key": "sk-bf-key"} and not keychain.asks
    assert secrets.get("gateway-key") == "sk-bf-key"


@pytest.mark.parametrize("code, reason", [(-25308, "not-reachable"), (-25293, "failed"),
                                          (-128, "failed")])
def test_a_store_that_refuses_says_why_with_its_code(keychain, code, reason):
    keychain.refuses = code

    with pytest.raises(secrets.SecretsError) as e:
        secrets.put("gateway-key", "sk-bf-key")

    assert (e.value.reason, e.value.code) == (reason, code)
    assert keychain.items == {}


def test_a_typed_secret_is_asked_for_by_security_itself_and_replaces_one_stored_before(keychain):
    keychain.items["claude-oauth-token"] = "sk-ant-oat01-stored-with-keyring"
    keychain.typed = "sk-ant-oat01-typed"

    secrets.put_typed("claude-oauth-token")

    # Deleted first, so the new item gets this access list.
    assert [c[1] for c in keychain.commands][:2] == ["delete-generic-password",
                                                     "add-generic-password"]
    assert keychain.commands[1][-1] == "-w"  # nothing after it: security asks on the terminal
    assert secrets.get("claude-oauth-token") == "sk-ant-oat01-typed"


@pytest.mark.parametrize("var", ["TMUX", "SSH_CONNECTION"])
def test_in_tmux_or_over_ssh_a_keychain_that_refuses_to_ask_is_out_of_reach(keychain, monkeypatch,
                                                                             var):
    monkeypatch.setenv(var, "1")
    keychain.refuses = -25308

    assert not secrets.reachable()
    assert keychain.items == {}


@pytest.mark.parametrize("var", ["TMUX", "SSH_CONNECTION"])
def test_in_tmux_or_over_ssh_a_keychain_that_saves_is_in_reach_and_keeps_nothing(keychain,
                                                                                monkeypatch, var):
    # #461: a tmux started in the desktop session may write the Keychain all the same.
    monkeypatch.setenv(var, "1")

    assert secrets.reachable()
    assert keychain.items == {}


def test_a_keychain_that_refuses_for_another_reason_is_still_in_reach(keychain, monkeypatch):
    # Only -25308 means macOS can't ask from here; anything else a real save reports itself.
    monkeypatch.setenv("TMUX", "1")
    keychain.refuses = -25293

    assert secrets.reachable()


def test_outside_tmux_and_ssh_the_keychain_is_in_reach_without_touching_it(keychain):
    # In the desktop session a locked Keychain would ask for its password just for the probe.
    assert secrets.reachable()
    assert keychain.commands == []


def test_a_refused_deletion_says_where_to_delete_it_by_hand(keychain):
    keychain.items["gateway-key"] = "sk-bf-key"
    keychain.refuses = -128

    with pytest.raises(secrets.SecretsError) as e:
        secrets.delete("gateway-key")

    assert (e.value.reason, e.value.code) == ("failed", -128)
    assert "Keychain Access" in str(e.value) and "family-brief" in str(e.value)
    assert keychain.items == {"gateway-key": "sk-bf-key"}


def test_the_family_finds_a_secret_by_its_service_and_account(keychain):
    assert secrets.where("gateway-key") == "family-brief / gateway-key"
