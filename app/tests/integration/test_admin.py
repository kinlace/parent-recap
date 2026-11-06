"""The maintainers' `admin` command on the gateway (ops/gateway/admin, #741): every task it lists,
refused without root; the tasks that run the shared program as the sign-up service's user, with
the same arguments and safeguards as the scripts they replaced; the Gateway Keys listed, made and
turned off or on through Bifrost's admin API, which is faked here, with no key's value printed but
by `key new`; a Household found from a Recipient's address; and the nightly check (#745), with
the Households' status, systemd and Email Delivery's SMTP faked: nothing wrong mails nothing, and
each kind of problem is in the mail, which holds only what the status prints; and the mail of a
new Household (`signed-up`, #814), with only its label, note and evening time."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import io
import json
import smtplib
import sys
import urllib.error
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import pytest

from family_brief.signup.status import REBOOT_PENDING, Status, line

GATEWAY = Path(__file__).resolve().parents[3] / "ops" / "gateway"


def _load(name: str, path: Path) -> Any:
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = sys.modules[name] = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


admin = _load("gateway_admin", GATEWAY / "admin")
relay = _load("brief_relay", GATEWAY / "brief_relay.py")

AI = {"id": "id-1", "name": "ai-pilot-a", "value": "sk-bf-secret-one", "is_active": True,
      "description": "Korhonen family, gateway AI, 2026-10-01",
      "created_at": "2026-10-01T08:00:00Z",
      "provider_configs": [{"provider": "anthropic"}],
      "budgets": [{"max_limit": 0.10, "current_usage": 0.02}]}
SEND = {"id": "id-2", "name": "send-pilot-b", "value": "sk-bf-secret-two", "is_active": False,
        "created_at": "2026-10-20T10:00:00.123456789+03:00"}
# Bifrost's request log, as its admin API lists it: a request's key, time, status and cost (no
# content, as disable_content_logging is on). Today is 2026-11-04 (UTC).
LOGS = [
    {"id": "r1", "virtual_key_id": "id-1", "timestamp": "2026-11-04T07:00:00Z",
     "status": "success", "cost": 0.02},
    # 18:00 UTC on 3 November, written in another zone.
    {"id": "r2", "virtual_key_id": "id-1", "timestamp": "2026-11-04T01:00:00.5+07:00",
     "status": "success", "cost": 0.01},
    {"id": "r3", "virtual_key_id": "id-1", "timestamp": "2026-10-10T21:00:00Z",
     "status": "success", "cost": 0.30},
    # Over 30 days ago, and last month.
    {"id": "r4", "virtual_key_id": "id-1", "timestamp": "2026-09-20T21:00:00Z",
     "status": "success", "cost": 1.00},
    # Bifrost refusing a send-only key's model call: no use, no cost.
    {"id": "r5", "virtual_key_id": "id-2", "timestamp": "2026-11-03T21:00:00Z",
     "status": "error", "cost": None},
    # A key since deleted from Bifrost.
    {"id": "r6", "virtual_key_id": "id-9", "timestamp": "2026-11-02T21:00:00Z",
     "status": "success", "cost": 0.05},
]


class FakeBifrost:
    """Bifrost's admin API for virtual keys: the list, POST to make one, PUT to change one. And its
    request log, newest first, from `start_time`, at most `log_page` requests a page."""

    def __init__(self, keys: list[dict], down: bool = False, logs: Any = (),
                 log_page: int = 1000) -> None:
        self.keys = [dict(k) for k in keys]
        self.logs = [dict(e) for e in logs]
        self.log_page = log_page
        # Whether a page of the request log says how many requests there are in all.
        self.log_total = True
        self.down = down
        self.made: list[dict] = []
        self.changed: list[tuple[str, dict]] = []
        self.log_queries: list[dict] = []

    def __call__(self, path: str, body: dict | None = None, method: str | None = None) -> dict:
        if self.down:
            raise urllib.error.URLError("connection refused")
        if path.startswith("/api/logs?"):
            query = dict(p.split("=", 1) for p in path.split("?", 1)[1].split("&"))
            self.log_queries.append(query)
            start = datetime.fromisoformat(query["start_time"])
            logs = sorted((e for e in self.logs
                           if datetime.fromisoformat(e["timestamp"]) >= start),
                          key=lambda e: datetime.fromisoformat(e["timestamp"]),
                          reverse=query["order"] == "desc")
            limit, offset = min(int(query["limit"]), self.log_page), int(query["offset"])
            pagination = {"limit": limit, "offset": offset,
                          **({"total_count": len(logs)} if self.log_total else {})}
            return {"logs": logs[offset:offset + limit], "pagination": pagination,
                    "stats": {"total_requests": len(logs)}}
        if method == "PUT":
            key_id = path.rsplit("/", 1)[1]
            key = next(k for k in self.keys if k["id"] == key_id)
            key.update(body or {})
            self.changed.append((key_id, body or {}))
            return {"message": "updated", "virtual_key": key}
        if body is not None:
            n = len(self.made)
            key = {**body, "id": f"id-made-{n}", "value": f"sk-bf-made-{n}"}
            self.made.append(body)
            self.keys.append(key)
            return {"message": "created", "virtual_key": key}
        offset = int(path.split("offset=")[1])
        page = self.keys[offset:offset + 100]
        return {"virtual_keys": page, "total_count": len(self.keys)}


class FakeSMTP:
    """Email Delivery's SMTP as smtplib.SMTP_SSL is called: each message it took, or `refuse`."""

    def __init__(self) -> None:
        self.sent: list[tuple[EmailMessage, list[str]]] = []
        self.logins: list[tuple[str, str]] = []
        self.refuse = False
        self.refuse_to: set[str] = set()

    def __call__(self, host: str, port: int, **_kw: Any) -> "FakeSMTP":
        assert port == 465
        return self

    def __enter__(self) -> "FakeSMTP":
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None

    def login(self, user: str, password: str) -> None:
        if self.refuse:
            raise smtplib.SMTPAuthenticationError(535, b"no")
        self.logins.append((user, password))

    def send_message(self, msg: EmailMessage, from_addr: str, to_addrs: list[str]) -> dict:
        self.sent.append((msg, to_addrs))
        return {a: (550, b"no such user") for a in to_addrs if a in self.refuse_to}


class Server:
    """The server's files as gateway-setup.sh leaves them, under tmp_path, and what `admin` ran."""

    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path
        etc = tmp_path / "etc"
        (etc / "bifrost").mkdir(parents=True)
        (etc / "bifrost" / "bifrost.env").write_text(
            "ANTHROPIC_API_KEY=sk-ant-provider\nBIFROST_ADMIN_USERNAME=admin\n"
            "BIFROST_ADMIN_PASSWORD=pa=ss\n")
        (etc / "signup").mkdir()
        (etc / "signup" / "signup.env").write_text("GATEWAY_HOST=gateway.example\n")
        (etc / "brief-relay").mkdir()
        (etc / "brief-relay" / "brief-relay.env").write_text(
            "BRIEF_SMTP_USERNAME=smtp-user\nBRIEF_SMTP_PASSWORD=smtp-pass\n")
        (etc / "brief-relay" / "maintainers.env").write_text(
            "MAINTAINERS_ADDRESS=ops@example.com,second@example.com\n")
        (tmp_path / "relay").mkdir()
        self.store = tmp_path / "store"
        self.store.mkdir()
        self.homes = tmp_path / "home"
        self.homes.mkdir()
        self.paths = admin.Paths(
            bifrost_env=etc / "bifrost" / "bifrost.env",
            signup_env=etc / "signup" / "signup.env",
            relay=GATEWAY / "brief_relay.py",
            relay_state=tmp_path / "relay",
            program="/usr/local/lib/parent-recap/bin/parent-recap",
            python=sys.executable,
            homes=self.homes,
            store=self.store,
            smtp_env=etc / "brief-relay" / "brief-relay.env",
            maintainers_env=etc / "brief-relay" / "maintainers.env",
            reboot_required=tmp_path / "run" / "reboot-required",
        )
        self.bifrost = FakeBifrost([AI, SEND], logs=LOGS)
        self.ran: list[tuple[list[str], dict]] = []
        self.root_user = True
        # What the nightly check reads: `signup status`'s exit and output, and systemd's units.
        self.status = (0, "")
        self.enabled = {"caddy", "bifrost", "brief-relay", "parent-recap-signup"}
        self.down: set[str] = set()
        self.checked: list[list[str]] = []
        self.smtp = FakeSMTP()
        # What the maintainer types at `key new`'s question, one answer each time it asks; None
        # is no terminal to ask on.
        self.answers: list[str] | None = None
        self.asked: list[str] = []

    def ask(self, prompt: str) -> str:
        self.asked.append(prompt)
        return self.answers.pop(0)

    def run(self, argv: list[str], env: dict) -> tuple[int, str]:
        self.checked.append(argv)
        if argv[:2] == ["systemctl", "is-enabled"]:
            return (0 if argv[-1] in self.enabled else 1), ""
        if argv[:2] == ["systemctl", "is-active"]:
            return (3 if argv[-1] in self.down else 0), ""
        assert "BIFROST_ADMIN_PASSWORD" not in env
        return self.status

    def household(self, label: str, recipients: list[Any], note: str = "",
                  weekend: list[Any] | None = None) -> None:
        family = self.homes / label / ".family"
        family.mkdir(parents=True)
        email = {"to": recipients, **({"weekend_to": weekend} if weekend else {})}
        config = {"hosted": True, "kids": [{"name": "Mia Virtanen"}], "email": email,
                  "secret_note": "do-not-print"}
        (family / "config.yaml").write_text(json.dumps(config))  # JSON is YAML
        listed = json.loads((self.store / "households.json").read_text()) \
            if (self.store / "households.json").exists() else []
        listed.append({"user": label, "evening": "21:00", "created": "2026-11-01",
                       **({"note": note} if note else {})})
        (self.store / "households.json").write_text(json.dumps(listed))

    def admin(self, *args: str) -> int:
        def exec_as(argv: list[str], env: dict) -> int:
            self.ran.append((argv, env))
            return 0
        try:
            return admin.main(list(args), paths=self.paths, is_root=lambda: self.root_user,
                              exec_as=exec_as, fetch=self.bifrost, today="2026-11-04",
                              run=self.run, smtp=self.smtp,
                              ask=self.ask if self.answers is not None else None)
        except SystemExit as e:
            if isinstance(e.code, str):
                print(e.code, file=sys.stderr)
                return 1
            return e.code or 0


@pytest.fixture
def server(tmp_path: Path) -> Server:
    return Server(tmp_path)


# ── The command


def test_help_lists_every_task_one_line_each(server, capsys):
    assert server.admin("--help") == 0
    out = capsys.readouterr().out
    for task in ("invite", "households", "keys", "cost", "key new", "key off", "key on", "find",
                 "remove", "check", "household", "signed-up"):
        assert sum(line.lstrip().startswith(task + " ") for line in out.splitlines()) == 1, task


@pytest.mark.parametrize("args", [(), ("help",)])
def test_no_task_or_help_prints_the_same_list(server, capsys, args):
    server.admin("--help")
    listed = capsys.readouterr().out
    server.admin(*args)
    assert capsys.readouterr().out == listed


@pytest.mark.parametrize("args", [("--help",), ("keys",), ("households",), ("find", "a@b.fi")])
def test_without_root_it_refuses_and_says_why(server, capsys, args):
    server.root_user = False
    assert server.admin(*args) == 1
    err = capsys.readouterr().err
    assert "root" in err and "sudo admin" in err
    assert server.ran == [] and server.bifrost.changed == []


def test_an_unknown_task_is_refused_with_the_list(server, capsys):
    assert server.admin("revoke") == 2
    captured = capsys.readouterr()
    assert "revoke" in captured.err and "key off" in captured.err


# ── Tasks the shared program does


def test_invite_runs_signup_invite_as_the_service_with_the_gateway_s_address(server):
    assert server.admin("invite", "--recipient", "parent@example.com:fi", "--note", "Virtanen") == 0
    [(argv, env)] = server.ran
    assert argv == ["sudo", "-u", "parent-recap-signup", "-H", "--",
                    "/usr/local/lib/parent-recap/bin/parent-recap", "signup", "invite",
                    "--url", "https://gateway.example",
                    "--recipient", "parent@example.com:fi", "--note", "Virtanen"]
    assert "BIFROST_ADMIN_PASSWORD" not in env


def test_invite_without_the_gateway_s_address_is_refused(server, capsys):
    server.paths.signup_env.write_text("")
    assert server.admin("invite") == 1
    assert "GATEWAY_HOST" in capsys.readouterr().err and server.ran == []


def test_households_runs_signup_status_as_the_service(server):
    assert server.admin("households") == 0
    [(argv, env)] = server.ran
    assert argv == ["sudo", "-u", "parent-recap-signup", "-H", "--",
                    "/usr/local/lib/parent-recap/bin/parent-recap", "signup", "status"]
    assert "BIFROST_ADMIN_PASSWORD" not in env


def test_remove_runs_signup_remove_with_the_admin_login_in_its_environment_only(server):
    assert server.admin("remove", "ph-0123abcd") == 0
    [(argv, env)] = server.ran
    assert argv == ["sudo", "--preserve-env=BIFROST_ADMIN_USERNAME,BIFROST_ADMIN_PASSWORD",
                    "-u", "parent-recap-signup", "-H", "--",
                    "/usr/local/lib/parent-recap/bin/parent-recap", "signup", "remove",
                    "ph-0123abcd"]
    assert env["BIFROST_ADMIN_USERNAME"] == "admin" and env["BIFROST_ADMIN_PASSWORD"] == "pa=ss"
    assert "ANTHROPIC_API_KEY" not in env and "sk-ant-provider" not in " ".join(argv)


@pytest.mark.parametrize("args", [(), ("ph-0123abcd", "ph-89abcdef")])
def test_remove_takes_exactly_one_label(server, capsys, args):
    assert server.admin("remove", *args) == 2
    assert "usage" in capsys.readouterr().err and server.ran == []


@pytest.mark.parametrize("change, command", [((), "show"), (("recipients",), "recipients"),
                                             (("myclub",), "myclub")])
def test_household_runs_its_signup_command_as_the_household_s_own_user(server, change, command):
    server.household("ph-0123abcd", ["parent@example.com"])

    assert server.admin("household", "ph-0123abcd", *change) == 0

    [(argv, env)] = server.ran
    # What the maintainer types, addresses and links, goes to its stdin: none is an argument,
    # since sudo logs each command line.
    assert argv == ["sudo", "-u", "ph-0123abcd", "-H", "--",
                    "/usr/local/lib/parent-recap/bin/parent-recap", "signup", command]
    assert "BIFROST_ADMIN_PASSWORD" not in env


@pytest.mark.parametrize("label", ["ph-89abcdef", "ph-0123ABCD", "parent-recap-signup", "root"])
def test_household_refuses_a_label_that_is_no_household_s(server, capsys, label):
    server.household("ph-0123abcd", ["parent@example.com"])

    assert server.admin("household", label) == 1

    assert "no Hosted Household" in capsys.readouterr().err and server.ran == []


def test_household_without_any_household_refuses_every_label(server, capsys):
    assert server.admin("household", "ph-0123abcd") == 1
    assert server.ran == []


@pytest.mark.parametrize("args", [(), ("ph-0123abcd", "evening"),
                                  ("ph-0123abcd", "recipients", "parent@example.com")])
def test_household_takes_a_label_and_one_change_and_no_address(server, capsys, args):
    server.household("ph-0123abcd", ["parent@example.com"])
    assert server.admin("household", *args) == 2
    assert "usage" in capsys.readouterr().err and server.ran == []


def test_a_task_needing_the_admin_login_without_bifrost_env_says_to_run_setup(server, capsys):
    server.paths.bifrost_env.unlink()
    assert server.admin("remove", "ph-0123abcd") == 1
    assert "gateway-setup.sh" in capsys.readouterr().err and server.ran == []


# ── Gateway Keys


def test_keys_lists_every_key_and_never_its_value(server, capsys):
    # The relay's last send date for each key: the send-only key last sent on 2 November, the AI
    # key before its last model call.
    (server.paths.relay_state / "last-sent.json").write_text(
        json.dumps({"id-1": "2026-11-01", "id-2": "2026-11-02"}))
    assert server.admin("keys") == 0
    out = capsys.readouterr().out
    assert out.splitlines() == [
        "ai-pilot-a  id=id-1  Korhonen family, gateway AI, 2026-10-01  writes+sends  on  "
        "made 2026-10-01  last used 2026-11-04  today $0.02 of $0.10  30 days $0.33",
        "send-pilot-b  id=id-2  sends only  off  made 2026-10-20  last used 2026-11-02  "
        "today $0.00  30 days $0.00"]
    assert "sk-bf-" not in out


BOUND = {"keys": {"id-2": {"name": "send-pilot-b", "made": "2026-10-20", "recipients": [
    {"address": "Grandma@Example.com", "language": "fi", "token": "t-one",
     "confirmed": "2026-10-21T08:00:00Z"},
    {"address": "partner@example.fi", "language": "zh", "token": "t-two", "confirmed": None}]}}}
HOLDER_EMAIL = {"id": "id-4", "name": "send-pilot-d", "value": "sk-bf-four", "is_active": True,
                "description": "Lahti family, Parent@Example.com, own Claude Code or Codex, "
                               "2026-11-04"}


def test_keys_prints_no_address_only_how_many_are_bound_and_confirmed(server, capsys):
    """Its output is pasted into a chat (#781): no Recipient's or holder's address."""
    (server.paths.relay_state / "bindings.json").write_text(json.dumps(BOUND))
    server.bifrost.keys.append(HOLDER_EMAIL)
    assert server.admin("keys") == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[1].startswith("send-pilot-b  id=id-2  sends only  ")
    assert lines[2] == "  2 addresses, 1 confirmed"
    assert lines[3].startswith("send-pilot-d  id=id-4  Lahti family, <address>, own Claude Code ")
    out = "\n".join(lines)
    for secret in ("@", "Grandma", "partner", "t-one", "sk-bf-"):
        assert secret not in out


def test_keys_leaves_out_an_address_set_by_hand_and_counts_a_key_with_none(server, capsys):
    (server.paths.relay_state / "bindings.json").write_text(json.dumps({"keys": {"id-2": {
        "name": "send-pilot-b", "made": "2026-10-20", "recipients": []}}}))
    server.bifrost.keys.append({**HOLDER_EMAIL,
                                "description": "Jörg (jörg@pörssi.fi), admin@host"})
    assert server.admin("keys") == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[2] == "  0 addresses, 0 confirmed"
    assert "  Jörg (<address>), <address>  " in lines[3] and "@" not in lines[3]


def test_keys_with_addresses_prints_them_as_find_does(server, capsys):
    (server.paths.relay_state / "bindings.json").write_text(json.dumps(BOUND))
    server.bifrost.keys.append(HOLDER_EMAIL)
    assert server.admin("keys", "--addresses") == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[2:4] == ["  Grandma@Example.com (fi): confirmed 2026-10-21T08:00:00Z",
                          "  partner@example.fi (zh): not confirmed"]
    assert lines[4].startswith("send-pilot-d  id=id-4  Lahti family, Parent@Example.com, ")
    assert "t-one" not in "\n".join(lines)


@pytest.mark.parametrize("args", [("--address",), ("--addresses", "x")])
def test_keys_takes_only_addresses(server, capsys, args):
    assert server.admin("keys", *args) == 2
    assert "keys [--addresses]" in capsys.readouterr().err


def test_keys_says_never_for_a_key_neither_bifrost_nor_the_relay_saw_used(server, capsys):
    server.bifrost.logs = []
    assert server.admin("keys") == 0
    out = capsys.readouterr().out.splitlines()
    assert "last used never" in out[0] and "last used never" in out[1]
    assert "today $0.00 of $0.10  30 days $0.00" in out[0]


def test_keys_reads_the_request_log_page_by_page_over_its_retention(server, capsys):
    server.bifrost.log_page = 2
    assert server.admin("keys") == 0
    assert "30 days $0.33" in capsys.readouterr().out
    queries = server.bifrost.log_queries
    assert [q["offset"] for q in queries] == ["0", "2", "4"]
    # Bifrost keeps 90 days of requests (config.json's log_retention_days). Oldest first, so a
    # request logged while it reads doesn't shift a page and get counted twice.
    assert {q["start_time"] for q in queries} == {"2026-08-06T00:00:00Z"}
    assert {q["order"] for q in queries} == {"asc"}


def test_without_a_total_the_request_log_is_read_until_an_empty_page(server, capsys):
    # This month has 4 requests: Bifrost's page of 3 is shorter than the 1000 asked for.
    server.bifrost.log_page, server.bifrost.log_total = 3, False
    assert server.admin("cost") == 0
    assert "  total  $0.08" in capsys.readouterr().out
    assert [q["offset"] for q in server.bifrost.log_queries] == ["0", "3", "4"]


def test_cost_without_the_key_list_says_so(server, capsys):
    bifrost = server.bifrost

    def no_keys(path: str, body: dict | None = None, method: str | None = None) -> dict:
        if path.startswith("/api/governance"):
            raise urllib.error.HTTPError(path, 401, "Unauthorized", None, io.BytesIO(b"{}"))
        return bifrost(path, body, method)
    server.bifrost = no_keys
    assert server.admin("cost") == 1
    err = capsys.readouterr().err
    assert "Gateway Keys" in err and "401" in err


def test_keys_without_the_request_log_still_lists_the_keys_and_says_why(server, capsys):
    bifrost = server.bifrost

    def no_logs(path: str, body: dict | None = None, method: str | None = None) -> dict:
        if path.startswith("/api/logs"):
            raise urllib.error.HTTPError(path, 404, "Not Found", None, io.BytesIO(b"{}"))
        return bifrost(path, body, method)
    server.bifrost = no_logs
    assert server.admin("keys") == 0
    captured = capsys.readouterr()
    assert captured.out.splitlines()[0].startswith("ai-pilot-a  id=id-1  ")
    assert "last used ?" in captured.out and "today ?" in captured.out
    assert "request log" in captured.err and "404" in captured.err


def test_cost_prints_this_month_s_spend_per_key_and_the_total(server, capsys):
    assert server.admin("cost") == 0
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert lines[0] == "Spend in 2026-11 (UTC) by Gateway Key, from Bifrost's request log:"
    assert lines[1:5] == ["  ai-pilot-a  id=id-1  $0.03",
                          "  send-pilot-b  id=id-2  $0.00",
                          "  id=id-9 (no longer a key)  $0.05",
                          "  total  $0.08"]
    assert "Anthropic Console" in out and "billed" in out
    assert "sk-bf-" not in out
    assert {q["start_time"] for q in server.bifrost.log_queries} == {"2026-11-01T00:00:00Z"}


def test_cost_without_the_request_log_says_so(server, capsys):
    bifrost = server.bifrost

    def no_logs(path: str, body: dict | None = None, method: str | None = None) -> dict:
        if path.startswith("/api/logs"):
            raise urllib.error.URLError("connection refused")
        return bifrost(path, body, method)
    server.bifrost = no_logs
    assert server.admin("cost") == 1
    captured = capsys.readouterr()
    assert "request log" in captured.err and captured.out == ""


def test_cost_takes_no_arguments(server, capsys):
    assert server.admin("cost", "2026-10") == 2
    assert "usage" in capsys.readouterr().err


def test_key_new_prints_the_new_key_s_name_and_value(server, capsys):
    assert server.admin("key", "new", "pilot-c", "Korhonen family") == 0
    assert capsys.readouterr().out == "ai-pilot-c  sk-bf-made-0\n"
    assert server.bifrost.made[0]["description"] == "Korhonen family, gateway AI, 2026-11-04"
    assert server.bifrost.made[0]["provider_configs"]


def test_key_new_send_only_has_no_provider(server, capsys):
    assert server.admin("key", "new", "pilot-c", "Korhonen family", "--send-only") == 0
    assert capsys.readouterr().out == "send-pilot-c  sk-bf-made-0\n"
    assert server.bifrost.made == [{"name": "send-pilot-c", "is_active": True,
                                    "description": "Korhonen family, own Claude Code or Codex, "
                                                   "2026-11-04"}]


def test_key_new_keeps_its_safeguards(server):
    assert server.admin("key", "new", "Virtanen family", "x") != 0
    assert server.bifrost.made == []


def test_key_new_keeps_the_holder_s_email_typed_after_who_holds_it(server, capsys):
    server.answers = ["  parent@example.com  "]
    assert server.admin("key", "new", "pilot-c", "Korhonen family", "--send-only") == 0
    assert server.bifrost.made[0]["description"] == (
        "Korhonen family, parent@example.com, own Claude Code or Codex, 2026-11-04")
    assert len(server.asked) == 1 and "email" in server.asked[0]
    # Kept only in the description: the key's name and its printed line don't hold it.
    assert server.bifrost.made[0]["name"] == "send-pilot-c"
    assert capsys.readouterr().out == "send-pilot-c  sk-bf-made-0\n"


def test_key_new_skips_the_email_on_enter(server):
    server.answers = [""]
    assert server.admin("key", "new", "pilot-c", "Korhonen family") == 0
    assert server.bifrost.made[0]["description"] == "Korhonen family, gateway AI, 2026-11-04"


def test_key_new_asks_again_for_an_email_that_isn_t_plain(server, capsys):
    server.answers = ["Parent <parent@example.com>", "parent", "parent@example.com"]
    assert server.admin("key", "new", "pilot-c", "Korhonen family") == 0
    assert len(server.asked) == 3
    assert "name@domain" in capsys.readouterr().err
    assert server.bifrost.made[0]["description"] == (
        "Korhonen family, parent@example.com, gateway AI, 2026-11-04")


def test_key_new_asks_on_the_terminal_with_the_question_off_stdout(server, capsys, monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr("builtins.input", lambda *prompt: "parent@example.com")
    assert server.admin("key", "new", "pilot-c", "Korhonen family") == 0
    captured = capsys.readouterr()
    # stdout holds the key alone, so it can go to a file; the question is on stderr.
    assert captured.out == "ai-pilot-c  sk-bf-made-0\n" and "email" in captured.err
    assert "parent@example.com" in server.bifrost.made[0]["description"]


def test_key_new_without_a_terminal_doesn_t_ask(server, monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    assert server.admin("key", "new", "pilot-c", "Korhonen family") == 0
    assert server.bifrost.made[0]["description"] == "Korhonen family, gateway AI, 2026-11-04"


@pytest.mark.parametrize("ref", ["ai-pilot-a", "id-1"])
def test_key_off_turns_a_key_off_by_name_or_id_and_says_so(server, capsys, ref):
    assert server.admin("key", "off", ref) == 0
    assert server.bifrost.changed == [("id-1", {"is_active": False})]
    out = capsys.readouterr().out
    assert out == "ai-pilot-a (id=id-1) is now off: it can neither write nor send the Brief\n"
    assert "sk-bf-" not in out


def test_key_on_turns_a_key_back_on(server, capsys):
    assert server.admin("key", "on", "send-pilot-b") == 0
    assert server.bifrost.changed == [("id-2", {"is_active": True})]
    assert capsys.readouterr().out == "send-pilot-b (id=id-2) is now on\n"


def test_a_key_already_so_is_left_as_it_is(server, capsys):
    assert server.admin("key", "off", "send-pilot-b") == 0
    assert server.bifrost.changed == []
    assert capsys.readouterr().out == "send-pilot-b (id=id-2) was already off; nothing changed\n"


def test_an_unknown_key_is_refused_and_nothing_is_changed(server, capsys):
    assert server.admin("key", "off", "ai-nobody") == 1
    assert server.bifrost.changed == []
    assert "ai-nobody" in capsys.readouterr().err


def test_a_name_two_keys_share_is_refused_with_their_ids(server, capsys):
    server.bifrost.keys.append({**AI, "id": "id-3", "value": "sk-bf-secret-three"})
    assert server.admin("key", "off", "ai-pilot-a") == 1
    err = capsys.readouterr().err
    assert "id-1" in err and "id-3" in err and "sk-bf-" not in err
    assert server.bifrost.changed == []


def test_bifrost_down_changes_nothing_and_says_so(server, capsys):
    server.bifrost.down = True
    assert server.admin("key", "off", "ai-pilot-a") == 1
    assert "nothing changed" in capsys.readouterr().err


def test_bifrost_refusing_the_change_says_nothing_changed_and_prints_no_value(server, capsys):
    bifrost = server.bifrost

    def refuse(path: str, body: dict | None = None, method: str | None = None) -> dict:
        if method == "PUT":
            raise urllib.error.HTTPError(path, 500, "Internal", None, io.BytesIO(b"{}"))
        return bifrost(path, body, method)
    server.bifrost = refuse
    assert server.admin("key", "off", "ai-pilot-a") == 1
    err = capsys.readouterr().err
    assert "500" in err and "nothing changed" in err and "sk-bf-" not in err
    assert bifrost.changed == []


@pytest.mark.parametrize("args", [("key",), ("key", "off"), ("key", "off", "a", "b"),
                                  ("key", "delete", "a")])
def test_a_key_task_without_its_arguments_is_refused(server, capsys, args):
    assert server.admin(*args) == 2
    assert "usage" in capsys.readouterr().err and server.bifrost.changed == []


def test_the_key_tasks_log_nothing(server, caplog):
    server.admin("keys")
    server.admin("cost")
    server.admin("key", "off", "ai-pilot-a")
    server.admin("key", "new", "pilot-c", "Korhonen family")
    assert caplog.records == []


# ── Find a Household


def test_find_prints_each_household_with_the_address_and_its_note_only(server, capsys):
    server.household("ph-0123abcd", [{"address": "parent@example.com", "language": "fi"}],
                     note="Virtanen family")
    server.household("ph-89abcdef", ["other@example.com", "Parent@Example.com"])
    server.household("ph-00000000", [{"address": "nobody@example.com"}])
    assert server.admin("find", "PARENT@example.com") == 0
    out = capsys.readouterr().out
    assert out.splitlines() == ["ph-0123abcd (Virtanen family)", "ph-89abcdef"]
    for secret in ("Mia", "do-not-print", "other@example.com", "nobody"):
        assert secret not in out


def test_find_reads_weekend_picks_recipients_too(server, capsys):
    server.household("ph-0123abcd", ["parent@example.com"], weekend=["grandma@example.com"])
    assert server.admin("find", "grandma@example.com") == 0
    assert capsys.readouterr().out == "ph-0123abcd\n"


def test_find_with_no_match_says_so_and_exits_1(server, capsys):
    server.household("ph-0123abcd", ["parent@example.com"])
    assert server.admin("find", "stranger@example.com") == 1
    captured = capsys.readouterr()
    assert captured.out == "" and "no Household" in captured.err


def test_find_skips_a_home_that_is_no_household_s(server, capsys):
    server.household("ph-0123abcd", ["parent@example.com"])
    (server.homes / "ubuntu" / ".family").mkdir(parents=True)
    (server.homes / "ubuntu" / ".family" / "config.yaml").write_text(
        json.dumps({"email": {"to": ["parent@example.com"]}}))
    assert server.admin("find", "parent@example.com") == 0
    assert capsys.readouterr().out == "ph-0123abcd\n"


def test_find_names_a_household_whose_config_it_couldn_t_read_and_nothing_of_it(server, capsys):
    server.household("ph-0123abcd", ["parent@example.com"])
    (server.homes / "ph-89abcdef" / ".family").mkdir(parents=True)
    (server.homes / "ph-89abcdef" / ".family" / "config.yaml").write_text("email: [unclosed")
    assert server.admin("find", "parent@example.com") == 0
    captured = capsys.readouterr()
    assert captured.out == "ph-0123abcd\n"
    assert "ph-89abcdef" in captured.err and "unclosed" not in captured.err


def test_find_follows_no_link_a_household_put_in_place_of_its_config(server, capsys):
    server.household("ph-0123abcd", ["parent@example.com"])
    outside = server.root / "root-only.yaml"
    outside.write_text(json.dumps({"email": {"to": ["parent@example.com"]}}))
    (server.homes / "ph-89abcdef" / ".family").mkdir(parents=True)
    try:
        (server.homes / "ph-89abcdef" / ".family" / "config.yaml").symlink_to(outside)
    except OSError:
        pytest.skip("this OS won't make a symlink here")
    assert server.admin("find", "parent@example.com") == 0
    assert capsys.readouterr().out == "ph-0123abcd\n"


@pytest.mark.parametrize("linked", [".family", "home"])
def test_find_follows_no_link_a_household_put_in_any_part_of_the_path(server, capsys, linked):
    """A Household's `.family`, or a home in place of a Household's, that is a link to a folder
    holding a config root alone can read."""
    server.household("ph-0123abcd", ["parent@example.com"])
    outside = server.root / "root-only"
    (outside / ".family").mkdir(parents=True)
    (outside / ".family" / "config.yaml").write_text(
        json.dumps({"email": {"to": ["parent@example.com"]}}))
    try:
        if linked == ".family":
            (server.homes / "ph-89abcdef").mkdir()
            (server.homes / "ph-89abcdef" / ".family").symlink_to(
                outside / ".family", target_is_directory=True)
        else:
            (server.homes / "ph-89abcdef").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("this OS won't make a symlink here")
    assert server.admin("find", "parent@example.com") == 0
    assert capsys.readouterr().out == "ph-0123abcd\n"


def test_find_says_why_the_shared_program_couldn_t_read_the_configs(
        server, capsys, tmp_path, monkeypatch):
    server.household("ph-0123abcd", ["parent@example.com"])
    # A shared program's Python without PyYAML.
    broken = tmp_path / "broken.py"
    broken.write_text("raise ModuleNotFoundError(\"No module named 'yaml'\")\n")
    real_run = admin.subprocess.run
    monkeypatch.setattr(admin.subprocess, "run",
                        lambda argv, **kw: real_run([argv[0], str(broken)], **kw))
    assert server.admin("find", "parent@example.com") == 1
    assert "No module named 'yaml'" in capsys.readouterr().err


def test_find_finds_a_key_by_part_of_who_holds_it_and_prints_no_value(server, capsys):
    assert server.admin("find", "korhonen") == 0
    out = capsys.readouterr().out
    assert out.splitlines() == [
        "ai-pilot-a  id=id-1  Korhonen family, gateway AI, 2026-10-01  writes+sends  on  "
        "made 2026-10-01  last used 2026-11-04  today $0.02 of $0.10  30 days $0.33"]
    assert "sk-bf-" not in out


def test_find_finds_a_key_by_its_holder_s_email(server, capsys):
    server.bifrost.keys.append({"id": "id-4", "name": "send-pilot-d", "value": "sk-bf-four",
                                "is_active": True,
                                "description": "Lahti family, Parent@Example.com, own Claude "
                                               "Code or Codex, 2026-11-04"})
    assert server.admin("find", "parent@example.com") == 0
    out = capsys.readouterr().out
    assert out.startswith("send-pilot-d  id=id-4  Lahti family, Parent@Example.com, ")
    assert len(out.splitlines()) == 1 and "sk-bf-" not in out


def test_find_finds_a_key_made_on_request_by_an_address_bound_to_it(server, capsys):
    (server.paths.relay_state / "bindings.json").write_text(json.dumps({"keys": {"id-2": {
        "name": "send-pilot-b", "made": "2026-10-20",
        "recipients": [{"address": "Grandma@Example.com", "language": "fi", "token": "t-secret",
                        "confirmed": None}]}}}))
    assert server.admin("find", "grandma@example.com") == 0
    out = capsys.readouterr().out
    assert out.splitlines() == [
        "send-pilot-b  id=id-2  sends only  off  made 2026-10-20  last used never  "
        "today $0.00  30 days $0.00",
        "  Grandma@Example.com (fi): not confirmed"]
    assert "t-secret" not in out and "sk-bf-" not in out


def test_find_says_when_it_couldn_t_read_the_bound_addresses(server, capsys):
    (server.paths.relay_state / "bindings.json").write_text("{not json")
    assert server.admin("find", "grandma@example.com") == 1
    err = capsys.readouterr().err
    assert "bindings.json" in err and "nothing matched" in err


def test_find_prints_a_hosted_household_s_key_with_its_household_s_lines(server, capsys):
    server.bifrost.keys.append({"id": "id-5", "name": FAILED.user, "value": "sk-bf-five",
                                "is_active": True, "description": "Virtanen hosted"})
    server.status = status_of(OK, FAILED)
    assert server.admin("find", "virtanen") == 0
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert lines[0].startswith(f"{FAILED.user}  id=id-5  Virtanen hosted  ")
    assert lines[1:] == line(FAILED).splitlines()
    assert OK.user not in out and "sk-bf-" not in out


def test_find_prints_both_a_household_and_a_key_that_match(server, capsys):
    server.household("ph-0123abcd", ["parent@example.com"])
    server.bifrost.keys.append({"id": "id-4", "name": "send-pilot-d", "value": "sk-bf-four",
                                "is_active": True, "description": "Lahti, parent@example.com"})
    assert server.admin("find", "parent@example.com") == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "ph-0123abcd" and lines[1].startswith("send-pilot-d  id=id-4  ")


def test_find_with_bifrost_down_still_finds_a_household_and_says_why(server, capsys):
    server.household("ph-0123abcd", ["parent@example.com"])
    server.bifrost.down = True
    assert server.admin("find", "parent@example.com") == 0
    captured = capsys.readouterr()
    assert captured.out == "ph-0123abcd\n" and "Gateway Key" in captured.err


def test_find_with_no_household_on_the_server_still_finds_a_key(server, capsys):
    assert server.admin("find", "Korhonen") == 0
    assert capsys.readouterr().out.startswith("ai-pilot-a  id=id-1  ")


@pytest.mark.parametrize("args", [(), ("a@b.fi", "c@d.fi"), (" ",)])
def test_find_takes_exactly_one_address(server, capsys, args):
    assert server.admin("find", *args) == 2
    assert "usage" in capsys.readouterr().err


# ── The nightly check (#745)

OK = Status("ph-0000aaaa", "21:00", exit=0, checks={"config": "ok"})
FAILED = Status("ph-1111bbbb", "21:05", exit=1, checks={"config": "ok", "wilma": "fail"},
                note="Virtanen")
OVERDUE = Status("ph-2222cccc", "21:10", exit=0, overdue=True, checks={"config": "ok"})
UNKNOWN = Status("ph-3333dddd", "21:15", unknown="signup health ended with 2 and said no status")


def status_of(*households: Status, reboot: bool = False) -> tuple[int, str]:
    """What `signup status` prints and its exit, from its own `line`."""
    out = "".join(line(h) + "\n" for h in households)
    out += f"\n{len(households)} Households: ...\n" + (f"\n{REBOOT_PENDING}\n" if reboot else "")
    marked = any(h.failed or h.overdue or h.unknown for h in households)
    return (1 if marked else 0), out


def mail(server) -> str:
    [(msg, to)] = server.smtp.sent
    assert to == ["ops@example.com", "second@example.com"]
    assert msg["From"] == "Parent Recap <brief@parentrecap.com>"
    assert msg["To"] == "ops@example.com, second@example.com"
    return msg.get_content()


def test_nothing_wrong_mails_nothing(server, capsys):
    server.status = status_of(OK)
    assert server.admin("check", "--mail") == 0
    assert server.smtp.sent == []
    assert "nothing wrong" in capsys.readouterr().out
    # It ran the Households' status as the service's user, as `admin households` does.
    assert ["sudo", "-u", "parent-recap-signup", "-H", "--",
            "/usr/local/lib/parent-recap/bin/parent-recap", "signup", "status"] in server.checked


@pytest.mark.parametrize("household", [FAILED, OVERDUE, UNKNOWN])
def test_a_household_marked_is_in_the_mail_with_its_lines_and_no_other(server, household):
    server.status = status_of(OK, household)
    assert server.admin("check", "--mail") == 0
    body = mail(server)
    for said in line(household).splitlines():
        assert said in body
    assert OK.user not in body and "Households: ..." not in body


def test_a_service_not_running_is_in_the_mail(server):
    server.status = status_of(OK)
    server.down = {"bifrost", "caddy"}
    assert server.admin("check", "--mail") == 0
    body = mail(server)
    assert "bifrost" in body and "caddy" in body and "brief-relay" not in body


def test_a_pending_reboot_is_in_the_mail(server):
    server.status = status_of(OK, reboot=True)
    server.paths.reboot_required.parent.mkdir()
    server.paths.reboot_required.write_text("*** System restart required ***\n")
    assert server.admin("check", "--mail") == 0
    assert "reboot" in mail(server)


def test_every_problem_goes_in_one_mail_counted_in_the_relay_s_quota(server):
    server.status = status_of(FAILED, OVERDUE, UNKNOWN)
    server.down = {"brief-relay"}
    server.paths.reboot_required.parent.mkdir()
    server.paths.reboot_required.write_text("")
    assert server.admin("check", "--mail") == 0
    body = mail(server)
    for h in (FAILED, OVERDUE, UNKNOWN):
        assert h.user in body
    assert "brief-relay" in body and "reboot" in body
    assert server.smtp.logins == [("smtp-user", "smtp-pass")]
    quota = json.loads((server.paths.relay_state / "quota.json").read_text())
    assert quota["sent"] == 2  # one message, to each of the two maintainers


def test_a_status_that_couldn_t_be_read_is_in_the_mail_without_its_output(server):
    server.status = (1, "sudo: unknown user parent-recap-signup\n")
    assert server.admin("check", "--mail") == 0
    body = mail(server)
    assert "couldn't be read" in body and "sudo:" not in body


def test_without_the_sign_up_service_no_household_is_checked(server):
    server.enabled.discard("parent-recap-signup")
    server.status = (2, "")
    assert server.admin("check", "--mail") == 0
    assert server.smtp.sent == []
    assert not any("signup" in argv for argv in server.checked)


def test_a_service_not_enabled_isn_t_said_to_be_down(server):
    server.status = status_of(OK)
    server.enabled.discard("caddy")
    server.down = {"caddy"}
    assert server.admin("check", "--mail") == 0
    assert server.smtp.sent == []


def test_without_mail_it_prints_what_it_found_and_mails_nothing(server, capsys):
    server.status = status_of(OK, FAILED)
    assert server.admin("check") == 1
    assert server.smtp.sent == []
    assert FAILED.user in capsys.readouterr().out


def test_without_the_maintainers_address_it_says_where_it_is_set(server, capsys):
    server.status = status_of(FAILED)
    server.paths.maintainers_env.write_text("")
    assert server.admin("check", "--mail") == 1
    assert server.smtp.sent == []
    err = capsys.readouterr().err
    assert "MAINTAINERS_ADDRESS" in err and "gateway-setup.sh" in err


def test_an_address_that_isn_t_plain_is_refused(server, capsys):
    server.status = status_of(FAILED)
    server.paths.maintainers_env.write_text("MAINTAINERS_ADDRESS=Ops <ops@example.com>\n")
    assert server.admin("check", "--mail") == 1
    assert server.smtp.sent == [] and "MAINTAINERS_ADDRESS" in capsys.readouterr().err


def test_a_mail_email_delivery_refused_fails_the_check_and_says_why(server, capsys):
    server.status = status_of(FAILED)
    server.smtp.refuse = True
    assert server.admin("check", "--mail") == 1
    assert "refused the gateway's login" in capsys.readouterr().err


def test_a_maintainer_address_email_delivery_refused_is_said(server, capsys):
    server.status = status_of(FAILED)
    server.smtp.refuse_to = {"second@example.com"}
    assert server.admin("check", "--mail") == 0
    out = capsys.readouterr().out
    assert "mailed to 1 maintainer address;" in out and "refused 1" in out


@pytest.mark.parametrize("args", [("--email",), ("--mail", "x")])
def test_check_takes_only_mail(server, capsys, args):
    assert server.admin("check", *args) == 2
    assert "usage" in capsys.readouterr().err


# ── A new Household's mail (#814)

def test_signed_up_mails_the_household_s_label_note_and_evening_and_nothing_else(server, capsys):
    server.household("ph-0123abcd", ["parent@example.com"], note="via Zhao 2026-11-06 #3 · 黄天茵")

    assert server.admin("signed-up", "ph-0123abcd") == 0

    [(msg, _)] = server.smtp.sent
    assert msg["Subject"] == "Parent Recap: a new Household, ph-0123abcd"
    body = mail(server)
    assert "ph-0123abcd (via Zhao 2026-11-06 #3 · 黄天茵)" in body and "21:00" in body
    # Not from the Household's config: no address, no Kid, nothing it holds.
    for kept in ("parent@example.com", "Mia", "do-not-print"):
        assert kept not in body and kept not in "".join(capsys.readouterr())
    assert server.ran == [] and server.checked == []  # read as root, nothing run
    quota = json.loads((server.paths.relay_state / "quota.json").read_text())
    assert quota["sent"] == 2  # counted in the relay's day, as the check's mail


def test_signed_up_without_a_note_mails_the_label_alone(server):
    server.household("ph-0123abcd", ["parent@example.com"])

    assert server.admin("signed-up", "ph-0123abcd") == 0

    assert "\nph-0123abcd\n" in mail(server)


def test_signed_up_for_a_household_not_listed_mails_nobody(server, capsys):
    server.household("ph-0123abcd", ["parent@example.com"])

    assert server.admin("signed-up", "ph-99999999") == 1

    assert server.smtp.sent == [] and "nobody was mailed" in capsys.readouterr().err


def test_signed_up_whose_mail_email_delivery_refused_fails_and_says_why(server, capsys):
    server.household("ph-0123abcd", ["parent@example.com"])
    server.smtp.refuse = True

    assert server.admin("signed-up", "ph-0123abcd") == 1

    assert "the mail wasn't sent" in capsys.readouterr().err


@pytest.mark.parametrize("args", [(), ("ph-0123abcd", "x"), ("ph-0123abcd --mail",),
                                  ("../etc",), ("PH-0123ABCD",)])
def test_signed_up_takes_one_household_s_label_only(server, capsys, args):
    server.household("ph-0123abcd", ["parent@example.com"])

    assert server.admin("signed-up", *args) == 2

    assert server.smtp.sent == [] and "usage" in capsys.readouterr().err
