"""The maintainers' `admin` command on the gateway (ops/gateway/admin, #741): every task it lists,
refused without root; the tasks that run the shared program as the sign-up service's user, with
the same arguments and safeguards as the scripts they replaced; the Gateway Keys listed, made and
turned off or on through Bifrost's admin API, which is faked here, with no key's value printed but
by `key new`; and a Household found from a Recipient's address."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import io
import json
import sys
import urllib.error
from pathlib import Path
from typing import Any

import pytest

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
      "provider_configs": [{"provider": "anthropic"}],
      "budgets": [{"max_limit": 0.10, "current_usage": 0.02}]}
SEND = {"id": "id-2", "name": "send-pilot-b", "value": "sk-bf-secret-two", "is_active": False}


class FakeBifrost:
    """Bifrost's admin API for virtual keys: the list, POST to make one, PUT to change one."""

    def __init__(self, keys: list[dict], down: bool = False) -> None:
        self.keys = [dict(k) for k in keys]
        self.down = down
        self.made: list[dict] = []
        self.changed: list[tuple[str, dict]] = []

    def __call__(self, path: str, body: dict | None = None, method: str | None = None) -> dict:
        if self.down:
            raise urllib.error.URLError("connection refused")
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
        )
        self.bifrost = FakeBifrost([AI, SEND])
        self.ran: list[tuple[list[str], dict]] = []
        self.root_user = True

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
                              exec_as=exec_as, fetch=self.bifrost, today="2026-11-04")
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
    for task in ("invite", "households", "keys", "key new", "key off", "key on", "find",
                 "remove"):
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


def test_a_task_needing_the_admin_login_without_bifrost_env_says_to_run_setup(server, capsys):
    server.paths.bifrost_env.unlink()
    assert server.admin("remove", "ph-0123abcd") == 1
    assert "gateway-setup.sh" in capsys.readouterr().err and server.ran == []


# ── Gateway Keys


def test_keys_lists_every_key_and_never_its_value(server, capsys):
    assert server.admin("keys") == 0
    out = capsys.readouterr().out
    assert out.splitlines() == ["ai-pilot-a  id=id-1  writes+sends  on  today $0.02 of $0.10",
                                "send-pilot-b  id=id-2  sends only  off  today -"]
    assert "sk-bf-" not in out


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


@pytest.mark.parametrize("args", [(), ("a@b.fi", "c@d.fi")])
def test_find_takes_exactly_one_address(server, capsys, args):
    assert server.admin("find", *args) == 2
    assert "usage" in capsys.readouterr().err
