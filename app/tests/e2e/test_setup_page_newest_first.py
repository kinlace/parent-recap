"""The setup page updates Parent Recap first when the Source a family adds changed since the
installed version (ADR 0051, #961), as its `setup` step in a terminal does
(test_setup_newest_first.py): for a Source that has a step on the page and a part of its own,
which are Eepos, eVaka and MyClub.

The step answers `updating-first` in place of adding anything, Parent Recap updates itself as
Update now does, and the server stops for the installed version to start it again at the same
address, where the page sends the step again. Over HTTP, as the page's own script calls it, on
the server `setup_page.py` starts inside the harness, with test_update.py's fake GitHub and
installed program. The program started again is a second server at the first one's address,
which is what `server.start_again` leaves in its place; the test of that function fakes the one
call that replaces the program. Each test asks for `github` before `harness`, so the Sources'
fakes, which pass on every request that isn't theirs, sit in front of the fake GitHub."""
from __future__ import annotations

import argparse
import json
import os
import socket
import threading
from typing import Any

import pytest
import yaml

from family_brief import setup_server, update
from family_brief.setup_server import server as page_server
from family_brief.setup_server import updates as step
from fake_sources import EEPOS_PASSWORD
from setup_page import (CAN_LISTEN, PAGE_DIR, assert_never_leaked, call, config_file, context,
                        wait_for)
from setup_page import clock, health, page, schedule  # noqa: F401
from test_setup_eepos import SECRET, at_the_eepos_step, portal, sign_in  # noqa: F401
from test_setup_evaka import portal as evaka_portal  # noqa: F401
from test_setup_myclub import at_the_myclub_step, myclub_server, save_link  # noqa: F401
from test_setup_updates import update_now, updates
from test_update import INSTALLED, github, mac, release_zip, signature, update_log  # noqa: F401
from test_update_skips import out_on_stable, parts, zips_asked

# install.sh's layout, on a Mac.
pytestmark = pytest.mark.posix

TEXT = json.loads((PAGE_DIR / "text.json").read_text(encoding="utf-8"))
EEPOS_ONLY = parts(eepos="0.20.0")


def saved(harness) -> dict[str, Any]:
    return yaml.safe_load(config_file(harness).read_text())


def check(url: str) -> dict[str, Any]:
    return call(url, "api/updates/check", method="POST", body={}).json()


def stopped_to_start_again(server) -> bool:
    return server.context.restart and server.context.stopped.is_set()


@pytest.fixture
def started_again(harness, clock):  # noqa: F811
    """The installed version's program, started again in the place of the server given, at its
    address: a second server on the same port with the same one-time code."""
    servers: list[tuple[setup_server.SetupServer, threading.Thread]] = []

    def start(old: setup_server.SetupServer) -> setup_server.SetupServer:
        made: list[setup_server.SetupServer] = []

        def listening() -> bool:
            try:
                made.append(setup_server.SetupServer(context(config_file(harness), clock),
                                                     port=old.address[1], code=old.code))
            except OSError:  # the server before is still closing
                return False
            return True
        wait_for(listening)
        thread = threading.Thread(target=made[0].serve_until_idle, daemon=True)
        thread.start()
        servers.append((made[0], thread))
        return made[0]
    yield start
    for server, thread in servers:
        server.stop()
        thread.join(5)


# ---------------------------------------------------------------- it updates first

@pytest.mark.skipif(not CAN_LISTEN, reason="a second server on the same port needs a real one")
def test_adding_eepos_updates_first_and_the_step_goes_on_in_the_new_version(
        github, harness, mac, page, portal, started_again, caplog, capsys):  # noqa: F811
    at_the_eepos_step(harness)
    out_on_stable(github, "0.20.0", EEPOS_ONLY)

    first = sign_in(page.url)

    # In place of adding it: nothing reached Eepos, and the password went nowhere.
    assert first.json() == {"result": "updating-first", "version": "0.20.0"}
    wait_for(lambda: stopped_to_start_again(page))
    assert portal.requests == [] and "eepos" not in saved(harness) and SECRET not in harness.secrets
    # The update ran as Update now's does, with Eepos counted as a Source the Household uses.
    assert mac.version() == "0.20.0" and len(mac.installs) == 1
    assert "adding fi/eepos, which changed since: updating first" in update_log(mac)

    # The installed version starts the page's program again, at the address the page has.
    new = started_again(page)
    assert new.url == page.url
    assert check(new.url) == {"result": "no-update"}  # what tells the page to send the step again
    asked = len(github.requests)

    again = sign_in(new.url)

    assert again.json()["result"] == "saved"
    assert saved(harness)["eepos"]["signins"][0]["username"] == "parent@example.com"
    assert harness.secrets[SECRET] == EEPOS_PASSWORD
    # The new version looked once more, and had nothing to install.
    assert [r["url"] for r in github.requests[asked:]] == [update.STABLE_RELEASE]
    assert len(mac.installs) == 1
    assert_never_leaked(harness, [first, again], caplog, capsys, EEPOS_PASSWORD)


def test_while_the_server_is_about_to_start_again_the_page_is_told_to_wait(
        github, harness, mac, page, portal):  # noqa: F811
    at_the_eepos_step(harness)
    out_on_stable(github, "0.20.0", EEPOS_ONLY)
    assert sign_in(page.url).json()["result"] == "updating-first"
    wait_for(lambda: page.context.restart)

    # Asked through the phase, as the server may have stopped listening by now.
    updates_phase = next(p for p in page.phases if isinstance(p, step.Updates))
    assert updates_phase.check_now({})[1] == {"result": "updating"}
    assert updates_phase.first("fi/eepos") == {"result": "updating-first"}
    assert portal.requests == []


def test_adding_myclub_and_evaka_update_first_too(github, harness, mac, page, myclub_server,  # noqa: F811
                                                  evaka_portal):  # noqa: F811
    at_the_myclub_step(harness)
    out_on_stable(github, "0.20.0", parts(myclub="0.20.0", evaka="0.20.0"))
    github.files.clear()  # the release can't be downloaded: the update fails, and nothing restarts

    r = save_link(page.url, "Mia")

    assert r.json() == {"result": "updating-first", "version": "0.20.0"}
    wait_for(lambda: check(page.url)["result"] != "updating")
    assert check(page.url)["result"] == "failed" and myclub_server.downloads == []

    r = call(page.url, "api/evaka", method="POST",
             body={"city": "espoo", "username": "parent@example.com", "password": "typed"})

    assert r.json()["result"] == "updating-first" and evaka_portal.requests == []
    wait_for(lambda: check(page.url)["result"] != "updating")
    assert mac.version() == INSTALLED and not page.context.restart


def test_a_link_for_a_kid_the_household_does_not_have_is_refused_before_any_look(
        github, harness, mac, page, myclub_server):  # noqa: F811
    at_the_myclub_step(harness)
    out_on_stable(github, "0.20.0", parts(myclub="0.20.0"))

    assert save_link(page.url, "Ada").status == 400
    assert github.requests == []


# ---------------------------------------------------------------- it goes on at once

@pytest.mark.parametrize("on_stable, said", [
    (INSTALLED, EEPOS_ONLY),
    ("0.20.0", parts(core="0.20.0")),
    ("0.21.0", parts(core="0.21.0", konnect="0.21.0", evaka="0.20.0")),
])
def test_a_part_that_is_not_newer_adds_the_source_at_once_and_downloads_nothing(
        github, harness, mac, page, portal, on_stable, said):  # noqa: F811
    at_the_eepos_step(harness)
    out_on_stable(github, on_stable, said)

    r = sign_in(page.url)

    assert r.json()["result"] == "saved" and harness.secrets[SECRET] == EEPOS_PASSWORD
    assert [r["url"] for r in github.requests] == [update.STABLE_RELEASE]
    assert mac.version() == INSTALLED and mac.installs == [] and not page.context.restart
    assert check(page.url) == {"result": "no-update"}


def test_a_program_that_does_not_update_itself_adds_the_source_at_once(
        github, harness, page, portal):  # noqa: F811
    # Not run from an installed release (no `mac`), as on Windows and for a Hosted Household.
    at_the_eepos_step(harness)
    out_on_stable(github, "0.20.0", EEPOS_ONLY)

    assert sign_in(page.url).json()["result"] == "saved"
    assert github.requests == []


# ---------------------------------------------------------------- the update can't be done

def test_without_a_network_the_step_says_so_and_nothing_is_added(
        github, harness, mac, page, portal, caplog, capsys):  # noqa: F811
    at_the_eepos_step(harness)
    github.stable = None  # the look gets no answer it can read

    r = sign_in(page.url)

    out = r.json()
    assert out["result"] == "update-failed"
    assert out["reason"].startswith("Parent Recap couldn't look for a newer release: ")
    assert portal.requests == [] and "eepos" not in saved(harness) and SECRET not in harness.secrets
    assert mac.version() == INSTALLED and not page.context.restart
    assert_never_leaked(harness, [r], caplog, capsys, EEPOS_PASSWORD)


def test_a_signature_that_does_not_verify_ends_the_step_with_the_old_version_running(
        github, harness, mac, page, portal):  # noqa: F811
    at_the_eepos_step(harness)
    out_on_stable(github, "0.20.0", EEPOS_ONLY)
    data = release_zip("0.20.0")
    github.publish("0.20.0", data, signature(data, key="someone-else"))

    assert sign_in(page.url).json()["result"] == "updating-first"
    wait_for(lambda: check(page.url)["result"] != "updating")

    # What the page asks while it waits says how the update ended, with the reason to copy.
    ended = check(page.url)
    assert ended["result"] == "failed" and "signature doesn't verify" in ended["reason"]
    assert ended["updates"]["version"] == INSTALLED
    assert portal.requests == [] and "eepos" not in saved(harness)
    assert mac.version() == INSTALLED and not page.context.restart
    # The server goes on as it was: another try looks again.
    github.publish("0.20.0")
    assert sign_in(page.url).json()["result"] == "updating-first"
    wait_for(lambda: stopped_to_start_again(page))
    assert mac.version() == "0.20.0"


def test_while_a_brief_is_being_made_the_step_says_it_is_busy(github, harness, mac, page, portal):  # noqa: F811
    at_the_eepos_step(harness)
    out_on_stable(github, "0.20.0", EEPOS_ONLY)

    with update.alone(str(config_file(harness))):  # a run, or another update
        r = sign_in(page.url)

    assert r.json() == {"result": "update-busy"}
    assert portal.requests == [] and zips_asked(github) == [] and mac.version() == INSTALLED
    assert check(page.url) == {"result": "no-update"} and not page.context.restart


# ---------------------------------------------------------------- after Update now

def test_after_update_now_the_server_starts_again_before_a_source_is_added(
        github, harness, mac, page, portal):  # noqa: F811
    at_the_eepos_step(harness)
    out_on_stable(github, "0.20.0", parts(core="0.20.0"))
    assert update_now(page.url)["result"] == "updated"
    assert not page.context.restart  # Update now leaves the page as it is
    assert updates(page.url)["version"] == "0.20.0"
    asked = len(github.requests)

    r = sign_in(page.url)

    # This server still runs 0.19.0's code: the step waits for the installed version's.
    assert r.json() == {"result": "updating-first"}
    wait_for(lambda: stopped_to_start_again(page))
    assert portal.requests == [] and len(mac.installs) == 1 and len(github.requests) == asked


# ---------------------------------------------------------------- the program started again

class Replaced(Exception):
    """The program was replaced: `os.execve` doesn't come back."""


def test_the_server_that_stopped_is_replaced_by_the_installed_version_at_its_address(
        github, harness, mac, clock, monkeypatch, capsys):  # noqa: F811
    server = setup_server.SetupServer(context(config_file(harness), clock)) if CAN_LISTEN else None
    if server is None:
        pytest.skip("needs a port to listen on")
    started: list[tuple[str, list[str], dict[str, str]]] = []

    def execve(program: str, arguments: list[str], env: dict[str, str]) -> None:
        started.append((program, arguments, env))
        raise Replaced
    monkeypatch.setattr(os, "execve", execve)
    server.context.restart = True
    server.context.stopped.set()

    with pytest.raises(Replaced):
        page_server.serve(server, opened=True)

    [(program, arguments, env)] = started
    assert program == str(mac.folder / "app" / ".venv" / "bin" / "parent-recap")
    assert arguments == [program, "-c", str(config_file(harness)), "setup", "page"]
    assert env[page_server.STARTED_AGAIN] == f"{server.address[1]} {server.code}"
    # Nothing is printed: the window goes on with the same page.
    assert capsys.readouterr().out == ""


def test_a_program_that_cannot_be_started_again_says_the_page_closed(
        github, harness, mac, clock, monkeypatch, capsys):  # noqa: F811
    if not CAN_LISTEN:
        pytest.skip("needs a port to listen on")
    server = setup_server.SetupServer(context(config_file(harness), clock))

    def execve(*_a: Any) -> None:
        raise OSError("no such file")
    monkeypatch.setattr(os, "execve", execve)
    server.context.restart = True
    server.context.stopped.set()

    assert page_server.serve(server, opened=True) == 0
    assert capsys.readouterr().out.strip() == page_server.closed_line("en")


def test_started_again_the_page_command_serves_the_same_address_and_opens_no_browser(
        harness, monkeypatch, capsys):
    if not CAN_LISTEN:
        pytest.skip("needs a port to listen on")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    monkeypatch.setenv(page_server.STARTED_AGAIN, f"{port} one-time-code")
    served: list[setup_server.SetupServer] = []
    serve = page_server.serve

    def once(server: setup_server.SetupServer, opened: bool = False) -> int:
        served.append(server)
        server.context.stopped.set()  # serves nothing: it stops at once
        return serve(server, opened)
    monkeypatch.setattr(page_server, "serve", once)

    assert page_server.cmd_page(argparse.Namespace(config=str(config_file(harness)))) == 0

    [server] = served
    assert server.url == f"http://127.0.0.1:{port}/one-time-code/"
    assert page_server.STARTED_AGAIN not in os.environ  # nothing it starts gets the code
    assert harness.opened == []
    assert capsys.readouterr().out.strip() == page_server.closed_line("en")


def test_started_again_on_a_port_taken_since_it_opens_a_new_page(harness, monkeypatch, capsys):
    if not CAN_LISTEN:
        pytest.skip("needs a port to listen on")
    served: list[tuple[setup_server.SetupServer, bool]] = []

    def once(server: setup_server.SetupServer, opened: bool = False) -> int:
        served.append((server, opened))
        server.stop()
        return 0
    monkeypatch.setattr(page_server, "serve", once)
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        monkeypatch.setenv(page_server.STARTED_AGAIN, f"{taken.getsockname()[1]} one-time-code")

        assert page_server.cmd_page(argparse.Namespace(config=str(config_file(harness)))) == 0

        [(server, opened)] = served
        assert not opened and server.address[1] != taken.getsockname()[1]
        assert server.code != "one-time-code"


# ---------------------------------------------------------------- what the page says

def test_the_page_says_each_of_them_in_all_three_languages():
    says = {"en": ("is updating itself first", "has to update itself before Eepos is added",
                   "it is busy right now"),
            "fi": ("päivittää ensin itsensä", "on päivitettävä itsensä ennen kuin Eepos lisätään",
                   "juuri nyt muuta tekeillä"),
            "zh": ("正在先更新自己", "添加 Eepos 之前，Parent Recap 需要先更新自己", "它现在正忙")}
    for language, table in TEXT.items():
        for result, words in zip(step.FIRST_RESULTS, says[language]):
            text = table[f"source.{result}"]
            assert "{source}" in text, (language, result)
            assert words in text.format(source="Eepos"), (language, result)
        # In the words the step in a terminal says them in.
        for result, texts in (("update-failed", update.NOT_ADDED), ("update-busy", update.NOT_ADDED_BUSY)):
            assert table[f"source.{result}"] == texts[language], (language, result)


def test_the_page_s_script_sends_each_of_those_steps_again_after_the_update():
    script = (PAGE_DIR / "page.js").read_text(encoding="utf-8")
    for sent in ('postNewest("api/myclub", ', "postNewest(`api/${name}`, "):
        assert sent in script, sent
    for result in step.FIRST_RESULTS:
        assert f'"{result}"' in script or f'"source.{result}"' in script, result
