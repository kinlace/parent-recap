"""The setup page's Working and Check steps (ADR 0006): Parent Recap reads the Sources with progress
shown, then one page with everything found, to confirm.

The server runs in-process on a free port, inside the harness, as `setup_page.py` starts it, so the
config, the progress record, the secrets, the jobs, the wake and the desktop are the harness's.
Assertions are on what the page and the family see: the responses, the config and progress written,
and what was opened."""
from __future__ import annotations

import imaplib
import json
import re
import threading
from pathlib import Path
from typing import Any

import pytest
import yaml

from family_brief import ops, setup_save, setup_server
from family_brief.config import Config
from nights import FEEDBACK
from test_setup_steps import HeaderImap
from setup_page import (APP_PASSWORD, PAGE_DIR, Response, at_the_check_step, call, check_page, config_file,
                        confirm, connect_done, health_result, more_progress, save_progress, wait_for)
from setup_page import ai, clock, health, page  # noqa: F401


# ── Working: Parent Recap reads the Sources, with progress shown


class GatedHeaderImap(HeaderImap):
    """Gmail's IMAP server for the sender scan, which holds each batch of headers after the
    first until the test lets it go, so the page can be seen while it reads."""

    def __init__(self, senders: list[str]) -> None:
        super().__init__(senders)
        self.go = threading.Event()
        self.batches = 0

    def fetch(self, ids: bytes, what: str) -> tuple[str, list[Any]]:
        self.batches += 1
        if self.batches > 1:
            assert self.go.wait(5)
        return super().fetch(ids, what)


SENDERS = (["Opettaja <opettaja@edu.espoo.fi>"] * 300 + ["Coach <coach@tapiolan-seura.fi>"] * 100
           + ["Friend <friend@gmail.com>"] * 40 + ["Shop <news@shop.example.com>"] * 10)


@pytest.fixture
def senders(harness, monkeypatch) -> GatedHeaderImap:
    imap = GatedHeaderImap(SENDERS)
    monkeypatch.setattr(imaplib, "IMAP4_SSL", imap)
    harness.secrets["gmail-imap-parent@example.com"] = APP_PASSWORD
    return imap


def start_reading(url: str) -> Response:
    return call(url, "api/working", method="POST", body={})


def reading(url: str) -> dict[str, Any]:
    return call(url, "api/working/check", method="POST", body={}).json()


def read_to_the_end(url: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    # Cleared before each poll: a reply that doesn't carry `read`/`total` must not inherit them
    # from the earlier "reading" one, or read-failed still names a count (a CI flake).
    wait_for(lambda: out.clear() or out.update(reading(url)) or out["result"] != "reading")
    return out


def test_once_every_source_is_done_the_page_reads_them_and_shows_how_far_it_is(harness, page,
                                                                             senders):
    connect_done(harness)

    r = start_reading(page.url)

    assert r.status == 200
    assert r.json()["result"] == "reading" and r.json()["progress"]["phase"] == "working"
    wait_for(lambda: reading(page.url).get("read") == 200)
    assert reading(page.url) == {"result": "reading", "read": 200, "total": 450}
    assert start_reading(page.url).json()["result"] == "reading"  # not started twice
    senders.go.set()
    out = read_to_the_end(page.url)
    assert out["result"] == "read"
    assert out["progress"]["phase"] == "check"
    # Kept for the check page, as `discover gmail-senders --json` gives them.
    assert [(s["domain"], s["count"], s["likely"]) for s in out["progress"]["gmail_senders"]] == [
        ("edu.espoo.fi", 300, True), ("tapiolan-seura.fi", 100, True), ("gmail.com", 40, False),
        ("shop.example.com", 10, False)]
    assert senders.batches == 3
    assert setup_save.read(config_file(harness))["progress"]["phase"] == "check"


def test_the_mail_of_a_connected_source_isnt_ticked_as_it_would_be_read_twice(harness, page,
                                                                               monkeypatch):
    # Wilma is connected and MyClub skipped: Wilma's own mail is the news Wilma already brings.
    monkeypatch.setattr(imaplib, "IMAP4_SSL", HeaderImap(
        ["Wilma <noreply@kilo.inschool.fi>"] * 3 + ["MyClub <noreply@myclub.fi>"] * 2))
    harness.secrets["gmail-imap-parent@example.com"] = APP_PASSWORD
    connect_done(harness)

    start_reading(page.url)
    assert read_to_the_end(page.url)["result"] == "read"

    ticked = {s["domain"]: s["ticked"] for s in check_page(page.url)["senders"]}
    assert ticked["kilo.inschool.fi"] is False
    assert ticked["myclub.fi"] is True


def test_reading_waits_until_every_source_is_done_or_skipped(harness, page, senders):
    connect_done(harness)
    save_progress(harness, {"phase": "connect", "source": "ai", "sources": {"ai": "to-do"}})

    r = start_reading(page.url)

    assert r.status == 400 and r.json()["result"] == "invalid-answers"
    assert setup_save.read(config_file(harness))["progress"]["phase"] == "connect"
    assert senders.batches == 0
    for body in [{"x": 1}, []]:
        assert call(page.url, "api/working", method="POST", body=body).status == 400


def test_reading_picks_up_again_after_the_page_was_restarted(harness, page, senders):
    connect_done(harness)
    more_progress(harness, phase="working")
    senders.go.set()

    assert reading(page.url)["result"] == "no-read"  # nothing reading in this server yet
    assert start_reading(page.url).json()["result"] == "reading"
    assert read_to_the_end(page.url)["result"] == "read"


def test_a_gmail_that_cannot_be_read_says_so_and_can_be_tried_again(harness, page, senders):
    connect_done(harness)
    senders.go.set()
    del harness.secrets["gmail-imap-parent@example.com"]

    start_reading(page.url)
    out = read_to_the_end(page.url)

    assert out == {"result": "read-failed"}  # without the error, which names the address
    assert setup_save.read(config_file(harness))["progress"]["phase"] == "working"
    harness.secrets["gmail-imap-parent@example.com"] = APP_PASSWORD
    start_reading(page.url)
    assert read_to_the_end(page.url)["result"] == "read"


def test_every_working_result_is_explained_in_all_three_languages():
    text = json.loads((PAGE_DIR / "text.json").read_text())

    for language, table in text.items():
        for result in setup_server.check.WORKING_RESULTS:
            assert table.get(f"working.{result}", "").strip(), (language, result)


# ── Check: one page with everything found, to confirm


def test_the_kids_come_all_ticked_with_their_first_name_as_everyday_name(harness, page):
    harness.config["kids"][0]["name"] = "Mia Virtanen"
    harness.config["kids"][1].update(name="Leo Matti Virtanen", everyday_name="Leksa")
    at_the_check_step(harness)

    out = check_page(page.url)

    assert out["kids"] == [{"name": "Mia Virtanen", "everyday_name": "Mia"},
                           {"name": "Leo Matti Virtanen", "everyday_name": "Leksa"}]
    # Nothing about schools or classes is asked.
    assert not {"school", "class_name", "grade"} & {k for kid in out["kids"] for k in kid}


def test_the_whatsapp_groups_linked_to_a_kid_come_ticked_the_others_unticked(harness, page):
    harness.config["kids"][0]["name"] = "Mia Virtanen"
    harness.config["whatsapp"]["chats"] = [{"name": "Leo piano", "kid": "Leo", "label": "piano"}]
    at_the_check_step(harness)

    groups = check_page(page.url)["whatsapp"]

    assert [(g["name"], g["ticked"], g["kids"]) for g in groups] == [
        ("3B parents", True, ["Mia"]),
        ("Kilo School families 🏫", True, ["Mia", "Leo"]),
        ("Neighbours ", False, []),
        ("Leo piano", True, ["Leo"]),  # picked before, though not found this time
    ]


def test_without_whatsapp_there_are_no_groups_to_tick(harness, page):
    at_the_check_step(harness, whatsapp_chats=[])
    more_progress(harness, sources={"whatsapp": "skipped"}, whatsapp_chats=None)

    assert check_page(page.url)["whatsapp"] is None


def test_with_another_source_the_check_page_doesnt_say_the_briefs_come_out_empty(harness, page):
    # With Wilma's or a WhatsApp group's news, a Gmail with no sender only sends the Brief (#361).
    at_the_check_step(harness)
    assert check_page(page.url)["other_sources"] is True

    harness.config["wilma"]["enabled"] = False
    at_the_check_step(harness)
    assert check_page(page.url)["other_sources"] is True  # the WhatsApp groups ticked

    more_progress(harness, sources={"whatsapp": "skipped"}, whatsapp_chats=None)
    assert check_page(page.url)["other_sources"] is False

    text = json.loads((PAGE_DIR / "text.json").read_text())
    for table in text.values():
        assert table["check.senders.none.other"].strip()
    assert "empty" in text["en"]["check.senders.none"]
    assert "empty" not in text["en"]["check.senders.none.other"]


def test_school_city_and_club_senders_come_ticked_and_public_mail_is_left_out(harness, page):
    at_the_check_step(harness)

    senders = check_page(page.url)["senders"]

    assert [(s["domain"], s["ticked"]) for s in senders] == [
        ("edu.espoo.fi", True), ("shop.example.com", False),
        ("espoo.fi", True),  # the city's starting allowlist, though no mail came from it yet
        ("kilo.example.fi", True)]  # already on the allowlist
    assert senders[0] == {"domain": "edu.espoo.fi", "count": 300, "example": "Opettaja",
                          "ticked": True}


def test_each_recipient_comes_with_their_language_and_the_evening_at_21(harness, page):
    harness.config["email"]["to"] = ["parent@example.com",
                                     {"address": "partner@example.com", "language": "fi"}]
    at_the_check_step(harness)

    out = check_page(page.url)

    assert out["recipients"] == [{"address": "parent@example.com", "language": "zh"},
                                 {"address": "partner@example.com", "language": "fi"}]
    assert out["evening"] == "21:00"


def test_confirming_saves_everything_through_setup_save_and_runs_the_health_check(harness, page,
                                                                                health):
    harness.config["kids"][0]["name"] = "Mia Virtanen"
    harness.config["kids"][0]["aliases"] = ["米娅"]
    at_the_check_step(harness)
    health.go.clear()

    r = confirm(page.url, kids=[{"name": "Mia Virtanen", "everyday_name": "Mimi"},
                                {"name": "Aino", "everyday_name": "Aino"}],
                whatsapp=["3B parents", "Kilo School families 🏫"],
                recipients=[{"address": "parent@example.com", "language": "fi"},
                            {"address": "partner@example.com", "language": "zh"}],
                evening="20:30")

    assert r.status == 200 and r.json()["result"] == "checking"
    cfg = Config.load(config_file(harness))
    assert [(k.name, k.everyday_name, k.aliases) for k in cfg.kids] == [
        ("Mia Virtanen", "Mimi", ["米娅"]),  # what the page doesn't show stays as it was
        ("Aino", "Aino", [])]  # Leo unticked, Aino added by her everyday name
    assert [(c.name, c.kid, c.label) for c in cfg.whatsapp.chats] == [
        ("3B parents", "Mia Virtanen", "class"),
        ("Kilo School families 🏫", "Mia Virtanen", "school")]  # Leo is no longer a Kid
    assert cfg.whatsapp.enabled
    assert cfg.gmail.allowlist_domains == ["edu.espoo.fi", "espoo.fi"]
    # The parent's language now differs from the page's, so it's their own; the partner reads
    # the Household's.
    assert [(t.address, t.language) for t in cfg.email.to] == \
        [("parent@example.com", "fi"), ("partner@example.com", None)]
    assert cfg.summary_language == "zh"
    assert (cfg.schedule.daily_hour, cfg.schedule.daily_minute) == (20, 30)

    assert call(page.url, "api/check/health", method="POST", body={}).json() == \
        {"result": "checking"}
    assert confirm(page.url).status == 409  # not again while it runs
    health.go.set()
    out = health_result(page.url)
    assert out["result"] == "ok"
    assert out["checks"] == [{"check": "config", "status": "ok"}, {"check": "gmail", "status": "ok"},
                             {"check": "ai", "status": "ok"}]
    assert out["progress"]["phase"] == "first-brief"
    # The schedule is Finish's: it isn't checked before it's installed.
    assert health.runs == [(str(config_file(harness)), False)]


def test_a_health_check_that_fails_keeps_the_page_and_says_which_check(harness, page, health):
    at_the_check_step(harness)
    health.results = [
        (ops.OK, "config", "Config file", "~/.family/config.yaml, 2 kids"),
        (ops.FAIL, "gmail", "Gmail", "IMAP login failed: parent@example.com rejected"),
        (ops.WARN, "feedback", "Pilot feedback", "feedback is on but prefill_base_url or fields is "
         "missing"),
        ops.Check(ops.FAIL, "myclub", "MyClub (Mia)",
                  "subscription link doesn't open: https://x.myclub.fi/secret", subject="Mia")]

    assert confirm(page.url).json()["result"] == "checking"
    out = health_result(page.url)

    assert out == {"result": "not-ok", "checks": [
        {"check": "config", "status": "ok"}, {"check": "gmail", "status": "fail"},
        {"check": "feedback", "status": "warn"},
        {"check": "myclub", "status": "fail", "kid": "Mia"}]}
    assert "parent@example.com" not in json.dumps(out) and "secret" not in json.dumps(out)
    assert setup_save.read(config_file(harness))["progress"]["phase"] == "check"
    # Fixed, it's run again by confirming again.
    health.results = health.results[:1]
    confirm(page.url)
    assert health_result(page.url)["result"] == "ok"


def test_warnings_alone_let_setup_move_on(harness, page, health):
    at_the_check_step(harness)
    health.results.append((ops.WARN, "feedback", "Pilot feedback", "feedback.household_label is empty"))

    confirm(page.url)

    assert health_result(page.url)["result"] == "ok"


def test_a_pilot_household_sees_its_feedback_label_and_can_change_it(harness, page, health):
    harness.config["feedback"] = {**FEEDBACK, "household_label": "parent"}
    at_the_check_step(harness)

    assert check_page(page.url)["feedback"] == {"household_label": "parent"}
    assert confirm(page.url, household_label=" ").status == 400

    assert confirm(page.url, household_label=" Virtanen family ").json()["result"] == "checking"
    assert Config.load(config_file(harness)).feedback.household_label == "Virtanen family"


def test_a_household_not_in_the_pilot_has_no_feedback_label_to_give(harness, page, health):
    at_the_check_step(harness)

    assert check_page(page.url)["feedback"] is None
    assert confirm(page.url, household_label="Virtanen family").status == 400
    assert confirm(page.url).json()["result"] == "checking"


@pytest.mark.macos
def test_the_health_check_runs_for_real_with_the_sources_confirmed(harness, page, ai, monkeypatch):
    ai("claude")
    harness.secrets["claude-oauth-token"] = "sk-ant-oat01-token"
    harness.secrets["gmail-imap-parent@example.com"] = APP_PASSWORD
    harness.config["wilma"]["enabled"] = False
    monkeypatch.setattr(imaplib, "IMAP4_SSL", HeaderImap(SENDERS))
    at_the_check_step(harness)

    confirm(page.url, whatsapp=[])

    out = health_result(page.url)
    assert out["result"] == "ok", out
    assert {c["check"] for c in out["checks"]} >= {"config", "gmail", "ai", "calendar"}
    assert not Config.load(config_file(harness)).whatsapp.enabled  # no group ticked


@pytest.mark.parametrize("answers", [
    {"senders": ["gmail.com"]},  # public mail is never offered
    {"senders": ["anything.example"]},
    {"whatsapp": ["Another group"]},
    {"kids": []},
    {"kids": [{"name": "Mia", "everyday_name": " "}]},
    {"kids": [{"name": "Mia", "everyday_name": "A"}, {"name": "mia", "everyday_name": "B"}]},
    {"recipients": [{"address": "stranger@example.com", "language": "fi"}]},
    {"recipients": [{"address": "parent@example.com", "language": "klingon!"},
                    {"address": "partner@example.com", "language": "zh"}]},
    {"evening": "25:00"},
    {"x": 1},
])
def test_answers_the_check_page_did_not_offer_are_refused(harness, page, health, answers):
    at_the_check_step(harness)
    before = config_file(harness).read_text()

    r = confirm(page.url, **answers)

    assert r.status == 400 and r.json()["result"] == "invalid-answers", answers
    assert config_file(harness).read_text() == before
    assert health.runs == []


def test_without_whatsapp_confirming_leaves_it_as_it_was(harness, page, health):
    at_the_check_step(harness, whatsapp_chats=[])
    more_progress(harness, sources={"whatsapp": "skipped"}, whatsapp_chats=None)
    harness_chats = yaml.safe_load(config_file(harness).read_text())["whatsapp"]

    assert confirm(page.url, whatsapp=["3B parents"]).status == 400
    assert confirm(page.url, whatsapp=None).json()["result"] == "checking"
    assert yaml.safe_load(config_file(harness).read_text())["whatsapp"] == harness_chats


def test_the_check_page_has_all_options_and_lets_kids_be_added(page):
    html = (PAGE_DIR / "index.html").read_text()

    for element in ('id="check-whatsapp-all"', 'id="check-senders-all"', 'id="check-kid-form"',
                    'id="check-evening"', 'type="time"'):
        assert element in html, element


def test_every_check_and_health_result_is_explained_in_all_three_languages():
    text = json.loads((PAGE_DIR / "text.json").read_text())

    for language, table in text.items():
        for result in setup_server.health.HEALTH_RESULTS:
            assert table.get(f"health.{result}", "").strip(), (language, result)
        for check in {*setup_server.health.HEALTH_CHECKS.values(), "other"}:
            assert table.get(f"health.check.{check}", "").strip(), (language, check)
            assert table.get(f"health.fail.{check}", "").strip(), (language, check)


def test_every_health_check_doctor_runs_has_a_name_on_the_page():
    keys = {key for key, _ in ops.CHECK_ITEMS}
    # Every add() names one of them: a key that isn't would fail only when its check runs.
    added = set(re.findall(r'\badd\([^,]+, "([^"]+)"', Path(ops.__file__).read_text()))

    assert added and added <= keys, added - keys
    assert keys - setup_server.health.SCHEDULE_CHECKS == set(setup_server.health.HEALTH_CHECKS)
    # Python path is always OK, so Finish never names it.
    assert setup_server.health.SCHEDULE_CHECKS - setup_server.health.FINISH_LEAVES_OUT - {"python-path"} <= \
        set(setup_server.health.FINISH_CHECKS)
