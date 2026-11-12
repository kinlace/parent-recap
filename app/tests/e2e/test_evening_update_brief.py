"""The next Brief says what the evening's update did (ADR 0047, #916): that Parent Recap updated
itself and to which version, with a link to what changed, the first time also that it updates
itself and where to turn that off; or that an update failed and the version before still runs.

Driven through main() as test_evening_update.py drives the evening run, with its fake GitHub and
installed program. Each evening the run sends the Brief, or sends nothing, and then the update
runs, so what the update did shows in the Brief of a later evening. Assertions are on the Briefs
that go out, by email and iMessage."""
from __future__ import annotations

import json
from datetime import timedelta

import pytest

from conftest import NOW, html_for_golden, msg, without_unread_note
from fake_system import WINDOWS
from family_brief import update
from family_brief.brief_text import TEXT
from nights import PARTNER_EN
from test_evening_update import DAY, at, evening, release_out  # noqa: F401  the fixture
from test_update import INSTALLED, github, mac  # noqa: F401  the fixtures

pytestmark = pytest.mark.macos

CHANGES = "https://github.com/kinlace/parent-recap/blob/v{}/CHANGELOG.md"
UPDATE_MARK = "🔄"


def a_message(h, day: int) -> None:
    """A new Message on the evening `day` days after NOW, so that evening's Brief is sent."""
    when = NOW + day * DAY - timedelta(hours=2)
    h.sources = {"gmail": [msg("gmail", f"77{day}", when.isoformat(), "Library books are due on Friday.",
                               sender="library@kilo.example.fi", subject="Library books")]}
    h.model_reply = {"per_kid": [], "calendar_events": [],
                     "message_digest": "- Library books are due back on Friday"}


def quiet(h) -> None:
    h.sources = {}


def evening_on(h, day: int) -> None:
    with at(NOW + day * DAY):
        assert h.run() == 0


def installed_on_evening_1(h, github, mac) -> None:  # noqa: F811
    """0.20.0 is out on NOW's evening and installed by the next evening's run, after its Brief."""
    release_out(github, "0.20.0")
    quiet(h)
    evening_on(h, 0)
    evening_on(h, 1)
    assert mac.version() == "0.20.0"


def top(text: str) -> list[str]:
    """The notes at the top of a Brief's text, under its header."""
    return text.split("\n\n")[1].split("\n")


# ---------------------------------------------------------------- after an update

@pytest.mark.parametrize("language", ["en", "zh", "fi"])
def test_the_brief_after_an_update_says_so_once_with_what_changed_and_that_it_updates_itself(
        evening, github, mac, golden, language):  # noqa: F811
    t = TEXT[language]
    evening.config["summary_language"] = language
    installed_on_evening_1(evening, github, mac)
    assert evening.sent == []  # quiet evenings: the update's line waits for a Brief

    a_message(evening, 2)
    evening_on(evening, 2)

    [email] = evening.sent
    assert top(email.text) == [t.updated.format(version="0.20.0", link=CHANGES.format("0.20.0")),
                               t.updates_itself]
    assert f'<a href="{CHANGES.format("0.20.0")}"' in email.html
    golden(f"evening_updated.{language}.txt", without_unread_note(email.text))
    golden(f"evening_updated.{language}.html", html_for_golden(email.html))

    a_message(evening, 3)
    evening_on(evening, 3)
    assert len(evening.sent) == 2 and UPDATE_MARK not in evening.sent[-1].text


def test_a_later_update_s_line_doesn_t_say_again_that_it_updates_itself(evening, github, mac):  # noqa: F811
    t = TEXT["en"]
    evening.config["summary_language"] = "en"
    installed_on_evening_1(evening, github, mac)
    a_message(evening, 2)
    release_out(github, "0.21.0")
    evening_on(evening, 2)  # the Brief says 0.20.0, then this Mac first sees 0.21.0
    quiet(evening)
    evening_on(evening, 3)
    assert mac.version() == "0.21.0"

    a_message(evening, 4)
    evening_on(evening, 4)

    assert top(evening.sent[-1].text) == [t.updated.format(version="0.21.0", link=CHANGES.format("0.21.0"))]


def test_the_line_says_the_version_it_runs_after_two_updates_without_a_brief(evening, github, mac):  # noqa: F811
    t = TEXT["en"]
    evening.config["summary_language"] = "en"
    installed_on_evening_1(evening, github, mac)
    release_out(github, "0.21.0")
    evening_on(evening, 2)
    evening_on(evening, 3)
    assert mac.version() == "0.21.0" and evening.sent == []

    a_message(evening, 4)
    evening_on(evening, 4)

    [email] = evening.sent
    assert top(email.text) == [t.updated.format(version="0.21.0", link=CHANGES.format("0.21.0")),
                               t.updates_itself]


def test_after_a_switch_back_by_hand_the_brief_doesn_t_say_it_updated(evening, github, mac):  # noqa: F811
    installed_on_evening_1(evening, github, mac)
    assert evening.cli("switch-back") == 0
    assert mac.version() == INSTALLED
    github.stable = INSTALLED  # as the maintainers would after a bad release: nothing to update to

    a_message(evening, 2)
    evening_on(evening, 2)

    [email] = evening.sent
    assert UPDATE_MARK not in email.text
    # Switched forward again by hand, as `switch-back` does run again: that's no update either.
    assert evening.cli("switch-back") == 0
    assert mac.version() == "0.20.0"
    a_message(evening, 3)
    evening_on(evening, 3)
    assert UPDATE_MARK not in evening.sent[-1].text


def test_a_brief_that_reached_nobody_leaves_the_line_for_the_next(evening, github, mac):  # noqa: F811
    from family_brief.gateway import GatewayError
    t = TEXT["en"]
    evening.config["summary_language"] = "en"
    installed_on_evening_1(evening, github, mac)
    evening.email_error = GatewayError("couldn't reach the gateway at gateway.parentrecap.com (ConnectionError)")
    a_message(evening, 2)
    evening_on(evening, 2)
    assert evening.sent == []

    evening.email_error = None
    a_message(evening, 3)
    evening_on(evening, 3)

    [email] = evening.sent
    assert top(email.text) == [t.updated.format(version="0.20.0", link=CHANGES.format("0.20.0")),
                               t.updates_itself]


# ---------------------------------------------------------------- after a failed update

def test_the_brief_after_an_update_that_failed_its_check_says_the_old_version_still_runs_once(
        evening, github, mac, golden):  # noqa: F811
    t = TEXT["en"]
    evening.config["summary_language"] = "en"
    release_out(github, "0.20.0")
    mac.fails["0.20.0"] = ["Calendar"]
    evening_on(evening, 0)
    evening_on(evening, 1)
    assert mac.version() == INSTALLED and len(mac.installs) == 1

    a_message(evening, 2)
    evening_on(evening, 2)

    [email] = evening.sent
    assert top(email.text) == [t.update_failed.format(version="0.20.0", running=INSTALLED)]
    golden("evening_update_failed.en.txt", without_unread_note(email.text))
    golden("evening_update_failed.en.html", html_for_golden(email.html))

    a_message(evening, 3)
    evening_on(evening, 3)
    assert len(evening.sent) == 2 and UPDATE_MARK not in evening.sent[-1].text


def test_a_zip_that_doesn_t_verify_is_said_once_though_each_evening_tries_it_again(
        evening, github, mac):  # noqa: F811
    t = TEXT["en"]
    evening.config["summary_language"] = "en"
    release_out(github, "0.20.0")
    github.publish("0.20.0", sig=b"not a signature")
    evening_on(evening, 0)
    evening_on(evening, 1)

    a_message(evening, 2)
    evening_on(evening, 2)  # says so, then downloads it again and it fails again
    a_message(evening, 3)
    evening_on(evening, 3)

    first, second = evening.sent
    assert top(first.text) == [t.update_failed.format(version="0.20.0", running=INSTALLED)]
    assert UPDATE_MARK not in second.text
    assert mac.installs == [] and mac.version() == INSTALLED


def test_a_failed_update_after_an_update_not_yet_told_shows_both_lines(evening, github, mac):  # noqa: F811
    t = TEXT["en"]
    evening.config["summary_language"] = "en"
    installed_on_evening_1(evening, github, mac)
    release_out(github, "0.21.0")
    mac.fails["0.21.0"] = ["WhatsApp"]
    evening_on(evening, 2)
    evening_on(evening, 3)
    assert mac.version() == "0.20.0" and evening.sent == []

    a_message(evening, 4)
    evening_on(evening, 4)

    [email] = evening.sent
    assert top(email.text) == [t.updated.format(version="0.20.0", link=CHANGES.format("0.20.0")),
                               t.updates_itself,
                               t.update_failed.format(version="0.21.0", running="0.20.0")]


def test_a_network_that_is_down_during_the_update_is_no_failed_update(evening, github, mac, monkeypatch):  # noqa: F811
    import requests
    release_out(github, "0.20.0")
    evening_on(evening, 0)

    def down(*_a, **_k):
        raise requests.ConnectionError("network is unreachable")
    monkeypatch.setattr(requests.Session, "request", down)
    evening_on(evening, 1)

    a_message(evening, 2)
    evening_on(evening, 2)
    assert UPDATE_MARK not in evening.sent[-1].text


# ---------------------------------------------------------------- each Recipient, every form

def test_each_recipient_reads_the_line_in_their_language_and_the_imessage_carries_it(
        evening, github, mac):  # noqa: F811
    evening.config["summary_language"] = "zh"
    evening.config["email"]["to"] = ["parent@example.com", PARTNER_EN, {"address": "mummo@example.com",
                                                                        "language": "fi"}]
    evening.config["imessage"] = {"enabled": True, "recipients": ["+358401234567"]}
    installed_on_evening_1(evening, github, mac)

    a_message(evening, 2)
    reply = evening.model_reply
    english = {**reply, "message_digest": "- Library books are due back on Friday"}
    finnish = {**reply, "message_digest": "- Kirjaston kirjat palautetaan perjantaina"}
    evening.model_reply = [reply, english, finnish]
    evening_on(evening, 2)

    link = CHANGES.format("0.20.0")
    by_language = {"zh": "parent@example.com", "en": "partner@example.com", "fi": "mummo@example.com"}
    for language, address in by_language.items():
        [email] = [e for e in evening.sent if address in e.to]
        t = TEXT[language]
        assert top(email.text)[:2] == [t.updated.format(version="0.20.0", link=link), t.updates_itself]
    [(_, imessage)] = evening.imessages
    assert top(imessage)[:2] == [TEXT["zh"].updated.format(version="0.20.0", link=link),
                                 TEXT["zh"].updates_itself]


def test_the_line_is_the_program_s_own_and_never_shown_to_the_model(evening, github, mac):  # noqa: F811
    installed_on_evening_1(evening, github, mac)
    a_message(evening, 2)
    evening_on(evening, 2)

    assert evening.model_calls
    for call in evening.model_calls:
        assert "0.20.0" not in call.prompt + call.system_prompt


# ---------------------------------------------------------------- with a catch-up

def test_a_catch_up_brief_shows_its_line_and_the_update_s(evening, github, mac):  # noqa: F811
    t = TEXT["en"]
    evening.config["summary_language"] = "en"
    installed_on_evening_1(evening, github, mac)
    # The computer was off at the next evening's Brief time, and comes back on three hours later.
    last = (NOW + DAY).isoformat()
    evening.state_path.write_text(json.dumps({"last_run_at": last, "caught_up_at": last}))
    a_message(evening, 2)
    with at(NOW + 2 * DAY + timedelta(hours=3)):
        assert evening.run() == 0

    [email] = evening.sent
    assert top(email.text) == [t.caught_up,
                               t.updated.format(version="0.20.0", link=CHANGES.format("0.20.0")),
                               t.updates_itself]
    assert t.caught_up in email.html and CHANGES.format("0.20.0") in email.html


# ---------------------------------------------------------------- never on the hosted version or Windows

def a_note_waits(mac) -> None:  # noqa: F811
    """The update state of an update to the running version, not yet told, as if it ran."""
    path = mac.folder / update.UPDATE / update.STATE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"tell_updated": INSTALLED,
                                "tell_failed": {"version": "0.20.0", "running": INSTALLED}}))


def test_a_note_left_by_an_update_shows_on_a_mac(evening, mac):  # noqa: F811
    # What the two tests below change from.
    a_note_waits(mac)
    a_message(evening, 0)

    evening_on(evening, 0)

    [email] = evening.sent
    assert email.text.count(UPDATE_MARK) == 2


def test_a_hosted_household_s_brief_never_carries_the_line(evening, mac):  # noqa: F811
    evening.config["hosted"] = True
    a_note_waits(mac)
    a_message(evening, 0)

    evening_on(evening, 0)

    [email] = evening.sent
    assert UPDATE_MARK not in email.text and UPDATE_MARK not in email.html


def test_a_windows_household_s_brief_never_carries_the_line(evening, mac):  # noqa: F811
    evening.system = WINDOWS
    a_note_waits(mac)
    a_message(evening, 0)

    evening_on(evening, 0)

    [email] = evening.sent
    assert UPDATE_MARK not in email.text and UPDATE_MARK not in email.html
