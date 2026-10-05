"""Saving the Household's setup answers and progress with `family-brief setup save`, which both the
setup page and the chat setup use (ADR 0006).

The answers go in as JSON on stdin; the command prints one line of JSON. Assertions are on that
line, on the config file and on the progress record, which is what the page, the setup skill and
the nightly run see."""
from __future__ import annotations

import io
import json
import stat
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from conftest import PILOT_FORM_FIELDS, PILOT_FORM_URL
from family_brief import __main__ as cli, feedback, ops
from family_brief.config import Config

LINK = "https://example.myclub.fi/ical/mia.ics"  # Mia's, in the harness's config

ANSWERS = {
    "language": "zh",
    "kids": [{"name": "Mia Virtanen", "everyday_name": "Mia", "aliases": ["米娅"],
              "school": "Kilo School", "class_name": "3B", "grade": 3},
             {"name": "Leo Virtanen", "everyday_name": "Leo"}],
    "recipients": [{"address": "parent@gmail.com"},
                   {"address": "partner@gmail.com", "language": "fi"}],
    "ai": "codex",
    "evening": "20:30",
    "sources": {
        "gmail": {"address": "parent@gmail.com", "allowlist_domains": ["espoo.fi"],
                  "allowlist_senders": ["coach@gmail.com"]},
        "wilma": {"enabled": True},
        "whatsapp": {"enabled": True, "chats": [{"name": "3B vanhemmat 🎒 ", "kid": "Mia Virtanen",
                                                  "label": "class"}]},
    },
    "feedback": {"enabled": True, "household_label": "Virtanen family"},
}


def config_file(harness) -> Path:
    return harness.home / ".family" / "config.yaml"


def progress_file(harness) -> Path:
    return harness.home / ".family" / "setup-progress.json"


def save(harness, answers: Any = None, *args: str, raw: str | None = None) -> int:
    """Runs `setup save` on the config as it is on disk (harness.cli would write its own first)."""
    text = raw if raw is not None else json.dumps(answers, ensure_ascii=False)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sys, "argv", ["family-brief", "-c", str(config_file(harness)), "setup", "save",
                                 *args])
        mp.setattr(sys, "stdin", io.StringIO(text))
        return cli.main()


def existing_config(harness) -> dict[str, Any]:
    """The harness's config, written as an earlier step left it."""
    config_file(harness).parent.mkdir(parents=True, exist_ok=True)
    config_file(harness).write_text(yaml.safe_dump(harness.config, allow_unicode=True,
                                                   sort_keys=False))
    config_file(harness).chmod(0o600)
    return yaml.safe_load(config_file(harness).read_text())


def saved(harness) -> dict[str, Any]:
    return yaml.safe_load(config_file(harness).read_text())


def result(capsys) -> tuple[dict[str, Any], str]:
    out, err = capsys.readouterr()
    lines = out.strip().splitlines()
    assert len(lines) == 1, f"expected one JSON line, got: {out!r}"
    return json.loads(lines[0]), out + err


# ── answers


def test_answers_on_a_new_mac_write_a_valid_owner_only_config(harness, capsys):
    harness.ship_pilot_form()

    assert save(harness, ANSWERS) == 0

    res, _ = result(capsys)
    assert res["result"] == "saved"
    cfg = Config.load(config_file(harness))
    assert cfg.summary_language == "zh"
    assert [(k.name, k.called(), k.class_name) for k in cfg.kids] == \
        [("Mia Virtanen", "Mia", "3B"), ("Leo Virtanen", "Leo", None)]
    assert cfg.kids[0].aliases == ["米娅"] and cfg.kids[0].grade == 3
    assert [(r.address, r.language) for r in cfg.email.to] == \
        [("parent@gmail.com", None), ("partner@gmail.com", "fi")]
    assert cfg.llm.backend == "codex"
    assert (cfg.schedule.daily_hour, cfg.schedule.daily_minute) == (20, 30)
    assert cfg.gmail.username == "parent@gmail.com"
    assert cfg.gmail.allowlist_domains == ["espoo.fi"]
    assert cfg.gmail.allowlist_senders == ["coach@gmail.com"]
    assert cfg.wilma.enabled
    assert cfg.whatsapp.enabled and cfg.whatsapp.chat_names() == ["3B vanhemmat 🎒 "]
    assert cfg.whatsapp.kid_for("3B vanhemmat 🎒 ") == "Mia Virtanen"
    assert cfg.feedback.active() and cfg.feedback.household_label == "Virtanen family"
    assert stat.S_IMODE(config_file(harness).stat().st_mode) == 0o600


def test_the_welcome_choices_alone_make_a_config_without_kids_yet(harness, capsys):
    answers = {"language": "fi", "ai": "claude", "recipients": [{"address": "parent@gmail.com"}]}

    assert save(harness, answers) == 0

    cfg = Config.load(config_file(harness))
    assert cfg.kids == [] and cfg.summary_language == "fi"
    assert [r.address for r in cfg.email.to] == ["parent@gmail.com"]


def test_answers_not_given_leave_the_config_as_it_was(harness, capsys):
    before = existing_config(harness)

    assert save(harness, {"evening": "21:15"}) == 0

    after = saved(harness)
    assert after.pop("schedule") == {"daily_hour": 21, "daily_minute": 15}
    assert after == before


def test_saving_the_kids_keeps_what_the_answers_do_not_mention_about_each(harness, capsys):
    existing_config(harness)

    assert save(harness, {"kids": [{"name": "Mia", "everyday_name": "Mimi"}]}) == 0

    kids = saved(harness)["kids"]
    assert [k["name"] for k in kids] == ["Mia"]  # Leo was unticked
    assert kids[0]["everyday_name"] == "Mimi"
    assert kids[0]["myclub_ical_url"] == LINK
    assert kids[0]["aliases"] == ["米娅"] and kids[0]["class_name"] == "3B"


def test_a_source_answer_leaves_that_sources_other_settings(harness, capsys):
    harness.config["gmail"]["lookback_hours"] = 48
    existing_config(harness)

    assert save(harness, {"sources": {"gmail": {"allowlist_domains": ["espoo.fi", "kilo.fi"]}}}) == 0

    gmail = saved(harness)["gmail"]
    assert gmail == {"username": "parent@example.com", "allowlist_domains": ["espoo.fi", "kilo.fi"],
                     "lookback_hours": 48}
    assert saved(harness)["whatsapp"] == harness.config["whatsapp"]


def test_an_answer_given_as_null_leaves_the_config_as_it_was(harness, capsys):
    before = existing_config(harness)

    assert save(harness, {"sources": {"gmail": {"address": None, "allowlist_domains": None}},
                          "kids": [{"name": "Mia", "aliases": None}, {"name": "Leo"}]}) == 0

    assert saved(harness) == before


def test_answers_in_the_wrong_shape_are_refused_with_the_reason(harness, capsys):
    before = existing_config(harness)

    for answers, field in [({"language": "Chinese"}, "language"),
                           ({"evening": "9pm"}, "evening"),
                           ({"evening": "25:00"}, "evening"),
                           ({"evening": "009:00"}, "evening"),
                           ({"ai": "gemini"}, "ai"),
                           ({"kids": [{"everyday_name": "Mia"}]}, "kids.0.name"),
                           ({"recipients": [{"address": "x", "language": "zh-CN"}]},
                            "recipients.0.language"),
                           ({"sources": {"whatsapp": {"chats": [{"kid": "Mia"}]}}},
                            "sources.whatsapp.chats.0.name"),
                           ({"feedback": {"prefill_base_url": "https://example.com/form"}},
                            "feedback.prefill_base_url"),  # the program's own, never pasted
                           ({"colour": "blue"}, "colour")]:
        assert save(harness, answers) == 1, answers
        res, _ = result(capsys)
        assert res["result"] == "invalid-answers", answers
        assert any(e.startswith(field) for e in res["errors"]), (answers, res["errors"])
        assert res["next"]
        assert saved(harness) == before


def test_opting_in_to_pilot_feedback_writes_the_shipped_form_with_the_household_s_label(
        harness, capsys, monkeypatch):
    harness.ship_pilot_form()
    harness.config.update(wilma={"enabled": False}, whatsapp={"enabled": False})
    del harness.config["kids"][0]["myclub_ical_url"]  # no network in tests
    existing_config(harness)
    monkeypatch.setattr(ops, "launchctl_loaded", lambda: set())

    assert save(harness, {"feedback": {"enabled": True}}) == 0

    feedback = saved(harness)["feedback"]
    assert feedback == {"enabled": True, "prefill_base_url": PILOT_FORM_URL,
                        "fields": PILOT_FORM_FIELDS,
                        "household_label": "parent"}  # the setup parent's email user, until changed
    capsys.readouterr()
    harness.config = saved(harness)
    harness.cli("doctor", "--skip-llm")
    out = capsys.readouterr().out
    assert "✅ Pilot feedback: the Brief has ⭐/❌ links, Household label parent" in out
    assert "⚠️  Pilot feedback" not in out

    assert save(harness, {"feedback": {"household_label": "Virtanen family"}}) == 0
    assert saved(harness)["feedback"]["household_label"] == "Virtanen family"


def test_the_household_label_follows_once_the_setup_parent_s_gmail_is_known(harness, capsys):
    harness.ship_pilot_form()

    assert save(harness, {"ai": "claude", "feedback": {"enabled": True}}) == 0
    assert "household_label" not in saved(harness)["feedback"]
    assert save(harness, {"sources": {"gmail": {"address": "virtanen.home@gmail.com"}}}) == 0

    assert saved(harness)["feedback"]["household_label"] == "virtanen.home"


def test_without_a_shipped_pilot_form_pilot_feedback_cant_be_turned_on(harness, capsys):
    before = existing_config(harness)

    assert save(harness, {"feedback": {"enabled": True}}) == 1

    res, _ = result(capsys)
    assert res["result"] == "invalid-answers"
    assert any(e.startswith("feedback.enabled") for e in res["errors"]), res["errors"]
    assert saved(harness) == before
    # Turning it off always works.
    assert save(harness, {"feedback": {"enabled": False}}) == 0
    assert saved(harness)["feedback"]["enabled"] is False


def test_the_pilot_form_this_version_ships_makes_links_and_goes_into_the_package():
    if feedback.PILOT_FORM.exists():  # until the team ships one, setup doesn't offer it
        assert feedback.pilot_form() is not None, "pilot_feedback.yaml is missing a field"
    pyproject = (Path(cli.__file__).parents[2] / "pyproject.toml").read_text()
    assert f'"{feedback.PILOT_FORM.name}"' in pyproject


def test_text_that_is_not_json_is_refused(harness, capsys):
    before = existing_config(harness)

    assert save(harness, raw="language: zh") == 1

    res, _ = result(capsys)
    assert res["result"] == "invalid-answers" and res["next"]
    assert saved(harness) == before


def test_a_myclub_link_is_refused_and_never_repeated(harness, capsys):
    secret = "webcal://example.myclub.fi/ical/s3cr3t"
    before = existing_config(harness)

    assert save(harness, {"kids": [{"name": "Mia", "myclub_ical_url": secret}]}) == 1

    res, printed = result(capsys)
    assert res["result"] == "invalid-answers"
    assert "setup myclub" in res["next"]
    assert secret not in printed
    assert saved(harness) == before


def test_a_config_it_cannot_read_is_left_alone(harness, capsys):
    config_file(harness).parent.mkdir(parents=True)
    config_file(harness).write_text(f"kids: [{{name: Mia, myclub_ical_url: {LINK}, grade: x}}]\n")
    before = config_file(harness).read_text()

    assert save(harness, {"evening": "21:15"}) == 1

    res, printed = result(capsys)
    assert res["result"] == "bad-config" and "doctor" in res["next"]
    assert LINK not in printed
    assert config_file(harness).read_text() == before


# ── progress


def test_progress_is_saved_and_read_back(harness, capsys):
    progress = {"phase": "connect", "source": "gmail",
                "sources": {"wilma": "done", "gmail": "to-do"}}

    assert save(harness, {"progress": progress}) == 0
    capsys.readouterr()
    assert save(harness, None, "--read") == 0

    res, _ = result(capsys)
    assert res["result"] == "read"
    assert res["progress"] == {"phase": "connect", "source": "gmail",
                               "sources": {"wilma": "done", "gmail": "to-do", "ai": "to-do",
                                           "whatsapp": "to-do", "myclub": "to-do"}}
    assert stat.S_IMODE(progress_file(harness).stat().st_mode) == 0o600
    assert not config_file(harness).exists()  # progress alone doesn't make a config


def test_before_any_progress_it_reads_as_the_start(harness, capsys):
    assert save(harness, None, "--read") == 0

    res, _ = result(capsys)
    assert res["progress"] == {"phase": "welcome", "source": None,
                               "sources": {s: "to-do" for s in
                                           ("wilma", "gmail", "ai", "whatsapp", "myclub")}}


def test_progress_not_given_stays_as_it_was(harness, capsys):
    assert save(harness, {"progress": {"phase": "connect", "source": "wilma",
                                       "sources": {"wilma": "done"}}}) == 0
    capsys.readouterr()
    assert save(harness, {"progress": {"source": "gmail", "sources": {"whatsapp": "skipped"}}}) == 0

    res, _ = result(capsys)
    assert res["progress"]["phase"] == "connect"
    assert res["progress"]["source"] == "gmail"
    assert res["progress"]["sources"]["wilma"] == "done"
    assert res["progress"]["sources"]["whatsapp"] == "skipped"


def test_the_partner_waits_in_the_progress_until_it_is_cleared(harness, capsys):
    partner = {"address": "partner@example.com", "language": "fi"}
    assert save(harness, {"progress": {"partner": partner}}) == 0
    capsys.readouterr()
    assert save(harness, {"progress": {"phase": "connect"}}) == 0
    assert result(capsys)[0]["progress"]["partner"] == partner

    assert save(harness, {"progress": {"partner": None}}) == 0  # only me

    assert result(capsys)[0]["progress"]["partner"] is None
    assert save(harness, None, "--read") == 0
    assert result(capsys)[0]["progress"]["partner"] is None


def test_the_whatsapp_groups_found_are_kept_in_the_progress_for_the_check_page(harness, capsys):
    found = [{"name": "3B parents", "last": "2026-09-25", "archived": False,
              "hint": {"kids": ["Mia"], "matched": ["3B"]}},
             {"name": "Neighbours ", "last": "2026-09-23", "archived": True}]

    assert save(harness, {"progress": {"whatsapp_chats": found}}) == 0
    capsys.readouterr()
    assert save(harness, {"progress": {"phase": "check"}}) == 0
    assert result(capsys)[0]["progress"]["whatsapp_chats"] == found

    assert save(harness, {"progress": {"whatsapp_chats": [{"name": "3B", "secret": 1}]}}) == 1
    assert any(e.startswith("progress.whatsapp_chats.0") for e in result(capsys)[0]["errors"])
    assert save(harness, None, "--read") == 0
    assert result(capsys)[0]["progress"]["whatsapp_chats"] == found


def test_the_gmail_senders_found_are_kept_in_the_progress_for_the_check_page(harness, capsys):
    found = [{"domain": "edu.espoo.fi", "count": 5, "example": "Opettaja", "likely": True},
             {"domain": "gmail.com", "count": 6, "example": "Friend", "likely": False,
              "public": True}]

    assert save(harness, {"progress": {"gmail_senders": found}}) == 0
    capsys.readouterr()
    assert save(harness, {"progress": {"phase": "check"}}) == 0
    assert result(capsys)[0]["progress"]["gmail_senders"] == found

    assert save(harness, {"progress": {"gmail_senders": [{"domain": "x.fi"}]}}) == 1
    assert any(e.startswith("progress.gmail_senders.0") for e in result(capsys)[0]["errors"])
    assert save(harness, None, "--read") == 0
    assert result(capsys)[0]["progress"]["gmail_senders"] == found


def test_answers_and_progress_are_saved_together(harness, capsys):
    existing_config(harness)

    assert save(harness, {"evening": "20:00", "progress": {"phase": "check"}}) == 0

    res, _ = result(capsys)
    assert res["progress"]["phase"] == "check"
    assert saved(harness)["schedule"]["daily_hour"] == 20


def test_progress_in_the_wrong_shape_is_refused(harness, capsys):
    for progress, field in [({"phase": "lunch"}, "progress.phase"),
                            ({"source": "facebook"}, "progress.source"),
                            ({"sources": {"wilma": "maybe"}}, "progress.sources.wilma")]:
        assert save(harness, {"progress": progress}) == 1, progress
        res, _ = result(capsys)
        assert res["result"] == "invalid-answers"
        assert any(e.startswith(field) for e in res["errors"]), (progress, res["errors"])
    assert not progress_file(harness).exists()
