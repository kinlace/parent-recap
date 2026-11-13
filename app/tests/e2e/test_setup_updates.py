"""The setup page's Updates, under Finish's "Later, if you need it" (ADR 0047, #917): the sentence
that Parent Recap installs new releases by itself, the switch that turns that off, the installed
version, and Update now once a newer release is out, with its outcome. On Windows and for a Hosted
Household the page gets the version alone.

Over HTTP, as the page's own script calls it, on the server `setup_page.py` starts inside the
harness, with test_update.py's fake GitHub and installed program. Assertions are on the responses,
the config written, the version that runs afterwards and which hosts were asked."""
from __future__ import annotations

import json
from typing import Any

import pytest
import yaml

from family_brief import failure, run_lock, update
from family_brief.config import Config
from fake_system import LINUX, MACOS, WINDOWS
from setup_page import (PAGE_DIR, at_finish, call, config_file, connect_done, finish, finished,
                        inside, more_progress, wait_for)
from setup_page import clock, health, page, schedule  # noqa: F401
from test_setup_finish import opened_again  # noqa: F401
from test_update import ASKS, INSTALLED, github, mac, needing_family  # noqa: F401  the fixtures

# install.sh's layout, on a Mac.
pytestmark = pytest.mark.posix

TEXT = json.loads((PAGE_DIR / "text.json").read_text(encoding="utf-8"))


@pytest.fixture
def household(harness, mac):  # noqa: F811
    """A Household set up on a Mac, running an installed release."""
    connect_done(harness)
    return harness


def updates(url: str) -> dict[str, Any]:
    return call(url, "api/state").json()["updates"]


def switch(url: str, on: Any):
    return call(url, "api/updates", method="POST", body={"on": on})


def saved(harness) -> dict[str, Any]:
    return yaml.safe_load(config_file(harness).read_text())


def update_now(url: str) -> dict[str, Any]:
    """Update now, pressed, and how it ended once the page's asking says it has."""
    assert call(url, "api/updates/now", method="POST", body={}).json() == {"result": "updating"}
    out: dict[str, Any] = {}

    def ended() -> bool:
        out.clear()
        out.update(call(url, "api/updates/check", method="POST", body={}).json())
        return out["result"] != "updating"
    wait_for(ended)
    return out


# ── The sentence, the switch and the installed version


def test_the_page_shows_the_installed_version_and_that_updates_are_on(household, github, page):
    shown = updates(page.url)

    assert shown == {"available": True, "version": INSTALLED, "on": True, "newer": None,
                     "waiting": None,
                     "report": update.status(str(config_file(household))).line()}
    assert github.requests == []  # showing it asks no host
    assert "updates" not in saved(household)  # on without the family doing anything


def test_the_finish_page_has_the_sentence_the_switch_the_version_and_update_now():
    html = (PAGE_DIR / "index.html").read_text()
    block = inside(html, "finish-updates")
    setting = inside(block, "finish-updates-setting")

    # The version is shown everywhere; the rest only where the program updates itself.
    assert 'id="finish-updates-version"' in block and 'id="finish-updates-version"' not in setting
    for needed in ['data-text="finish.updates.about"', 'type="checkbox" id="finish-updates-on"',
                   'data-text="finish.updates.off-note"', 'id="finish-updates-now"',
                   'id="finish-updates-waiting" hidden', 'data-text="finish.updates.waiting"',
                   'id="finish-updates-asks"',
                   'id="finish-updates-message"', 'id="finish-updates-reason"']:
        assert needed in setting, needed
    assert 'id="finish-updates-setting" hidden' in block


def test_the_sentence_by_the_switch_says_what_off_leaves_on_in_all_three_languages():
    says = {"en": ("still looks", "after each evening Brief", "downloads nothing"),
            "fi": ("tarkistaa silti", "jokaisen iltakoosteen jälkeen", "eikä lataa mitään"),
            "zh": ("仍会", "每次晚间日报发出后", "不会下载任何东西")}
    by_itself = {"en": "by itself after the evening Brief", "fi": "itse iltakoosteen jälkeen",
                 "zh": "晚间日报发出后自动安装"}
    for language, table in TEXT.items():
        for words in says[language]:
            assert words in table["finish.updates.off-note"], (language, words)
        assert by_itself[language] in table["finish.updates.about"], language


def test_every_updates_text_is_in_all_three_languages():
    from family_brief.setup_server import updates as step
    keys = ["title", "version", "about", "on", "off-note", "saved.on", "saved.off", "save-failed",
            "closed", "newer", "waiting", "now",
            *(r for r in step.NOW_RESULTS if r != "no-update")]
    for language, table in TEXT.items():
        for key in keys:
            assert table.get(f"finish.updates.{key}", "").strip(), (language, key)
        for key in ("version", "newer", "updated", "up-to-date", "switched-back"):
            assert "{version}" in table[f"finish.updates.{key}"], (language, key)
        assert "{tried}" in table["finish.updates.switched-back"], language
        # The Brief sends a family here by the button's name (brief_text's update_available).
        from family_brief.brief_text import TEXT as BRIEF
        assert table["finish.updates.now"] in BRIEF[language].update_available, language
        # And so do the Brief's line on an update that waits, and the page's own sentence on it.
        assert table["finish.updates.now"] in BRIEF[language].update_waiting, language
        assert table["finish.updates.now"] in table["finish.updates.waiting"], language


def test_the_switch_is_saved_and_turned_on_again(household, github, page):
    r = switch(page.url, False)

    assert r.status == 200 and r.json()["result"] == "saved"
    assert r.json()["updates"]["on"] is False
    assert saved(household)["updates"] == {"enabled": False}
    assert updates(page.url)["on"] is False  # as a page opened later shows it
    assert "updates are off" in updates(page.url)["report"]

    r = switch(page.url, True)

    assert r.json()["result"] == "saved" and r.json()["updates"]["on"] is True
    assert saved(household)["updates"] == {"enabled": True}
    assert github.requests == []


def test_the_switch_takes_only_on_or_off(household, page):
    for body in [{}, {"on": "no"}, {"on": False, "version": "0.1.0"}, []]:
        r = call(page.url, "api/updates", method="POST", body=body)
        assert r.status == 400 and r.json()["result"] == "invalid-answers", body
    assert "updates" not in saved(household)


def test_the_switch_turned_off_survives_an_update(household, github, mac, page):  # noqa: F811
    switch(page.url, False)
    github.stable = "0.20.0"
    github.publish("0.20.0")

    out = update_now(page.url)

    assert out["result"] == "updated" and mac.version() == "0.20.0"
    assert saved(household)["updates"] == {"enabled": False}
    assert out["updates"]["on"] is False and out["updates"]["version"] == "0.20.0"


# ── Update now


def a_look_found(household, github, version: str) -> None:
    """An evening's look, with updates off, has found `version` on `stable`."""
    household.config["updates"] = {"enabled": False}
    config_file(household).write_text(yaml.safe_dump(household.config, allow_unicode=True))
    github.stable = version
    github.publish(version)
    assert update.look(str(config_file(household))).result == "available"


def test_a_newer_release_this_mac_has_seen_is_offered_and_update_now_installs_it(
        household, github, mac, page):  # noqa: F811
    assert updates(page.url)["newer"] is None
    a_look_found(household, github, "0.20.0")
    assert updates(page.url)["newer"] == "0.20.0"

    out = update_now(page.url)

    assert out == {"result": "updated", "version": "0.20.0", "tried": "0.20.0",
                   "updates": updates(page.url)}
    assert mac.version() == "0.20.0" and len(mac.installs) == 1
    assert out["updates"]["version"] == "0.20.0" and out["updates"]["newer"] is None
    assert github.hosts() <= set(update.HOSTS)


# ── A release that needs the family waits for Update now (#918)


def an_evening_left_it_for_the_family(household, github, version: str) -> None:
    """`version` needs the family, and an evening's update, a day after this Mac first saw it,
    read that from its verified zip and left it for them to start."""
    config_file(household).write_text(yaml.safe_dump(household.config, allow_unicode=True))
    github.stable = version
    github.publish(version, needing_family(version))
    config = str(config_file(household))
    assert update.update(config, by_family=False).result == "needs-family"


def test_a_release_that_waits_for_the_family_is_shown_with_what_it_asks_and_update_now_installs_it(
        household, github, mac, page):  # noqa: F811
    an_evening_left_it_for_the_family(household, github, "0.20.0")
    assert mac.version() == INSTALLED and mac.installs == []

    shown = updates(page.url)

    assert shown["newer"] == "0.20.0" and shown["on"] is True
    assert shown["waiting"] == {"version": "0.20.0", "asks": ASKS}
    assert "an update is waiting: version 0.20.0 needs you at the computer" in shown["report"]
    asked_before = len(github.requests)
    updates(page.url)
    assert len(github.requests) == asked_before  # showing it asks no host

    out = update_now(page.url)

    assert out == {"result": "updated", "version": "0.20.0", "tried": "0.20.0",
                   "updates": updates(page.url)}
    assert mac.version() == "0.20.0"
    # Started from the page, its installer runs with its dialogs.
    assert ["PARENT_RECAP_UPDATING" in env for env in mac.install_envs] == [False]
    assert out["updates"]["waiting"] is None and out["updates"]["newer"] is None


def test_a_waiting_release_whose_check_fails_switches_back_and_still_waits(
        household, github, mac, page):  # noqa: F811
    an_evening_left_it_for_the_family(household, github, "0.20.0")
    mac.fails["0.20.0"] = ["Calendar"]

    out = update_now(page.url)

    assert (out["result"], out["version"], out["tried"]) == ("switched-back", INSTALLED, "0.20.0")
    assert mac.version() == INSTALLED
    assert out["updates"]["waiting"] == {"version": "0.20.0", "asks": ASKS}


def test_a_release_that_needs_nobody_is_not_shown_as_waiting(household, github, mac, page):  # noqa: F811
    a_look_found(household, github, "0.20.0")

    assert updates(page.url)["waiting"] is None


def test_update_now_with_nothing_newer_out_says_it_is_up_to_date(household, github, mac, page):  # noqa: F811
    out = update_now(page.url)

    assert (out["result"], out["version"]) == ("up-to-date", INSTALLED)
    assert "tried" not in out and "reason" not in out and mac.installs == []


def test_update_now_that_fails_says_so_and_why(household, github, mac, page):  # noqa: F811
    github.stable = "0.20.0"
    github.publish("0.20.0", sig=b"not a signature")

    out = update_now(page.url)

    assert (out["result"], out["version"]) == ("failed", INSTALLED)
    assert "signature doesn't verify" in out["reason"]
    assert mac.version() == INSTALLED and mac.installs == []
    assert out["updates"]["newer"] == "0.20.0"  # still out, and still offered
    assert "0.20.0 refused" in out["updates"]["report"]  # what the family copies to us says so


def test_update_now_that_switches_back_says_which_version_runs(household, github, mac, page):  # noqa: F811
    github.stable = "0.20.0"
    github.publish("0.20.0")
    mac.fails["0.20.0"] = ["Calendar"]

    out = update_now(page.url)

    assert (out["result"], out["version"], out["tried"]) == ("switched-back", INSTALLED, "0.20.0")
    assert "Calendar" in out["reason"] and "switched back" in out["reason"]
    assert mac.version() == INSTALLED


def test_update_now_that_stops_says_so_and_never_leaves_the_page_updating(
        household, github, mac, page, monkeypatch):  # noqa: F811
    def crash(*_a, **_k):
        raise RuntimeError("disk full")
    monkeypatch.setattr(update, "update", crash)

    out = update_now(page.url)

    assert out["result"] == "failed" and "RuntimeError: disk full" in out["reason"]


def test_update_now_waits_while_a_brief_is_being_made(household, github, mac, page):  # noqa: F811
    github.stable = "0.20.0"
    github.publish("0.20.0")
    cfg = Config.load(config_file(household))

    with run_lock.exclusive(cfg):  # the evening run, whose own update runs under its lock
        r = call(page.url, "api/updates/now", method="POST", body={})

    assert r.json() == {"result": "busy"}
    assert github.requests == [] and mac.installs == []
    assert update_now(page.url)["result"] == "updated"  # once the run has ended


def test_update_now_takes_nothing_and_says_when_none_was_started(household, page):
    for path in ("api/updates/now", "api/updates/check"):
        for body in [{"version": "0.20.0"}, []]:
            assert call(page.url, path, method="POST", body=body).status == 400, path
    assert call(page.url, "api/updates/check", method="POST", body={}).json() == {
        "result": "no-update"}


# ── Windows, the hosted version and a program not run from an installed release


@pytest.mark.parametrize("on, hosted", [(WINDOWS, False), (LINUX, False), (MACOS, True)],
                         ids=["windows", "server", "hosted-household"])
def test_on_windows_and_for_a_hosted_household_the_page_gets_the_version_alone(
        harness, github, mac, page, on, hosted):  # noqa: F811
    harness.system = on
    connect_done(harness, hosted=hosted)

    shown = updates(page.url)

    # No `on` and no `newer`: the page shows no sentence, switch or button.
    assert shown == {"available": False, "version": failure.about()["version"],
                     "report": update.status(str(config_file(harness))).line()}
    assert "updates aren't available here" in shown["report"]
    # And the calls behind the switch and the button change nothing and ask no host.
    r = switch(page.url, False)
    assert r.status == 200 and r.json()["result"] == "not-available"
    assert call(page.url, "api/updates/now", method="POST", body={}).json() == {
        "result": "not-available"}
    assert "updates" not in saved(harness)
    assert github.requests == [] and mac.installs == []


def test_a_program_not_run_from_an_installed_release_gets_the_version_alone(harness, github, page):  # noqa: F811
    connect_done(harness)

    shown = updates(page.url)

    assert shown["available"] is False and shown["version"] == failure.about()["version"]
    assert set(shown) == {"available", "version", "report"}


# ── The report a family copies to us


def test_the_page_is_given_the_update_state_for_the_report_a_family_copies(household, github, page):
    # What Copy adds under a failure's reason is doctor's line; the browser test copies it.
    a_look_found(household, github, "0.20.0")

    report = updates(page.url)["report"]

    assert report.startswith(f"version {INSTALLED}; updates are off")
    assert "last looked " in report and "0.20.0 is out; not installed, as updates are off" in report


# ── A page opened again from Parent Recap Settings stays for the switch


def test_a_page_opened_again_after_setup_stays_open_where_the_program_updates_itself(
        harness, mac, schedule, opened_again):  # noqa: F811
    at_finish(harness)
    more_progress(harness, sources={"myclub": "done", "eepos": "done", "evaka": "done"})
    again = opened_again()
    finish(again.url)

    out = finished(again.url)

    assert out["result"] == "done" and out["stays"] is True
    assert switch(again.url, False).json()["result"] == "saved"  # still serving, for the switch
