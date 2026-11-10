"""The hosted server's Wilma calls through the Finnish egress (ADR 0046, #856): with a proxy set in
the server's settings file and answering, every wilma CLI call gets Node's proxy variables and
nothing else does; with it set but not answering, the CLI goes direct, a Wilma that still answers
(Espoo) is read, one that turns the server away gets the neutral line in the Brief and the run
names the cause for the maintainers; with none set, nothing changes. Doctor names both ways Wilma
can turn a sign-in away. The proxy's port is faked at `socket.create_connection`, as the harness
fakes the network; the wilma CLI is fake_sources' fake. All names and Wilmas are made up."""
from __future__ import annotations

import json
import os
import socket
from pathlib import Path
from typing import Any

import pytest

import fake_node
from test_source_failures import the_model_says_something  # noqa: F401 (autouse)
from test_wilma_source import cli, failed, listed, read  # noqa: F401 (fixture)

from family_brief import ops, wilma_egress
from family_brief.last_run import path as last_run_path

EGRESS = "http://egress.example.ts.net:8888"
# What the wilma CLI before 2.5.1 says when Wilma's Cloudflare check turns it away, and what 2.5.1
# says (aikarjal/wilmai#26).
TURNED_AWAY = [("wilma_error", "APIError: Wilma answered HTTP 403 for its login token"),
               ("login_blocked", "Wilma blocked the sign-in before checking the password.")]


@pytest.fixture
def egress(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """The server's settings file with the proxy in it, as gateway-setup.sh writes it, and the
    proxy's port: up unless the test sets `up` False. `tried` counts each time it was tried."""
    settings = tmp_path / "wilma-egress.env"
    settings.write_text(f"# the Finnish egress\n{wilma_egress.SETTING}={EGRESS}\n")
    monkeypatch.setattr(wilma_egress, "SETTINGS", settings)
    state: dict[str, Any] = {"up": True, "tried": 0}
    others = socket.create_connection  # the harness's, which answers for every host

    def connect(address: tuple[str, int], *a: Any, **k: Any) -> Any:
        if address == ("egress.example.ts.net", 8888):
            state["tried"] += 1
            if not state["up"]:
                raise ConnectionRefusedError("refused")
        return others(address, *a, **k)
    monkeypatch.setattr(socket, "create_connection", connect)
    return state


def a_message(cli) -> None:  # noqa: F811
    cli.answers = {("messages", "list"): listed({"wilmaId": 818, "subject": "Retki"}),
                   ("messages", "read"): read(818, "Retki torstaina.")}


def through_egress(env: Any) -> bool:
    """Whether a process with `env` goes through the egress: the computer running the tests may
    have a proxy of its own in HTTPS_PROXY, which is left as it is."""
    return (env or {}).get("HTTPS_PROXY") == EGRESS or "NODE_USE_ENV_PROXY" in (env or {})


def causes(harness) -> Any:
    from family_brief.config import Config
    noted = json.loads(last_run_path(Config.load(harness.home / ".family" / "config.yaml"))
                       .read_text())
    return noted.get("causes")


def test_with_the_egress_up_every_wilma_call_goes_through_it_and_nothing_else_does(
        harness, cli, egress):  # noqa: F811
    a_message(cli)

    assert harness.run() == 0

    assert cli.ran and all(e.get("HTTPS_PROXY") == EGRESS and e.get("NODE_USE_ENV_PROXY") == "1"
                           for e in cli.envs)
    [email] = harness.sent
    assert "Wilma 1 " in email.text
    # Only the wilma CLI's own environment: no other command's, and never the program's own,
    # which the model's and the other Sources' requests use.
    for command, env in zip(harness.processes.commands, harness.processes.envs):
        if not fake_node.is_wilma(command):
            assert not through_egress(env), command
    assert not through_egress(os.environ)
    assert egress["tried"] == 1  # once for the run, not before each call
    assert causes(harness) is None


def test_with_the_egress_down_a_wilma_that_still_answers_is_read_direct(harness, cli, egress):  # noqa: F811
    egress["up"] = False
    a_message(cli)

    assert harness.run() == 0

    assert cli.ran and not any(map(through_egress, cli.envs))
    [email] = harness.sent
    assert "Wilma 1 " in email.text and "没读到" not in email.text
    assert causes(harness) is None


@pytest.mark.parametrize("code, said", TURNED_AWAY, ids=["403", "login_blocked"])
def test_with_the_egress_down_a_wilma_that_turns_the_server_away_gets_the_neutral_line(
        harness, cli, egress, code, said):  # noqa: F811
    egress["up"] = False
    cli.answers = {("messages", "list"): failed(code, said)}

    assert harness.run() == 0

    [email] = harness.sent
    assert "Wilma 没读到（今晚连不上，维护人员已经知道）" in email.text
    # Neither the proxy, the machine it is on, nor the CLI's words.
    for hidden in ("egress", "Finnish", "403", "login_blocked", "登录失败"):
        assert hidden not in email.text
    assert causes(harness) == [wilma_egress.CAUSE]


def test_with_the_egress_down_one_wilma_of_two_turning_the_server_away_is_named(
        harness, cli, egress):  # noqa: F811
    egress["up"] = False
    answer = listed({"wilmaId": 818, "subject": "Retki"})
    answer["problems"] = [{"wilma": "Helsingin kaupunki", "code": "login_blocked",
                           "message": "Wilma blocked the sign-in before checking the password."}]
    cli.answers = {("messages", "list"): answer, ("messages", "read"): read(818, "Retki torstaina.")}

    assert harness.run() == 0

    [email] = harness.sent
    assert "Wilma 部分没读到（Helsingin kaupunki: 今晚连不上，维护人员已经知道）" in email.text
    assert causes(harness) == [wilma_egress.CAUSE]


def test_with_no_egress_set_nothing_changes(harness, cli):  # noqa: F811
    cli.answers = {("messages", "list"): failed(*TURNED_AWAY[0])}
    probed = len(harness.probed)

    assert harness.run() == 0

    assert cli.ran and not any(map(through_egress, cli.envs))
    assert "egress.example.ts.net" not in harness.probed[probed:]
    [email] = harness.sent
    assert "今晚连不上" not in email.text  # the CLI's own reason, as before
    assert causes(harness) is None


@pytest.mark.parametrize("settings", ["", f"{wilma_egress.SETTING}=\n",
                                      f"{wilma_egress.SETTING}=egress.example.ts.net:8888\n",
                                      f"{wilma_egress.SETTING}=socks5://egress.example.ts.net:8888\n"],
                         ids=["empty-file", "empty", "no-scheme", "socks"])
def test_an_egress_setting_that_is_empty_or_no_http_proxy_is_none(tmp_path, settings):
    path = tmp_path / "wilma-egress.env"
    path.write_text(settings)

    assert wilma_egress.proxy(path) is None
    assert wilma_egress.proxy(tmp_path / "missing.env") is None


def test_doctor_says_wilma_couldn_t_be_reached_when_the_egress_was_down(
        harness, cli, egress, capsys):  # noqa: F811
    del harness.config["kids"][0]["myclub_ical_url"]  # no network in tests
    egress["up"] = False
    cli.answers = {("kids", "list"): failed(*TURNED_AWAY[0])}

    harness.cli("doctor", "--skip-llm")

    out = capsys.readouterr().out
    assert ops.WILMA_UNREACHABLE.split(":")[0] in out
    assert wilma_egress.causes() == [wilma_egress.CAUSE]


def test_doctor_names_a_sign_in_wilma_blocked_on_any_install(harness, cli, capsys):  # noqa: F811
    del harness.config["kids"][0]["myclub_ical_url"]  # no network in tests
    cli.answers = {("kids", "list"): failed(*TURNED_AWAY[1])}

    harness.cli("doctor", "--skip-llm")

    [line] = [l for l in capsys.readouterr().out.splitlines() if "login_blocked" in l]
    assert line.startswith(ops.FAIL.strip()) and "outside Finland" in line
    assert "sign in again" not in line  # the password is not the problem
