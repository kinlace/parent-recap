"""Saving a Kid's MyClub calendar link with `parent-recap setup myclub`: the link goes from
macOS's dialog to one download and the owner-only config, and nowhere else (ADR 0005).

The link carries a personal token, so it's a secret like the others. The outside edges are faked:
the dialog, the desktop that opens MyClub's page, Terminal's hidden prompt and MyClub's server.
The assistant reads only the command's one-line JSON result, so assertions are on that result, on
the config file, and on every place the link must never reach.

The setup page's MyClub step is at the end, driven with HTTP calls as the page makes them
(`setup_page.py`), against the same fake MyClub server."""
from __future__ import annotations

import getpass
import importlib.util
import json
import logging
import re
import stat
import sys
import threading
from pathlib import Path
from typing import Any

import pytest
import requests
import yaml

from family_brief import setup_server
from family_brief.collectors import myclub
from family_brief.config import Config
from setup_page import PAGE_DIR, call, progress_file, save_progress, source, statuses
from setup_page import _listening, clock, page  # noqa: F401
from setup_page import assert_never_leaked as assert_never_leaked_by_page

TOKEN = "s3cr3t-t0ken"
LINK = f"webcal://example.myclub.fi/ical/{TOKEN}?key={TOKEN}"
CALENDAR = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"
            "BEGIN:VEVENT\r\nUID:mc-1\r\nSUMMARY:Training\r\nDTSTART:20261005T150000Z\r\nEND:VEVENT\r\n"
            "BEGIN:VEVENT\r\nUID:mc-2\r\nSUMMARY:Match\r\nDTSTART:20261010T080000Z\r\nEND:VEVENT\r\n"
            "END:VCALENDAR\r\n")
SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "setup_myclub.py"


@pytest.fixture(autouse=True)
def desktop(monkeypatch):
    """A desktop session, as when the family runs setup on their own Mac."""
    for var in ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY"):
        monkeypatch.delenv(var, raising=False)


class Response:
    def __init__(self, status: int, text: str) -> None:
        self.status_code, self.text = status, text


class MyClubServer:
    """MyClub's server: answers each download with `status` and `text`, or raises `error`."""

    def __init__(self) -> None:
        self.status, self.text = 200, CALENDAR
        self.error: Exception | None = None
        self.downloads: list[str] = []

    def get(self, url: str, **_k: Any) -> Response:
        self.downloads.append(url)
        if self.error:
            raise self.error
        return Response(self.status, self.text)


@pytest.fixture
def server(harness, monkeypatch) -> MyClubServer:
    s = MyClubServer()
    monkeypatch.setattr(myclub.requests, "get", s.get)
    del harness.config["kids"][0]["myclub_ical_url"]  # Mia has no link yet
    return s


@pytest.fixture
def terminal(monkeypatch) -> dict[str, Any]:
    """Terminal's hidden prompt: `value` is what the family types, or an exception to raise."""
    t: dict[str, Any] = {"value": "", "prompts": []}

    def fake(prompt: str = "") -> str:
        t["prompts"].append(prompt)
        if isinstance(t["value"], BaseException):
            raise t["value"]
        return t["value"]
    monkeypatch.setattr(getpass, "getpass", fake)
    return t


def config_file(harness) -> Path:
    return harness.home / ".family" / "config.yaml"


def saved_links(harness) -> dict[str, str | None]:
    kids = yaml.safe_load(config_file(harness).read_text())["kids"]
    return {k["name"]: k.get("myclub_ical_url") for k in kids}


def result(capsys) -> tuple[dict[str, Any], str]:
    out, err = capsys.readouterr()
    lines = out.strip().splitlines()
    assert len(lines) == 1, f"expected one JSON line, got: {out!r}"
    return json.loads(lines[0]), out + err


def assert_never_leaked(harness, printed: str, caplog, secret: str = TOKEN) -> None:
    assert secret not in printed
    assert secret not in caplog.text
    assert not any(secret in arg for cmd in harness.commands for arg in cmd)


# ── the dialog


@pytest.mark.macos
def test_a_link_pasted_in_the_dialog_is_downloaded_and_saved(harness, server, capsys, caplog):
    caplog.set_level(logging.DEBUG)
    harness.dialog.typed = f"  {LINK}\n"

    assert harness.cli("setup", "myclub", "--kid", "Mia") == 0

    res, printed = result(capsys)
    assert res == {"result": "saved", "kid": "Mia", "events": 2}
    assert harness.desktop.opened == ["https://id.myclub.fi"]
    assert len(harness.dialog.shown) == 1 and "hidden answer" in harness.dialog.shown[0]
    assert "Mia" in harness.dialog.shown[0]
    assert server.downloads == [LINK.replace("webcal://", "https://")]
    assert saved_links(harness) == {"Mia": LINK, "Leo": None}
    assert stat.S_IMODE(config_file(harness).stat().st_mode) == 0o600
    assert_never_leaked(harness, printed, caplog)


def test_an_https_link_is_saved_too(harness, server, capsys):
    https = LINK.replace("webcal://", "https://")
    harness.dialog.typed = https

    assert harness.cli("setup", "myclub", "--kid", "Leo") == 0

    assert saved_links(harness)["Leo"] == https


def test_a_new_link_replaces_the_old_one(harness, server, capsys):
    harness.config["kids"][0]["myclub_ical_url"] = "https://example.myclub.fi/ical/old"
    harness.dialog.typed = LINK

    assert harness.cli("setup", "myclub", "--kid", "Mia") == 0

    assert saved_links(harness)["Mia"] == LINK


def test_a_link_already_saved_for_another_kid_is_not_saved_again(harness, server, capsys):
    """A link made with several members ticked is one link for all of them (#207, #209)."""
    harness.config["kids"][0]["myclub_ical_url"] = LINK
    harness.dialog.typed = LINK.replace("webcal://", "https://")

    harness.cli("setup", "myclub", "--kid", "Leo")

    res, printed = result(capsys)
    assert res["result"] == "same-link" and "Mia" in res["next"] and "Valitut jäsenet" in res["next"]
    assert saved_links(harness) == {"Mia": LINK, "Leo": None}
    assert server.downloads == []
    assert LINK not in printed


@pytest.mark.parametrize("typed", [
    "my-myclub-password",
    "https://example.com/ical/s3cr3t-t0ken",
    "https://myclub.fi.example.com/ical/s3cr3t-t0ken",
    "http://example.myclub.fi/ical/s3cr3t-t0ken",
])
def test_something_that_is_not_a_myclub_link_is_never_downloaded(harness, server, capsys, caplog,
                                                                  typed):
    caplog.set_level(logging.DEBUG)
    harness.dialog.typed = typed

    assert harness.cli("setup", "myclub", "--kid", "Mia") == 1

    res, printed = result(capsys)
    assert res["result"] == "not-a-myclub-link" and "webcal://" in res["next"]
    assert server.downloads == []
    assert saved_links(harness)["Mia"] is None
    assert_never_leaked(harness, printed, caplog, typed)


@pytest.mark.parametrize("status", [403, 404, 500])
def test_a_link_that_does_not_open_names_only_the_server_and_status(harness, server, capsys,
                                                                    caplog, status):
    caplog.set_level(logging.DEBUG)
    server.status, server.text = status, f"no calendar for {TOKEN}"
    harness.dialog.typed = LINK

    assert harness.cli("setup", "myclub", "--kid", "Mia") == 1

    res, printed = result(capsys)
    assert res["result"] == "link-failed"
    assert res["error"] == f"example.myclub.fi answered HTTP {status}"
    assert "setup myclub --kid Mia" in res["next"]
    assert saved_links(harness)["Mia"] is None
    assert_never_leaked(harness, printed, caplog)
    assert "/ical/" not in printed


def test_a_link_that_cannot_be_reached_names_only_the_server(harness, server, capsys, caplog):
    caplog.set_level(logging.DEBUG)
    server.error = requests.ConnectionError(
        f"HTTPSConnectionPool(host='example.myclub.fi', port=443): Max retries exceeded with url: "
        f"/ical/{TOKEN}?key={TOKEN} (Caused by NewConnectionError('refused'))")
    harness.dialog.typed = LINK

    assert harness.cli("setup", "myclub", "--kid", "Mia") == 1

    res, printed = result(capsys)
    assert res["result"] == "link-failed"
    assert res["error"] == "example.myclub.fi: ConnectionError"
    assert saved_links(harness)["Mia"] is None
    assert_never_leaked(harness, printed, caplog)


def test_a_page_that_is_not_a_calendar_is_not_saved(harness, server, capsys, caplog):
    # Such as MyClub's sign-in page, when the link was copied from the address bar.
    caplog.set_level(logging.DEBUG)
    server.text = f"<html><body>Sign in to MyClub {TOKEN}</body></html>"
    harness.dialog.typed = LINK

    assert harness.cli("setup", "myclub", "--kid", "Mia") == 1

    res, printed = result(capsys)
    assert res["result"] == "not-a-calendar" and "Calendar subscription" in res["next"]
    assert saved_links(harness)["Mia"] is None
    assert_never_leaked(harness, printed, caplog)


def test_closing_the_dialog_saves_nothing(harness, server, capsys):
    harness.dialog.button = "cancel"

    assert harness.cli("setup", "myclub", "--kid", "Mia") == 1

    assert result(capsys)[0]["result"] == "cancelled"
    assert server.downloads == [] and saved_links(harness)["Mia"] is None


def test_no_open_leaves_the_page_alone(harness, server, capsys):
    harness.dialog.typed = LINK

    assert harness.cli("setup", "myclub", "--kid", "Mia", "--no-open") == 0

    assert harness.desktop.opened == []


# ── which Kid


def test_an_unknown_kid_is_reported_with_the_kids_there_are_before_asking(harness, server, capsys):
    assert harness.cli("setup", "myclub", "--kid", "Emma") == 1

    res, _ = result(capsys)
    assert res["result"] == "no-such-kid" and res["kids"] == ["Mia", "Leo"]
    assert harness.dialog.shown == [] and harness.desktop.opened == []


def test_without_a_kid_it_lists_the_kids(harness, server, capsys):
    assert harness.cli("setup", "myclub") == 1

    res, _ = result(capsys)
    assert res["result"] == "no-kid" and res["kids"] == ["Mia", "Leo"]
    assert "--kid" in res["next"]
    assert harness.dialog.shown == []


def test_without_a_config_it_says_to_write_it_first(harness, server, monkeypatch, capsys, tmp_path):
    from family_brief.__main__ import main
    monkeypatch.setattr(sys, "argv", ["family-brief", "-c", str(tmp_path / "config.yaml"),
                                      "setup", "myclub", "--kid", "Mia"])

    assert main() == 1

    assert result(capsys)[0]["result"] == "no-config"
    assert harness.dialog.shown == []


def test_a_config_that_does_not_load_is_reported_without_its_links(harness, server, capsys):
    harness.config["kids"][1]["myclub_ical_url"] = LINK
    harness.config["kids"][1]["grade"] = {"not": "a grade"}

    assert harness.cli("setup", "myclub", "--kid", "Mia") == 1

    res, printed = result(capsys)
    assert res["result"] == "bad-config" and "doctor" in res["next"]
    assert TOKEN not in printed and harness.dialog.shown == []


# ── without a desktop session


def test_over_ssh_it_asks_in_a_hidden_terminal_prompt(harness, server, terminal, monkeypatch,
                                                      capsys, caplog):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("SSH_CONNECTION", "10.0.0.2 52000 10.0.0.1 22")
    terminal["value"] = LINK

    assert harness.cli("setup", "myclub", "--kid", "Mia") == 0

    res, printed = result(capsys)
    assert res["result"] == "saved"
    assert harness.dialog.shown == [] and len(terminal["prompts"]) == 1
    assert saved_links(harness)["Mia"] == LINK
    assert_never_leaked(harness, printed, caplog)


def test_with_neither_a_dialog_nor_a_terminal_it_says_where_to_run_it(harness, server, terminal,
                                                                     capsys):
    harness.dialog.button = "fails"
    terminal["value"] = EOFError()

    assert harness.cli("setup", "myclub", "--kid", "Mia") == 1

    res, _ = result(capsys)
    assert res["result"] == "no-prompt" and "setup myclub --kid Mia" in res["next"]
    assert saved_links(harness)["Mia"] is None


# ── the Terminal script from 0.4.1 and older


def test_the_terminal_script_still_saves_the_link(harness, server, terminal, monkeypatch, capsys):
    harness.cli("setup", "myclub")  # writes the harness's config; Mia has no link yet
    capsys.readouterr()
    spec = importlib.util.spec_from_file_location("setup_myclub", SCRIPT)
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    terminal["value"] = LINK
    monkeypatch.setattr(sys, "argv", ["setup_myclub.py", "Mia", "--config", str(config_file(harness))])

    assert script.main() == 0

    assert saved_links(harness)["Mia"] == LINK
    out, err = capsys.readouterr()
    assert TOKEN not in out + err


# ── Connect: MyClub, on the setup page

MYCLUB_LINK, MYCLUB_TOKEN = LINK, TOKEN


@pytest.fixture
def myclub_server(harness, monkeypatch) -> MyClubServer:
    """MyClub's server, as test_setup_myclub.py fakes it, with no Kid's link saved yet."""
    s = MyClubServer()
    monkeypatch.setattr(myclub.requests, "get", s.get)
    for kid in harness.config["kids"]:
        kid.pop("myclub_ical_url", None)
    return s


def at_the_myclub_step(harness) -> None:
    config_file(harness).parent.mkdir(parents=True, exist_ok=True)
    config_file(harness).write_text(yaml.safe_dump(harness.config, allow_unicode=True))
    save_progress(harness, {"phase": "connect", "source": "myclub", "sources": {
        "wilma": "done", "gmail": "done", "ai": "done", "whatsapp": "done"}})


def save_link(url: str, kid: str, link: str = MYCLUB_LINK) -> Response:
    return call(url, "api/myclub", method="POST", body={"kid": kid, "link": link})


def add_kid(url: str, name: str) -> Response:
    return call(url, "api/myclub/kid", method="POST", body={"name": name})


def myclub_links(harness) -> dict[str, str | None]:
    return {k["name"]: k.get("myclub_ical_url")
            for k in yaml.safe_load(config_file(harness).read_text())["kids"]}


def test_the_button_opens_the_page_to_download_the_chatgpt_app(harness, page):
    r = call(page.url, "api/open", method="POST", body={"site": "codex-app"})

    assert r.status == 200 and r.json()["result"] == "opened"
    assert harness.desktop.opened == ["https://developers.openai.com/codex/app"]


def test_the_button_opens_myclub_s_site(harness, page):
    r = call(page.url, "api/open", method="POST", body={"site": "myclub"})

    assert r.status == 200 and r.json()["result"] == "opened"
    assert harness.desktop.opened == ["https://id.myclub.fi"]


def test_each_kid_known_so_far_is_listed_without_its_link(harness, page, myclub_server):
    harness.config["kids"][0]["myclub_ical_url"] = MYCLUB_LINK
    at_the_myclub_step(harness)

    r = call(page.url, "api/state")

    assert r.json()["myclub"] == {"kids": [{"name": "Mia", "linked": True},
                                           {"name": "Leo", "linked": False}], "add": False}
    assert MYCLUB_TOKEN.encode() not in r.body


def test_the_link_saved_for_one_kid_is_refused_for_the_next(harness, page, myclub_server):
    at_the_myclub_step(harness)
    assert save_link(page.url, "Mia").json()["result"] == "saved"

    r = save_link(page.url, "Leo", MYCLUB_LINK.replace("webcal://", "https://"))

    assert r.status == 200 and r.json() == {"result": "same-link"}
    assert myclub_links(harness) == {"Mia": MYCLUB_LINK, "Leo": None}


def test_a_good_link_is_checked_and_saved_for_the_right_kid(harness, page, myclub_server, caplog,
                                                           capsys):
    caplog.set_level(logging.DEBUG)
    harness.config["kids"][0]["myclub_ical_url"] = "https://example.myclub.fi/ical/mia-old"
    at_the_myclub_step(harness)

    r = save_link(page.url, "Leo", f"  {MYCLUB_LINK}\n")

    assert r.status == 200
    out = r.json()
    assert out["result"] == "saved" and out["kid"] == "Leo" and out["events"] == 2
    assert out["kids"] == [{"name": "Mia", "linked": True}, {"name": "Leo", "linked": True}]
    assert myclub_server.downloads == [MYCLUB_LINK.replace("webcal://", "https://")]
    assert myclub_links(harness) == {"Mia": "https://example.myclub.fi/ical/mia-old",
                                     "Leo": MYCLUB_LINK}
    # Every Kid has a link now, so MyClub is done and setup moves on.
    assert out["progress"]["sources"]["myclub"] == "done" and out["progress"]["source"] is None
    # The config is where the link is kept, and only there.
    printed = "".join(capsys.readouterr())
    for shown in [r.body.decode(), call(page.url, "api/state").body.decode(), printed, caplog.text,
                  progress_file(harness).read_text(), *(a for c in harness.commands for a in c)]:
        assert MYCLUB_TOKEN not in shown


def test_with_a_kid_still_without_a_link_the_step_stays_until_continue(harness, page,
                                                                       myclub_server):
    at_the_myclub_step(harness)

    out = save_link(page.url, "Mia").json()

    assert out["progress"]["sources"]["myclub"] == "done"
    assert out["progress"]["source"] == "myclub"  # Leo's link can come next
    r = call(page.url, "api/myclub/done", method="POST", body={})
    assert r.status == 200 and r.json()["progress"]["source"] is None
    assert myclub_links(harness) == {"Mia": MYCLUB_LINK, "Leo": None}


def test_continue_needs_a_link_saved_first(harness, page, myclub_server):
    at_the_myclub_step(harness)

    for body in [{}, {"x": 1}]:
        assert call(page.url, "api/myclub/done", method="POST", body=body).status == 400
    assert call(page.url, "api/state").json()["progress"]["source"] == "myclub"


@pytest.mark.parametrize("pasted, answer, result", [
    ("my-myclub-password", None, "not-a-myclub-link"),
    ("https://example.com/ical/x", None, "not-a-myclub-link"),
    (MYCLUB_LINK, (404, ""), "link-failed"),
    (MYCLUB_LINK, (200, "<html>Sign in</html>"), "not-a-calendar"),
])
def test_a_link_that_does_not_work_is_explained_and_not_saved(harness, page, myclub_server,
                                                              caplog, capsys, pasted, answer,
                                                              result):
    caplog.set_level(logging.DEBUG)
    at_the_myclub_step(harness)
    if answer:
        myclub_server.status, myclub_server.text = answer

    r = save_link(page.url, "Mia", pasted)

    assert r.status == 200 and r.json() == {"result": result}
    if answer is None:
        assert myclub_server.downloads == []  # never sent anywhere
    assert myclub_links(harness) == {"Mia": None, "Leo": None}
    assert statuses(page.url)["myclub"] == "to-do"
    assert_never_leaked_by_page(harness, [r], caplog, capsys, MYCLUB_TOKEN)
    assert_never_leaked_by_page(harness, [r], caplog, capsys, "my-myclub-password")


def test_a_slow_myclub_holds_up_no_other_save(harness, page, myclub_server, monkeypatch):
    at_the_myclub_step(harness)
    downloading, answer = threading.Event(), threading.Event()
    get = myclub_server.get

    def slow(url: str, **k: Any):
        downloading.set()
        answer.wait(5)
        return get(url, **k)
    monkeypatch.setattr(myclub.requests, "get", slow)
    saved: list[Response] = []
    thread = threading.Thread(target=lambda: saved.append(save_link(page.url, "Mia")))
    thread.start()
    downloading.wait(5)

    assert call(page.url, "api/language", method="POST", body={"language": "fi"}).status == 200

    answer.set()
    thread.join(5)
    assert saved[0].json()["result"] == "saved"
    assert myclub_links(harness)["Mia"] == MYCLUB_LINK
    assert Config.load(config_file(harness)).summary_language == "fi"


def test_a_link_that_cannot_be_reached_says_so(harness, page, myclub_server):
    at_the_myclub_step(harness)
    myclub_server.error = requests.ConnectionError(f"no route to {MYCLUB_LINK}")

    assert save_link(page.url, "Mia").json() == {"result": "link-failed"}


def test_a_link_is_saved_only_for_a_kid_the_household_has(harness, page, myclub_server):
    at_the_myclub_step(harness)

    for body in [{"kid": "Ada", "link": MYCLUB_LINK}, {"kid": "Mia"}, {"link": MYCLUB_LINK},
                 {"kid": "Mia", "link": 1}, {"kid": "Mia", "link": MYCLUB_LINK, "x": 1}]:
        r = call(page.url, "api/myclub", method="POST", body=body)
        assert r.status == 400 and r.json()["result"] == "invalid-answers", body
        assert MYCLUB_TOKEN not in r.body.decode()
    assert myclub_server.downloads == []


def test_a_household_without_wilma_adds_its_kids_here(harness, page, myclub_server):
    harness.config |= {"kids": [], "wilma": {"enabled": False}}
    at_the_myclub_step(harness)
    assert call(page.url, "api/state").json()["myclub"] == {"kids": [], "add": True}

    r = add_kid(page.url, "  Mia ")

    assert r.status == 200
    assert r.json()["result"] == "added"
    assert r.json()["kids"] == [{"name": "Mia", "linked": False}]
    assert add_kid(page.url, "小狮").json()["kids"] == [{"name": "Mia", "linked": False},
                                                        {"name": "小狮", "linked": False}]
    assert add_kid(page.url, "mia").json() == {"result": "kid-exists"}
    assert [k.name for k in Config.load(config_file(harness)).kids] == ["Mia", "小狮"]
    assert save_link(page.url, "小狮").json()["result"] == "saved"
    assert myclub_links(harness) == {"Mia": None, "小狮": MYCLUB_LINK}


def test_a_household_with_wilma_gets_its_kids_from_wilma(harness, page, myclub_server):
    at_the_myclub_step(harness)

    for body in [{"name": "Ada"}, {"name": "  "}, {}, {"name": 1}]:
        assert call(page.url, "api/myclub/kid", method="POST", body=body).status == 400, body
    assert myclub_links(harness) == {"Mia": None, "Leo": None}


def test_skipping_myclub_saves_no_link_and_moves_on(harness, page, myclub_server):
    at_the_myclub_step(harness)

    out = source(page.url, "myclub", "skip").json()

    assert out["progress"]["sources"]["myclub"] == "skipped" and out["progress"]["source"] is None
    assert myclub_links(harness) == {"Mia": None, "Leo": None}
    assert myclub_server.downloads == []


def test_the_myclub_step_shows_where_the_link_is_and_takes_it_in_the_page_s_own_field(page):
    html = call(page.url).body.decode()

    step = re.search(r'<div id="source-myclub".*?\n    </div>\n', html, re.S).group(0)
    figures = re.findall(r"<figure>.*?</figure>", step, re.S)
    assert 2 <= len(figures) <= 3
    for figure in figures:
        assert "<svg" in figure and re.search(r'<figcaption data-text="[\w.-]+">', figure)
    assert 'id="myclub-open"' in step
    field = re.search(r'<input[^>]*id="myclub-link"[^>]*>', step).group(0)
    # A text field, so the parent can check what they pasted (#209): it's their own screen.
    assert 'type="text"' in field and 'autocomplete="off"' in field


def test_every_myclub_result_is_explained_in_all_three_languages():
    text = json.loads((PAGE_DIR / "text.json").read_text())

    for language, table in text.items():
        for result in (*setup_server.myclub.MYCLUB_RESULTS, *setup_server.myclub.MYCLUB_KID_RESULTS):
            assert table.get(f"myclub.{result}", "").strip(), (language, result)
