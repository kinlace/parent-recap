"""The setup page's Run Report switch, under Finish's "Later, if you need it" (ADR 0048, #927): on
by default for a Household that sends its Brief through the gateway, with one sentence on what the
report is and a link to what it holds in SECURITY.md. A Household that turns sending from its own
Gmail on (ADR 0034) is asked instead: the switch is off until the family turns it on. Once the
family set it, it stays as set whichever way the Brief is sent. A Hosted Household's page has no
switch. The manage skill turns it off and on with `setup save`.

Over HTTP, as the page's own script calls it, on the server `setup_page.py` starts inside the
harness. Assertions are on the responses, the config written and the reports the evening run
sends to the gateway (fake_outbox.py)."""
from __future__ import annotations

import json
from typing import Any

import pytest
import yaml

from family_brief import setup_save
from nights import normal_night
from setup_page import APP_PASSWORD, PAGE_DIR, at_finish, call, config_file, connect_done, inside
from setup_page import clock, health, page, schedule  # noqa: F401
from test_gmail_sends import ASKED, PARENT, REPORTING, sending

TEXT = json.loads((PAGE_DIR / "text.json").read_text(encoding="utf-8"))
SECURITY_LINK = "https://github.com/kinlace/parent-recap/blob/stable/SECURITY.md#the-run-report"
ROOT = PAGE_DIR.parents[3]


def shown(url: str) -> dict[str, Any]:
    return call(url, "api/state").json()["run_report"]


def switch(url: str, on: Any):
    return call(url, "api/run-report", method="POST", body={"on": on})


def saved(harness) -> dict[str, Any]:
    return yaml.safe_load(config_file(harness).read_text())


def evening_run(harness) -> list:
    """The next evening run, with the config as the page left it: the reports the gateway took."""
    harness.config = saved(harness)
    normal_night(harness, "en")
    before = len(harness.reports)
    assert harness.run() == 0
    return harness.reports[before:]


# ── A Household on the gateway


def test_the_switch_is_on_for_a_household_on_the_gateway_without_anything_saved(harness, page):
    at_finish(harness)

    assert shown(page.url) == REPORTING
    assert "run_report" not in saved(harness)


def test_turned_off_the_next_run_sends_none_and_turned_on_again_it_sends_one(harness, page):
    at_finish(harness)

    r = switch(page.url, False)

    assert r.status == 200
    assert r.json() == {"result": "saved", "run_report": {"available": True, "on": False,
                                                          "ask": False}}
    assert saved(harness)["run_report"] == {"enabled": False}
    assert shown(page.url)["on"] is False  # as a page opened later shows it
    assert evening_run(harness) == []

    r = switch(page.url, True)

    assert r.json()["result"] == "saved" and r.json()["run_report"]["on"] is True
    assert saved(harness)["run_report"] == {"enabled": True}
    assert len(evening_run(harness)) == 1


def test_the_switch_takes_only_on_or_off(harness, page):
    at_finish(harness)
    for body in [{}, {"on": "no"}, {"on": False, "more": 1}, []]:
        r = call(page.url, "api/run-report", method="POST", body=body)
        assert r.status == 400 and r.json()["result"] == "invalid-answers", body
    assert "run_report" not in saved(harness)


# ── A Household that sends the Brief from its own Gmail (ADR 0034)


def test_turning_own_gmail_sending_on_shows_the_switch_off_and_asks(harness, page):
    at_finish(harness)
    harness.secrets[f"gmail-imap-{PARENT}"] = APP_PASSWORD

    out = sending(page.url, "gmail")

    assert out["result"] == "saved"
    assert out["run_report"] == ASKED
    assert shown(page.url) == ASKED
    assert "run_report" not in saved(harness)  # nothing said yet, so nothing is sent
    assert evening_run(harness) == []


def test_an_own_gmail_household_that_says_yes_sends_its_report(harness, page):
    at_finish(harness)
    harness.secrets[f"gmail-imap-{PARENT}"] = APP_PASSWORD
    sending(page.url, "gmail")

    r = switch(page.url, True)

    assert r.json()["run_report"] == REPORTING
    assert saved(harness)["run_report"] == {"enabled": True}
    assert len(evening_run(harness)) == 1


@pytest.mark.parametrize("set_to", [True, False])
def test_a_switch_the_family_set_holds_whichever_way_the_brief_is_sent(harness, page, set_to):
    at_finish(harness)
    harness.secrets[f"gmail-imap-{PARENT}"] = APP_PASSWORD
    switch(page.url, set_to)

    assert sending(page.url, "gmail")["run_report"] == {"available": True, "on": set_to,
                                                        "ask": False}
    assert sending(page.url, "gateway")["run_report"]["on"] is set_to
    assert saved(harness)["run_report"] == {"enabled": set_to}


# ── A Hosted Household


def test_a_hosted_household_s_page_has_no_switch_and_saves_nothing(harness, page):
    connect_done(harness, hosted=True)

    assert shown(page.url) == {"available": False}
    r = switch(page.url, True)
    assert r.status == 200 and r.json() == {"result": "not-available",
                                            "run_report": {"available": False}}
    assert "run_report" not in saved(harness)


# ── The page's words and the chat's way


def test_finish_has_the_switch_its_sentence_its_question_and_the_link():
    html = (PAGE_DIR / "index.html").read_text()
    block = inside(html, "finish-report")

    for needed in ['type="checkbox" id="finish-report-on"', 'data-text="finish.report.about"',
                   '<p id="finish-report-ask" data-text="finish.report.ask" hidden>',
                   'id="finish-report-more" target="_blank" rel="noopener noreferrer"',
                   'data-text="finish.report.more"']:
        assert needed in block, needed
    assert 'id="finish-report" hidden' in html
    # The script gives the link its address: the page's own files name no internet address.
    assert f'"{SECURITY_LINK}"' in (PAGE_DIR / "page.js").read_text()
    assert "## The Run Report" in (ROOT / "SECURITY.md").read_text()  # where the link goes


def test_every_run_report_text_is_in_all_three_languages():
    keys = ["title", "about", "on", "ask", "more", "saved.on", "saved.off", "save-failed",
            "closed"]
    for language, table in TEXT.items():
        for key in keys:
            assert table.get(f"finish.report.{key}", "").strip(), (language, key)


def test_the_sentence_says_what_the_report_is_and_what_it_never_holds():
    # The run, not the evening's: the Brief's time can be any time of day (#733).
    says = {"en": ("how the run went", "nothing from your messages"),
            "fi": ("miten ajo meni", "ei ole mitään viesteistäsi"),
            "zh": ("这次运行的情况", "不含你的任何消息")}
    for language, table in TEXT.items():
        for words in says[language]:
            assert words in table["finish.report.about"], (language, words)


def test_the_own_gmail_privacy_text_names_the_run_report_in_every_language():
    unless = {"en": "unless you turn the Run Report on", "fi": "ellet ota ajoraporttia käyttöön",
              "zh": "除非你打开运行报告"}
    for language, table in TEXT.items():
        assert unless[language] in table["finish.sending.about"], language


def test_setup_save_turns_the_run_report_off_and_on_as_the_manage_skill_does(harness):
    connect_done(harness)

    assert setup_save.save(config_file(harness), {"run_report": False})["result"] == "saved"
    assert saved(harness)["run_report"] == {"enabled": False}
    assert setup_save.save(config_file(harness), {"run_report": True})["result"] == "saved"
    assert saved(harness)["run_report"] == {"enabled": True}


def test_manage_has_a_row_for_the_run_report_in_three_languages():
    manage = (ROOT / "skills" / "manage" / "SKILL.md").read_text()
    [row] = [line for line in manage.splitlines() if line.startswith("| Turn the Run Report")]
    for phrase in ['"stop sending the Run Report"', "「关闭运行报告」", '"lopeta ajoraportti"',
                   """'{"run_report": false}'""", """'{"run_report": true}'"""]:
        assert phrase in row, phrase
