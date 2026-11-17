"""The Run Report sent to the maintainers' gateway after each evening run (ADR 0048, #923): on by
default for a Household that sends its Brief through the gateway, off for one that sends it from
its own Gmail (ADR 0034), as `run_report.enabled` says when it says anything. What leaves is the
file the run wrote, byte for byte, with the Gateway Key or a report id made once. A report the
gateway didn't take goes with the next run's, until it is a week old, and never touches the
Brief or the run's exit code. `doctor` says whether it is on and when one was last sent.

The gateway is the outbox's (fake_outbox.py). Assertions are on the reports it took, the files in
the run's folder, the Brief sent, the run's exit code and doctor's line."""
from __future__ import annotations

import json
import re
from datetime import timedelta

import pytest
import time_machine

from conftest import GATEWAY_KEY, NOW
from family_brief import gateway as api
from nights import normal_night
from test_gateway_sends import doctor, no_key
from test_gmail_sends import own_gmail  # noqa: F401  the fixture

NO_ANSWER = api.GatewayError("couldn't reach the gateway at gateway.parentrecap.com (ConnectionError)")


def last_run_file(h) -> bytes:
    return h.state_path.with_name("last-run.json").read_bytes()


def waiting(h) -> list:
    folder = h.state_path.with_name("run-reports")
    return sorted(folder.glob("*.json")) if folder.is_dir() else []


def test_a_household_that_sends_through_the_gateway_sends_its_report_by_default(harness):
    normal_night(harness, "en")

    assert harness.run() == 0

    [report] = harness.reports
    assert report.raw == last_run_file(harness)  # exactly the file the run wrote
    assert report.json()["brief"] == {"outcome": "sent"}
    assert report.headers == {"x-api-key": GATEWAY_KEY}
    assert waiting(harness) == []


def test_turned_off_nothing_is_sent_and_nothing_waits(harness):
    normal_night(harness, "en")
    harness.config["run_report"] = {"enabled": False}
    harness.report_answer = NO_ANSWER

    assert harness.run() == 0

    assert harness.reports == []
    assert waiting(harness) == []
    assert len(harness.sent) == 1


def test_a_household_that_sends_from_its_own_gmail_sends_none_by_default(own_gmail):
    assert own_gmail.run() == 0

    assert own_gmail.reports == []
    assert waiting(own_gmail) == []
    assert own_gmail.sent[0].via == "gmail"


def test_turned_on_from_its_own_gmail_it_reports_under_an_id_made_once(own_gmail):
    own_gmail.config["run_report"] = {"enabled": True}

    assert own_gmail.run() == 0
    assert own_gmail.run() == 0

    first, second = own_gmail.reports
    assert set(first.headers) == {"x-report-id"}
    assert re.fullmatch(r"[0-9a-f]{32}", first.headers["x-report-id"])
    assert second.headers == first.headers
    assert own_gmail.sent[0].via == "gmail"


def test_a_report_the_gateway_did_not_answer_goes_with_the_next_run_s(harness):
    normal_night(harness, "en")
    harness.report_answer = NO_ANSWER

    assert harness.run() == 0

    assert len(harness.sent) == 1  # the Brief went out all the same
    [kept] = waiting(harness)
    assert kept.read_bytes() == last_run_file(harness)
    missed = kept.read_bytes()

    harness.report_answer = 200
    assert harness.run() == 0

    first, second = harness.reports
    assert first.raw == missed
    assert second.raw == last_run_file(harness)
    assert waiting(harness) == []


@pytest.mark.parametrize("answer", [500, 429, 401, NO_ANSWER])
def test_a_gateway_that_refuses_or_does_not_answer_leaves_the_run_as_it_was(harness, answer):
    normal_night(harness, "en")
    harness.report_answer = answer

    assert harness.run() == 0

    assert len(harness.sent) == 1
    assert json.loads(last_run_file(harness))["exit"] == 0
    assert len(waiting(harness)) == 1


def test_a_report_the_gateway_will_never_take_is_dropped(harness):
    normal_night(harness, "en")
    harness.report_answer = 400

    assert harness.run() == 0

    assert waiting(harness) == []


def test_a_waiting_report_over_a_week_old_is_dropped(harness):
    harness.report_answer = NO_ANSWER
    with time_machine.travel(NOW - timedelta(days=8), tick=False):
        assert harness.run() == 0
    with time_machine.travel(NOW - timedelta(days=2), tick=False):
        assert harness.run() == 0
    _eight_days_ago, two_days_ago = [p.read_bytes() for p in waiting(harness)]

    harness.report_answer = 200
    assert harness.run() == 0

    assert [r.raw for r in harness.reports] == [two_days_ago, last_run_file(harness)]
    assert waiting(harness) == []


def test_a_run_that_crashed_sends_its_report_too(harness, monkeypatch):
    from family_brief import run as evening
    def crash(*_a, **_k):
        raise RuntimeError("the disk is full")
    monkeypatch.setattr(evening, "_read", crash)

    with pytest.raises(RuntimeError):
        harness.run()

    [report] = harness.reports
    assert report.json()["exit"] == 1
    assert report.json()["exception"]["type"] == "RuntimeError"


def test_a_dry_run_sends_nothing(harness):
    normal_night(harness, "en")

    assert harness.run("--dry-run") == 0

    assert harness.reports == []


def test_doctor_says_the_report_is_on_and_when_the_last_one_was_sent(harness, capsys):
    normal_night(harness, "en")
    assert doctor(harness, capsys)["Run Report"].endswith("none sent yet")

    harness.run()
    capsys.readouterr()
    with time_machine.travel(NOW + timedelta(hours=3), tick=False):
        line = doctor(harness, capsys)["Run Report"]

    assert "on:" in line and "the last one was sent 3 hours ago" in line


def test_doctor_says_how_many_wait_to_be_sent(harness, capsys):
    harness.report_answer = NO_ANSWER
    harness.run()
    capsys.readouterr()

    line = doctor(harness, capsys)["Run Report"]

    assert "none sent yet" in line and "1 waiting to be sent with the next run's" in line


def test_doctor_says_the_report_is_off_for_a_household_on_its_own_gmail(own_gmail, capsys):
    line = doctor(own_gmail, capsys)["Run Report"]

    assert "off, as the Brief goes from your own Gmail" in line


def test_doctor_says_the_report_was_turned_off(harness, capsys):
    harness.config["run_report"] = {"enabled": False}

    assert "turned off (run_report.enabled in the config)" in doctor(harness, capsys)["Run Report"]


def test_a_household_without_a_key_on_the_gateway_reports_under_its_id(harness):
    normal_night(harness, "en")
    no_key(harness)
    harness.key_answer = (503, {"error": "busy"})  # nor does it get one tonight

    harness.run()

    [report] = harness.reports
    assert set(report.headers) == {"x-report-id"}


def test_a_key_the_gateway_no_longer_knows_reports_under_the_id(own_gmail):
    # A Household that sends from its own Gmail, with a key from before kept unused and revoked.
    own_gmail.secrets[api.ACCOUNT] = GATEWAY_KEY
    own_gmail.outbox.revoked_keys.add(GATEWAY_KEY)
    own_gmail.config["run_report"] = {"enabled": True}

    assert own_gmail.run() == 0

    [report] = own_gmail.reports
    assert set(report.headers) == {"x-report-id"}
    assert waiting(own_gmail) == []


def test_a_household_that_got_its_key_tonight_reports_under_it(harness):
    normal_night(harness, "en")
    no_key(harness)

    harness.run()

    [made] = harness.made_keys
    [report] = harness.reports
    assert report.headers == {"x-api-key": made}
