"""The setup page's Finish step (ADR 0006): the evening job and the wake-up, then the outcome
checklist.

The server runs in-process on a free port, inside the harness, as `setup_page.py` starts it, so the
config, the progress record, the secrets, the jobs, the wake and the desktop are the harness's.
Assertions are on what the page and the family see: the responses, the config and progress written,
and what was opened."""
from __future__ import annotations

import json
import re
import threading
from typing import Any

import pytest

from family_brief import ops, setup_server, setup_status
from family_brief.system import jobs, wake
from fake_system import FakeWake
from setup_page import (PAGE_DIR, Response, at_finish, call, config_file, finish, finished, more_progress,
                        refused, wait_for)
from setup_page import _listening, clock, health, page, schedule  # noqa: F401
from test_setup_whatsapp import (WHATSAPP_FAILURES, WHATSAPP_PYTHON, assert_says_why, back_to_connect,
                                 back_to_whatsapp)


# ── Finish: the evening job and the wake-up, then the outcome checklist


OTHER_WAKE = "wake every weekday at 07:00"


def checklist(out: dict[str, Any]) -> dict[str, bool]:
    return {o["outcome"]: o["ok"] for o in out["outcomes"]}


def test_finish_installs_the_evening_job_and_the_wake_up_through_macos_s_dialog(harness, page,
                                                                               schedule, health):
    at_finish(harness)

    r = finish(page.url)

    assert r.status == 200 and r.json() == {"result": "installing"}
    out = finished(page.url)
    assert out["result"] == "done" and out["wake"] == "set"
    assert checklist(out) == {"installed": True, "doctor": True, "brief": True, "nightly": True,
                              "wake": True}
    assert jobs.DAILY in harness.jobs.installed()
    # The Mac password goes into macOS's own dialog only.
    assert schedule.dialogs == ["set 20:55"]
    assert harness.wake.wakes == [FakeWake.line(21, 0)]
    # Doctor's checks run with the evening job's own, now that it's installed.
    assert health.runs[-1][1] is True


def test_finish_takes_no_password(page):
    for body in [{}, {"replace_wake": "yes"}, {"replace_wake": False, "password": "hunter2"},
                 ["replace_wake"]]:
        r = call(page.url, "api/finish", method="POST", body=body)
        assert r.status == 400 and r.json()["result"] == "invalid-answers", body
    html_text = (PAGE_DIR / "index.html").read_text()
    finish_section = html_text.split('<section id="finish"', 1)[1].split("</section>", 1)[0]
    # Only the choice of AI, whose sign-ins are Connect's own.
    assert all('type="radio" name="finish-ai"' in field
               for field in re.findall(r"<input[^>]*>", finish_section))


def test_the_server_stops_once_every_outcome_is_true(harness, page, schedule):
    at_finish(harness)

    finish(page.url)
    assert finished(page.url)["result"] == "done"

    wait_for(lambda: refused(page.url))


def test_an_outcome_still_false_keeps_the_page_and_says_which(harness, page, schedule):
    at_finish(harness, delivered=False)

    finish(page.url)
    out = finished(page.url)

    assert out["result"] == "not-done"
    assert checklist(out) == {"installed": True, "doctor": True, "brief": False, "nightly": True,
                              "wake": True}
    # Only each outcome and whether it's true: their reasons name addresses, in English.
    assert "parent@example.com" not in json.dumps(out)
    assert call(page.url, "api/state").status == 200  # still serving


def test_a_failed_health_check_names_the_checks_that_aren_t_ok(harness, page, schedule, health):
    at_finish(harness)
    health.results += [(ops.FAIL, "gmail", "Gmail", "IMAP login failed for parent@example.com"),
                       (ops.WARN, "last-run", "Last run", "40 hours ago"),
                       ops.Check(ops.FAIL, "myclub", "MyClub (Mia)",
                                 "subscription link doesn't open: secret", subject="Mia"),
                       (ops.WARN, "something-new", "Something new", "a check the page doesn't know")]

    finish(page.url)
    out = finished(page.url)

    assert out["result"] == "not-done"
    doctor = next(o for o in out["outcomes"] if o["outcome"] == "doctor")
    assert doctor == {"outcome": "doctor", "ok": False, "checks": [
        {"check": "gmail", "status": "fail"}, {"check": "last-run", "status": "warn"},
        {"check": "myclub", "status": "fail", "kid": "Mia"}, {"check": "other", "status": "warn"}]}
    # Only their names: doctor's details can name an address or quote an error.
    assert "parent@example.com" not in json.dumps(out) and "secret" not in json.dumps(out)
    # The outcomes that are true and have no warnings carry nothing more.
    assert all(set(o) == {"outcome", "ok"} for o in out["outcomes"] if o["ok"])


def test_warnings_alone_dont_hold_finish_up_and_are_listed(harness, page, schedule, health):
    at_finish(harness)
    health.results += [(ops.WARN, "feedback", "Pilot feedback", "feedback.household_label is empty"),
                       ops.Check(ops.WARN, "language", "Language sv",
                                 "the Brief's own text in sv isn't ready yet", subject="sv")]

    finish(page.url)
    out = finished(page.url)

    assert out["result"] == "done"
    doctor = next(o for o in out["outcomes"] if o["outcome"] == "doctor")
    assert doctor == {"outcome": "doctor", "ok": True, "checks": [
        {"check": "feedback", "status": "warn"},
        {"check": "language", "status": "warn", "language": "sv"}]}


def test_finish_names_a_check_by_its_key_whatever_doctor_calls_it(harness, page, schedule, health):
    # #414: a check renamed in doctor's words still shows as itself, not as `other`.
    at_finish(harness)
    health.results += [(ops.FAIL, "gmail", "Email inbox", "IMAP login failed"),
                       ops.Check(ops.WARN, "language", "Brief text in sv", "not ready yet",
                                 subject="sv"),
                       ops.Check(ops.FAIL, "myclub", "Club calendar for Mia", "doesn't open",
                                 subject="Mia")]

    finish(page.url)
    out = finished(page.url)

    doctor = next(o for o in out["outcomes"] if o["outcome"] == "doctor")
    assert doctor["checks"] == [{"check": "gmail", "status": "fail"},
                                {"check": "language", "status": "warn", "language": "sv"},
                                {"check": "myclub", "status": "fail", "kid": "Mia"}]


@pytest.mark.parametrize("failure", WHATSAPP_FAILURES)
def test_finish_says_why_whatsapp_isn_t_ok(harness, page, schedule, health, failure):
    at_finish(harness)
    status, reason = WHATSAPP_FAILURES[failure]
    groups = ("Leo piano",) if reason == "missing-groups" else ()
    health.results.append((status, "whatsapp", "WhatsApp", f"{WHATSAPP_PYTHON} can't", reason,
                           groups))

    finish(page.url)
    out = finished(page.url)

    doctor = next(o for o in out["outcomes"] if o["outcome"] == "doctor")
    check = {"check": "whatsapp", "status": setup_server.HEALTH_STATUSES[status], "reason": reason,
             **({"groups": list(groups)} if groups else {})}
    assert doctor == {"outcome": "doctor", "ok": status == ops.WARN, "checks": [check]}
    assert WHATSAPP_PYTHON not in json.dumps(out)
    assert_says_why("finish", check)


def test_finish_leads_back_to_whatsapp_s_step_while_the_page_is_open(harness, page, schedule,
                                                                      health):
    at_finish(harness, delivered=False)  # so the page stays open
    health.results.append((ops.WARN, "whatsapp", "WhatsApp", "", "missing-groups", ("3B parents",)))
    finish(page.url)
    assert finished(page.url)["result"] == "not-done"

    r = back_to_whatsapp(page.url)

    assert r.status == 200
    assert (r.json()["progress"]["phase"], r.json()["progress"]["source"]) == ("connect", "whatsapp")
    # Finish checks again once setup is back there.
    assert call(page.url, "api/finish/check", method="POST", body={}).json() == \
        {"result": "no-finish"}


def test_finish_leads_back_to_connect_s_list_for_a_skipped_source(harness, page, schedule):
    at_finish(harness, delivered=False)  # so the page stays open; MyClub skipped
    finish(page.url)
    assert finished(page.url)["result"] == "not-done"

    r = back_to_connect(page.url)

    assert r.status == 200
    assert (r.json()["progress"]["phase"], r.json()["progress"]["source"]) == ("connect", None)
    assert call(page.url, "api/finish/check", method="POST", body={}).json() == \
        {"result": "no-finish"}


@pytest.fixture
def opened_again(harness, clock):
    """Starts the page again, as the install line does once setup has got as far as it has."""
    servers: list[tuple[setup_server.SetupServer, threading.Thread]] = []

    def start() -> setup_server.SetupServer:
        server = setup_server.SetupServer(config_file(harness))
        thread = threading.Thread(target=server.serve_until_idle, daemon=True)
        thread.start()
        servers.append((server, thread))
        return server
    yield start
    for server, thread in servers:
        server.stop()
        thread.join(5)


def test_a_page_opened_again_after_setup_stays_open_to_add_a_skipped_source(harness, page,
                                                                          schedule, opened_again):
    at_finish(harness)  # MyClub skipped
    finish(page.url)
    assert finished(page.url)["result"] == "done"
    wait_for(lambda: refused(page.url))

    again = opened_again()
    call(again.url, "api/finish/outcomes", method="POST", body={})
    out = finished(again.url)

    assert out["result"] == "done" and out["stays"] is True
    assert call(again.url, "api/state").status == 200  # still serving, for the way back
    r = back_to_connect(again.url)
    assert r.status == 200 and r.json()["progress"]["phase"] == "connect"


def test_a_page_opened_again_after_setup_with_nothing_skipped_stops_once_all_is_true(
        harness, schedule, opened_again):
    at_finish(harness)
    more_progress(harness, sources={"myclub": "done"})
    page = opened_again()
    finish(page.url)
    out = finished(page.url)

    assert out["result"] == "done" and "stays" not in out
    wait_for(lambda: refused(page.url))


def test_finish_for_now_stops_the_page_once_a_try_has_ended(harness, page, schedule):
    at_finish(harness, delivered=False)
    finish(page.url)
    assert finished(page.url)["result"] == "not-done"

    for body in [{"x": 1}, []]:
        assert call(page.url, "api/finish/stop", method="POST", body=body).status == 400
    r = call(page.url, "api/finish/stop", method="POST", body={})

    assert r.status == 200 and r.json() == {"result": "stopped"}
    wait_for(lambda: refused(page.url))


def test_finish_for_now_waits_while_finish_is_installing(harness, page, schedule):
    at_finish(harness)
    schedule.go.clear()
    finish(page.url)
    wait_for(lambda: schedule.dialogs)

    r = call(page.url, "api/finish/stop", method="POST", body={})

    assert r.status == 200 and r.json() == {"result": "installing"}
    assert call(page.url, "api/state").status == 200  # still serving: the install isn't cut off
    schedule.go.set()
    finished(page.url)


def test_finish_for_now_is_only_for_finish(harness, page, schedule):
    at_finish(harness)
    more_progress(harness, phase="first-brief")

    r = call(page.url, "api/finish/stop", method="POST", body={})

    assert r.status == 400 and r.json()["result"] == "invalid-answers"
    assert call(page.url, "api/state").status == 200


def test_another_wake_schedule_is_replaced_only_once_the_family_agrees(harness, page, schedule):
    at_finish(harness)
    harness.wake.wakes = [OTHER_WAKE]

    finish(page.url)
    out = finished(page.url)

    assert out["result"] == "not-done" and out["wake"] == "other-schedule"
    assert out["other"] == [OTHER_WAKE]
    assert schedule.dialogs == []  # the family decides before any dialog opens
    assert checklist(out)["wake"] is False and checklist(out)["nightly"] is True

    finish(page.url, replace_wake=True)
    out = finished(page.url)
    assert out["result"] == "done" and out["wake"] == "set"
    assert len(schedule.dialogs) == 1


@pytest.mark.parametrize("dialog, result", [("cancel", "cancelled"), ("none", "no-dialog")])
def test_a_wake_up_that_isn_t_set_says_why_and_can_be_tried_again(harness, page, schedule, dialog,
                                                                  result):
    at_finish(harness)
    harness.wake.dialog = dialog

    finish(page.url)
    out = finished(page.url)

    assert out["result"] == "not-done" and out["wake"] == result
    assert checklist(out)["wake"] is False
    assert "sudo" not in json.dumps(out)  # the password goes into macOS's dialog only
    harness.wake.dialog = "allow"
    finish(page.url)
    assert finished(page.url)["result"] == "done"


def test_a_mac_that_never_sleeps_needs_no_wake_up(harness, page, schedule):
    at_finish(harness)
    harness.wake.never_sleeps = True

    finish(page.url)
    out = finished(page.url)

    assert out["result"] == "done" and out["wake"] == "never-sleeps"
    assert schedule.dialogs == []


def test_while_macos_asks_for_the_password_the_page_waits(harness, page, schedule):
    at_finish(harness)
    schedule.go.clear()

    finish(page.url)

    wait_for(lambda: schedule.dialogs)
    assert call(page.url, "api/finish/check", method="POST", body={}).json() == {
        "result": "installing"}
    assert finish(page.url).json() == {"result": "installing"}  # not asked twice
    schedule.go.set()
    assert finished(page.url)["result"] == "done"
    assert len(schedule.dialogs) == 1


def test_jobs_that_cannot_be_loaded_say_so(harness, page, schedule):
    at_finish(harness)
    schedule.load_fails = True

    finish(page.url)

    assert finished(page.url) == {"result": "install-failed"}
    assert schedule.dialogs == []
    schedule.load_fails = False
    finish(page.url)
    assert finished(page.url)["result"] == "done"


def test_finish_waits_for_the_first_brief(harness, page, schedule):
    at_finish(harness)
    more_progress(harness, phase="first-brief")

    for path in ["api/finish", "api/finish/outcomes"]:
        body = {"replace_wake": False} if path == "api/finish" else {}
        r = call(page.url, path, method="POST", body=body)
        assert r.status == 400 and r.json()["result"] == "invalid-answers", path
    assert call(page.url, "api/finish/check", method="POST", body={}).json() == {
        "result": "no-finish"}
    assert schedule.loads == [] and schedule.dialogs == []


def test_a_finished_household_sees_only_the_checklist(harness, page, schedule):
    at_finish(harness)
    harness.jobs.add(jobs.DAILY)
    harness.wake.wakes = [FakeWake.line(21, 0)]

    r = call(page.url, "api/finish/outcomes", method="POST", body={})

    assert r.status == 200 and r.json() == {"result": "checking"}
    out = finished(page.url)
    assert out["result"] == "done" and "wake" not in out
    assert all(checklist(out).values())
    # Checked only: nothing installed again, and no dialog.
    assert schedule.loads == [] and schedule.dialogs == []
    wait_for(lambda: refused(page.url))


def test_a_household_not_finished_yet_is_checked_without_installing(harness, page, schedule):
    at_finish(harness)

    call(page.url, "api/finish/outcomes", method="POST", body={})
    out = finished(page.url)

    assert out["result"] == "not-done"
    assert checklist(out)["nightly"] is False and checklist(out)["wake"] is False
    assert schedule.loads == [] and schedule.dialogs == []
    for body in [{"x": 1}, []]:
        assert call(page.url, "api/finish/outcomes", method="POST", body=body).status == 400


def check_again(url: str) -> Response:
    return call(url, "api/finish/outcomes", method="POST", body={"again": True})


def test_check_again_rechecks_without_installing_or_touching_the_wake_up(harness, page, schedule,
                                                                         health):
    at_finish(harness)
    health.results += [(ops.FAIL, "claude", "Claude", "call failed")]
    finish(page.url)
    assert finished(page.url)["result"] == "not-done"
    loads, dialogs = list(schedule.loads), list(schedule.dialogs)
    runs = len(health.runs)
    health.results.pop()

    r = check_again(page.url)

    assert r.status == 200 and r.json() == {"result": "checking"}
    out = finished(page.url)
    assert out["result"] == "done" and all(checklist(out).values())
    assert len(health.runs) == runs + 1  # the health check ran again
    # Checked only: the evening job isn't loaded again, and no dialog sets the wake-up.
    assert schedule.loads == loads and schedule.dialogs == dialogs
    wait_for(lambda: refused(page.url))  # every outcome true: All done, and the page stops


def test_check_again_keeps_saying_what_s_still_missing(harness, page, schedule, health):
    at_finish(harness)
    health.results += [(ops.FAIL, "claude", "Claude", "call failed")]
    finish(page.url)
    finished(page.url)

    check_again(page.url)
    out = finished(page.url)

    assert out["result"] == "not-done" and out["tried"] is True and out["wake"] == "set"
    doctor = next(o for o in out["outcomes"] if o["outcome"] == "doctor")
    assert doctor["ok"] is False and {"check": "ai", "status": "fail"} in doctor["checks"]
    assert call(page.url, "api/state").status == 200  # still serving
    for body in [{"again": False}, {"again": "yes"}, {"again": 1}, {"again": True, "x": 1}]:
        assert call(page.url, "api/finish/outcomes", method="POST", body=body).status == 400


def test_check_again_still_offers_to_replace_another_wake_schedule(harness, page, schedule):
    at_finish(harness)
    harness.wake.wakes = [OTHER_WAKE]
    finish(page.url)
    finished(page.url)

    check_again(page.url)
    out = finished(page.url)

    assert out["result"] == "not-done" and out["wake"] == "other-schedule"
    assert out["other"] == [OTHER_WAKE] and schedule.dialogs == []


def test_check_again_before_turning_it_on_says_what_s_missing_without_installing(
        harness, page, schedule):
    at_finish(harness)

    check_again(page.url)
    out = finished(page.url)

    assert out["result"] == "not-done" and out["tried"] is True
    assert checklist(out)["nightly"] is False
    assert schedule.loads == [] and schedule.dialogs == []


def test_finish_offers_check_again_in_every_language():
    html_text = (PAGE_DIR / "index.html").read_text()
    finish_section = html_text.split('<section id="finish"', 1)[1].split("</section>", 1)[0]
    text = json.loads((PAGE_DIR / "text.json").read_text())

    assert 'id="finish-again"' in finish_section and 'data-text="finish.again"' in finish_section
    assert text["en"]["finish.again"] == "Check again"
    for table in text.values():
        assert table["finish.again"] in table["finish.not-done"]
        # Turn it on is for installing, not for checking.
        for key in ["finish.fail.ai", "finish.fail.whatsapp", "finish.fail.weekend",
                    "finish.fail.other"]:
            assert table["finish.again"] in table[key], key
            assert table["finish.start"] not in table[key], key


def test_the_finish_page_shows_the_checklist_the_restart_reminder_and_where_changes_go(page):
    html_text = (PAGE_DIR / "index.html").read_text()
    finish_section = html_text.split('<section id="finish"', 1)[1].split("</section>", 1)[0]

    for needed in ['id="finish-checklist"', 'data-text="finish.restart"', 'id="finish-changes"',
                   'id="finish-start"', 'id="finish-replace"', 'id="finish-stop"',
                   'id="finish-stopped"']:
        assert needed in finish_section, needed


def test_every_finish_text_is_in_all_three_languages():
    text = json.loads((PAGE_DIR / "text.json").read_text())

    for language, table in text.items():
        keys = [*(f"finish.{r}" for r in setup_server.FINISH_RESULTS),
                *(f"finish.wake.{r}" for r in wake.RESULTS),
                *(f"finish.outcome.{o}" for o in setup_status.TITLES),
                *(f"finish.missing.{o}" for o in setup_status.TITLES),
                *(f"finish.changes.{a}" for a in setup_server.AIS),
                *(f"finish.stopped.{a}" for a in setup_server.AIS),
                *(f"health.check.{c}" for c in {*setup_server.FINISH_CHECKS.values(), "other"}),
                *(f"finish.fail.{c}" for c in {*setup_server.FINISH_CHECKS.values(), "other"}),
                "finish.title", "finish.intro", "finish.start", "finish.replace", "finish.restart",
                "finish.checklist", "finish.closed", "finish.stop", "finish.warnings",
                "finish.again"]
        for key in keys:
            assert table.get(key, "").strip(), (language, key)


def test_finish_says_a_mac_that_s_shut_down_makes_no_brief():
    text = json.loads((PAGE_DIR / "text.json").read_text())

    assert "shut down" in text["en"]["finish.restart"]
    assert "sammutettu" in text["fi"]["finish.restart"]
    assert "关机" in text["zh"]["finish.restart"]
