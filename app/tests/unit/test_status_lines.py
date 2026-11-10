"""The hosted version's status on its own: when a Household's run is overdue, what its line
says, and that only check keys and statuses are taken from what a Household's `signup health`
says."""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from family_brief import last_run
from family_brief.signup.status import CAUSES, Status, _checks, line, overdue

HELSINKI = ZoneInfo("Europe/Helsinki")


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=HELSINKI)


@pytest.mark.parametrize("since, now, late", [
    (at(29, 21, 4), at(30, 12), False),   # last night's run ended
    (at(29, 21, 4), at(30, 21, 30), False),  # tonight's may still be going
    (at(29, 21, 4), at(30, 22, 1), True),   # tonight's should have ended an hour ago
    (at(28, 21, 4), at(30, 12), True),    # last night's never ended
    (at(30, 21, 50), at(30, 23), False),  # tonight's ended late
    (at(30, 3), at(30, 12), False),       # installed this morning, its first evening to come
    (None, at(30, 12), True),
])
def test_a_run_is_overdue_once_its_evening_and_an_hour_have_gone_by(since, now, late):
    assert overdue(since, 21, 0, "Europe/Helsinki", now.astimezone(timezone.utc)) is late


def test_the_evening_is_the_household_s_own_timezone_s():
    # At 20:30 UTC, 22:30 in Helsinki, Helsinki's 21:00 (19:00 UTC) is over an hour ago, but
    # UTC's 21:00 is still to come: a run that ended at 21:30 UTC the day before is late only
    # for Helsinki.
    since = datetime(2026, 10, 29, 21, 30, tzinfo=timezone.utc)
    now = datetime(2026, 10, 30, 20, 30, tzinfo=timezone.utc)
    assert overdue(since, 21, 0, "Europe/Helsinki", now)
    assert not overdue(since, 21, 0, "UTC", now)


def test_only_check_keys_and_statuses_are_taken_each_key_at_its_worst():
    said = [["config", "ok"], ["language", "ok"], ["language", "warn"], ["myclub", "fail"],
            ["myclub", "ok"], ["Mia Virtanen", "ok"], ["wilma", "signed in, students: Mia"],
            ["parent@example.com", "fail"], ["wilma", "ok"], "gmail", ["a", "b", "c"], [1, "ok"]]

    assert _checks(said) == {"config": "ok", "language": "warn", "myclub": "fail", "wilma": "ok"}
    assert _checks("not a list") == {}


def test_a_household_s_line():
    ran = datetime(2026, 10, 30, 18, 4, tzinfo=timezone.utc)
    checks = {"config": "ok", "wilma": "fail", "schedule": "warn"}

    assert line(Status("ph-0123abcd", "21:05", ran, 0, False, checks)) == (
        "✅ ph-0123abcd  21:05  last run 2026-10-30 18:04 UTC, ok\n"
        "   doctor: ✅ config  ❌ wilma  ⚠️ schedule")
    assert line(Status("ph-0123abcd", "21:05", ran, 0, False, checks, 7, 180)).startswith(
        "✅ ph-0123abcd  21:05  last run 2026-10-30 18:04 UTC, ok, 7 s, 180 MB\n")
    assert line(Status("ph-0123abcd", "21:05", ran, 0, False, checks, seconds=7)).startswith(
        "✅ ph-0123abcd  21:05  last run 2026-10-30 18:04 UTC, ok, 7 s\n")  # Windows: no peak
    assert line(Status("ph-0123abcd", "21:05", ran, 1, True, {})).splitlines() == [
        "❌ ph-0123abcd  21:05  last run 2026-10-30 18:04 UTC, failed (exit 1), overdue",
        "   doctor: (no checks)"]
    assert line(Status("ph-0123abcd", "21:05", None, None, True, checks)).startswith(
        "⚠️ ph-0123abcd  21:05  no run yet, overdue\n")
    assert line(Status("ph-0123abcd", "21:05", unknown="sudo couldn't be run")) == \
        "❓ ph-0123abcd  21:05  status unknown: sudo couldn't be run"


def test_a_household_s_note_is_beside_its_user():
    ran = datetime(2026, 10, 30, 18, 4, tzinfo=timezone.utc)

    assert line(Status("ph-0123abcd", "21:05", ran, 0, note="赵晗博, test family")).startswith(
        "✅ ph-0123abcd (赵晗博, test family)  21:05  last run 2026-10-30 18:04 UTC, ok\n")
    assert line(Status("ph-0123abcd", "21:05", unknown="sudo couldn't be run",
                       note="赵晗博")) == \
        "❓ ph-0123abcd (赵晗博)  21:05  status unknown: sudo couldn't be run"


def test_a_cause_the_household_named_marks_it_and_is_said_on_a_line_of_its_own():
    """A Household whose Wilma couldn't be read because the Finnish egress didn't answer (ADR
    0046) is marked, so the nightly check's mail has it, with the cause named."""
    ran = datetime(2026, 10, 30, 18, 4, tzinfo=timezone.utc)

    assert line(Status("ph-0123abcd", "21:05", ran, 0, checks={"wilma": "ok"},
                       causes=("wilma-egress",))).splitlines() == [
        "⚠️ ph-0123abcd  21:05  last run 2026-10-30 18:04 UTC, ok",
        "   doctor: ✅ wilma",
        "   cause: wilma: Finnish egress unreachable"]


def test_only_cause_keys_are_taken():
    assert last_run.causes(["wilma-egress", "wilma-egress", "Mia Virtanen", 3, "gmail"]) == \
        ("wilma-egress", "gmail")
    assert last_run.causes("wilma-egress") == ()
    assert set(CAUSES) == {"wilma-egress"}  # and `status` keeps only these
