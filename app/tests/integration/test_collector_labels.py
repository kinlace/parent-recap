"""Labels the Sources add to Messages are model input, so they are English whatever the Brief's
language; the summarize prompt explains them (see summarize._build_prompt)."""
from __future__ import annotations

from datetime import timedelta

import pytest
import time_machine

from conftest import NOW
from family_brief.collectors import wilma
from family_brief.config import Config
from family_brief.state import State


@pytest.mark.parametrize("day, label", [
    ("2026-09-27", "Sun · today"),
    ("2026-09-28", "Mon · tomorrow"),
    ("2026-09-29", "Tue · day after tomorrow"),
    ("2026-10-01", "Thu · in 4 days"),
])
@time_machine.travel(NOW, tick=False)
def test_wilma_timetable_is_headed_with_its_date(sources, day, label):
    cli = sources.fakes["wilma"]
    cli.set_up()
    cli.answers[("schedule", "list")] = {"when": "tomorrow", "date": day, "students": [
        {"student": {"name": "Virtanen Aino"}, "lessons": [
            {"date": day, "start": "08:15", "end": "09:45", "subject": "Liikunta", "room": "ulkokenttä"}]}]}

    [msg] = wilma.collect(Config.model_validate(sources.config), State(sources.home / "state.json"), []).messages

    assert msg.subject == f"Timetable for the next school day (from {day})"
    assert msg.body.splitlines()[:3] == [
        f"[{day} {label}]", "— Virtanen Aino —", "  08:15–09:45  Liikunta  @ ulkokenttä"]


EXAM = {"examId": 1027029, "date": "2026-09-29", "name": "Murtoluvut", "subject": "Matematiikka",
        "topic": "Koealue jakso 3: s. 62-103", "teacher": "Mattila Olli", "time": None}


def _exams(cli, *exams):
    cli.answers[("exams",)] = {"students": [
        {"student": {"studentNumber": "1001", "name": "Virtanen Aino"}, "exams": list(exams)}]}


def _collect(sources):
    """One evening's Wilma Messages, with what it told kept, as a run keeps it."""
    state = State(sources.home / "state.json")
    messages = wilma.collect(Config.model_validate(sources.config), state, []).messages
    state.save()
    return messages


def test_a_wilma_exam_in_the_coming_week_reaches_the_brief_with_what_to_study(sources):
    """#5: an upcoming exam's date, subject, name and topic, headed like the timetable."""
    cli = sources.fakes["wilma"]
    cli.set_up()
    _exams(cli, EXAM, {**EXAM, "examId": 2, "date": "2026-10-20"})  # the second, beyond a week

    with time_machine.travel(NOW, tick=False):  # a Sunday evening
        [msg] = [m for m in _collect(sources) if m.chat_name == "exams"]

    assert msg.subject == "Exam: Matematiikka – Murtoluvut"
    assert msg.kid_hint == "Virtanen Aino"
    assert msg.body.splitlines() == [
        "[2026-09-29 Tue · day after tomorrow]", "Subject: Matematiikka", "Exam: Murtoluvut",
        "What to study: Koealue jakso 3: s. 62-103", "Teacher: Mattila Olli"]


def test_a_wilma_exam_comes_again_only_the_evening_before_it(sources):
    cli = sources.fakes["wilma"]
    cli.set_up()
    _exams(cli, EXAM)
    exams = lambda: [m for m in _collect(sources) if m.chat_name == "exams"]  # noqa: E731

    with time_machine.travel(NOW, tick=False):
        assert len(exams()) == 1
    with time_machine.travel(NOW + timedelta(hours=1), tick=False):
        assert exams() == []  # once told, not again the same evening
    with time_machine.travel(NOW + timedelta(days=1), tick=False):  # Monday: Tuesday's exam
        [reminder] = exams()
        assert reminder.body.splitlines()[0] == "[2026-09-29 Tue · tomorrow]"
        assert "Reminder" in reminder.body.splitlines()[-1]
        assert exams() == []
