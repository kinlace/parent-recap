"""A run's peak memory, from the maximum resident sizes `resource.getrusage` gives for the run and
for its child processes, in the unit each system's profile says it gives them in; and what a Run
Report may hold: only the listed fields, each a number, a time or one of a fixed set of keys."""
from __future__ import annotations

import re

import pytest

from family_brief import last_run, run
from family_brief.last_run import peak_mb
from family_brief.system import profile_linux, profile_macos, profile_windows

LINUX, MACOS = profile_linux.PROFILE.maxrss_unit, profile_macos.PROFILE.maxrss_unit


@pytest.mark.parametrize("own, children, unit, mb", [
    (180 * 1024, 40 * 1024, LINUX, 180),          # Linux counts in kilobytes
    (40 * 1024, 180 * 1024, LINUX, 180),          # a child (the wilma CLI, Node) peaked higher
    (180 * 2**20, 40 * 2**20, MACOS, 180),        # macOS counts in bytes
    (int(179.6 * 2**20), 0, MACOS, 180),          # rounded to the nearest MB
    (0, 0, LINUX, 0),
])
def test_the_peak_is_the_larger_of_the_run_s_and_its_children_s_in_mb(own, children, unit, mb):
    assert peak_mb(own, children, unit) == mb


def test_windows_gives_no_peak():
    assert profile_windows.PROFILE.maxrss_unit is None


# ── What a Run Report may hold (ADR 0048)

GOOD = {
    "version": "0.12.0", "system": "macos", "system_version": "15.1", "at": "2026-11-14T19:00:00+00:00",
    "started": "schedule", "exit": 0, "seconds": 300, "peak_mb": 180, "causes": ["wilma-egress"],
    "sources": {"gmail": {"read": 3}, "wilma": {"failed": "wilma-egress"}, "whatsapp": {"failed": "unknown"}},
    "ai": {"backend": "gateway", "result": "busy"},
    "brief": {"outcome": "failed", "cause": "unknown"},
    "exception": {"type": "requests.exceptions.ConnectionError", "at": "actions/archive.py:42"},
}

FREE_TEXT = [
    "teacher.3b@kilo.example.fi",            # an address
    "Mia Virtanen",                          # a name
    "Retki Nuuksioon to 1.10.",              # a subject
    "/Users/anna/ParentRecap/state.json",    # a path
    "login failed: [AUTHENTICATIONFAILED]",  # an error message
]


def test_a_report_of_listed_fields_and_keys_has_no_problems():
    assert last_run.problems(GOOD) == []


def test_a_field_the_list_doesnt_name_is_a_problem():
    assert last_run.problems({**GOOD, "error": "unknown"}) == ["error"]


@pytest.mark.parametrize("text", FREE_TEXT)
@pytest.mark.parametrize("field, carrying", [
    ("version", lambda t: t),
    ("system", lambda t: t),
    ("system_version", lambda t: t),
    ("at", lambda t: t),
    ("started", lambda t: t),
    ("exit", lambda t: t),
    ("causes", lambda t: [t]),
    ("sources", lambda t: {"gmail": {"failed": t}}),
    ("sources", lambda t: {"gmail": {"read": 1, "error": t}}),
    ("sources", lambda t: {t: {"read": 1}}),
    ("ai", lambda t: {"backend": "claude", "result": t}),
    ("brief", lambda t: {"outcome": "failed", "cause": t}),
    ("brief", lambda t: {"outcome": "sent", "cause": t}),
    ("exception", lambda t: {"type": "OSError", "message": t}),
    ("exception", lambda t: {"type": t}),
    ("exception", lambda t: {"type": "OSError", "at": t}),
])
def test_a_field_carrying_free_text_is_a_problem(field, carrying, text):
    assert last_run.problems({**GOOD, field: carrying(text)}) == [field]


def test_a_failure_with_no_key_yet_is_an_unknown_one():
    report = last_run.Report()
    report.failed("gmail", "socket closed by imap.gmail.com")
    report.brief_ended("failed", "SMTP said no to parent@example.com")
    assert report.sources == {"gmail": {"failed": "unknown"}}
    assert report.brief == {"outcome": "failed", "cause": "unknown"}


def test_an_exception_is_its_type_and_the_line_of_own_code_never_its_message():
    report = last_run.Report()
    try:
        last_run.path(None)  # type: ignore[arg-type]  - fails inside last_run
    except AttributeError as e:
        report.ended_on(e)
    assert report.exception is not None
    assert report.exception["type"] == "AttributeError"
    assert re.fullmatch(r"last_run\.py:[0-9]+", report.exception["at"])


@pytest.mark.parametrize("error, result", [
    (None, "ok"),
    ("Claude timed out twice after 600s each", "timed-out"),
    ("claude -p failed: API Error: 529 Overloaded", "busy"),
    ("Error: usage limit reached, too many requests", "failed"),  # waiting won't help
    ("Error: something went wrong for teacher.3b@kilo.example.fi", "failed"),
])
def test_the_ai_s_result_is_one_of_four_words(error, result):
    assert run._ai_result(error) == result
