"""The Run Report each evening run leaves in last-run.json (ADR 0048): how the run ended, in fixed
keys and numbers only, however it ended, and `doctor`'s line for it. Assertions are on the file
the run leaves and doctor's output."""
from __future__ import annotations

import json
import re
from datetime import timedelta
from typing import Any

import pytest

from family_brief import last_run
from family_brief.actions import archive
from family_brief.system import jobs

from conftest import NOW
from nights import normal_night

# Free text a run's errors can carry, none of which may reach a report.
ADDRESS = "teacher.3b@kilo.example.fi"
PATH = "/Users/anna/ParentRecap"
SUBJECT = "Retki Nuuksioon"


def scheduled_after_the_brief_time(h, hours: float, last_run_hours_ago: float) -> None:
    """NOW is `hours` after the Brief's time on the computer's own clock, and the last run ended
    `last_run_hours_ago`."""
    evening = (NOW - timedelta(hours=hours)).astimezone()
    h.config["schedule"] = {"daily_hour": evening.hour, "daily_minute": evening.minute}
    at = (NOW - timedelta(hours=last_run_hours_ago)).isoformat()
    h.state_path.parent.mkdir(parents=True, exist_ok=True)
    h.state_path.write_text(json.dumps({"last_run_at": at, "caught_up_at": at}))


def report_of(h) -> dict[str, Any]:
    text = h.state_path.with_name("last-run.json").read_text()
    for free in (ADDRESS, PATH, SUBJECT, "Mia", "anna"):
        assert free not in text
    report = json.loads(text)
    assert last_run.problems(report) == []
    return report


def test_a_run_that_sent_its_brief_says_so_with_what_each_source_read(harness):
    normal_night(harness, "en")

    assert harness.run() == 0

    report = report_of(harness)
    assert report["brief"] == {"outcome": "sent"}
    assert report["ai"] == {"backend": "claude", "result": "ok"}
    assert report["sources"]["gmail"] == {"read": 2}
    assert report["sources"]["wilma"] == {"read": 1}
    assert report["started"] == "by-hand"
    assert report["exit"] == 0 and isinstance(report["seconds"], int)
    assert report["system"] in last_run.SYSTEMS
    assert re.fullmatch(r"[0-9].*|unknown", report["version"])
    assert "exception" not in report


def test_a_night_with_nothing_to_tell_says_so(harness):
    assert harness.run() == 0

    report = report_of(harness)
    assert report["brief"] == {"outcome": "nothing-to-tell"}
    assert "ai" not in report  # nothing new, so the model wasn't asked


def test_a_source_that_failed_is_an_unknown_failure_without_its_text(harness):
    normal_night(harness, "en")
    harness.sources["gmail"] = RuntimeError(f"login refused for {ADDRESS} reading {SUBJECT} in {PATH}")

    assert harness.run() == 0

    report = report_of(harness)
    assert report["sources"]["gmail"] == {"failed": "unknown"}
    assert report["sources"]["wilma"] == {"read": 1}
    assert report["brief"] == {"outcome": "sent"}


def test_a_night_no_source_could_be_read_says_the_brief_failed_for_it(harness):
    for source in ("gmail", "wilma", "whatsapp", "myclub"):
        harness.sources[source] = RuntimeError(f"down for {ADDRESS}")

    harness.run()

    report = report_of(harness)
    assert report["brief"] == {"outcome": "failed", "cause": "nothing-read"}
    assert all(r == {"failed": "unknown"} for r in report["sources"].values())


def test_a_brief_nobody_got_failed_and_a_model_that_failed_says_so(harness):
    normal_night(harness, "en")
    harness.model_error = f"Error: quota exceeded for {ADDRESS}"
    harness.email_error = RuntimeError(f"SMTP refused {ADDRESS}")

    harness.run()

    report = report_of(harness)
    assert report["ai"] == {"backend": "claude", "result": "failed"}
    assert report["brief"] == {"outcome": "failed", "cause": "unknown"}


def test_a_run_that_ended_on_an_exception_names_its_type_and_line_not_its_message(harness, monkeypatch):
    normal_night(harness, "en")

    def disk_full(*_a: object) -> None:
        raise OSError(f"disk full writing {PATH}/{SUBJECT}")
    monkeypatch.setattr(archive, "write", disk_full)

    with pytest.raises(OSError):
        harness.run()

    report = report_of(harness)
    assert report["exit"] == 1
    assert report["exception"]["type"] == "OSError"
    # The test's own function raised it, so the line is the run's own that called it.
    assert re.fullmatch(r"run\.py:[0-9]+", report["exception"]["at"])
    assert report["brief"] == {"outcome": "sent"}  # sent before the archive failed


def test_writing_the_report_never_fails_the_run(harness):
    normal_night(harness, "en")
    harness.state_path.with_name("last-run.json").mkdir(parents=True)  # a file can't replace it

    assert harness.run() == 0

    assert len(harness.sent) == 1


def test_a_run_by_the_scheduler_says_whether_it_was_on_time_or_caught_up(harness, monkeypatch):
    monkeypatch.setenv(jobs.SCHEDULED, "1")
    normal_night(harness, "en")
    scheduled_after_the_brief_time(harness, hours=3, last_run_hours_ago=27)

    assert harness.run() == 0
    assert report_of(harness)["started"] == "catch-up"

    scheduled_after_the_brief_time(harness, hours=0, last_run_hours_ago=24)
    assert harness.run() == 0
    assert report_of(harness)["started"] == "schedule"


def test_doctor_says_how_the_last_run_ended(harness, capsys):
    normal_night(harness, "en")
    harness.sources["gmail"] = RuntimeError(f"login refused for {ADDRESS}")
    harness.run()
    capsys.readouterr()

    harness.cli("doctor", "--skip-llm")

    [line] = [line for line in capsys.readouterr().out.splitlines() if "Last run" in line]
    assert "the last run ended 0 hours ago: its Brief was sent; couldn't read gmail (unknown)" in line
    assert ADDRESS not in line


def test_a_note_from_an_older_version_still_reads_and_doctor_says_its_exit_code(harness, capsys):
    harness.run()
    harness.state_path.with_name("last-run.json").write_text(json.dumps(
        {"at": "2026-09-27T17:00:00+00:00", "exit": 1, "seconds": 12, "peak_mb": 180}))
    capsys.readouterr()

    harness.cli("doctor", "--skip-llm")

    [line] = [line for line in capsys.readouterr().out.splitlines() if "Last run" in line]
    assert line.endswith(": exit code 1")
