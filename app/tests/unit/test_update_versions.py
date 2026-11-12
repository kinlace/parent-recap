"""The update's pure parts (ADR 0047): which version is newer, and which checks doctor's output
names as failing."""
from __future__ import annotations

import pytest

from family_brief.update import doctor_failures, newer, parsed


@pytest.mark.parametrize("candidate, installed, is_newer", [
    ("0.20.0", "0.19.0", True),
    ("0.19.10", "0.19.9", True),   # by number, not as text
    ("1.0.0", "0.99.99", True),
    ("0.19.0", "0.19.0", False),
    ("0.18.5", "0.19.0", False),
    ("v0.20.0", "0.19.0", False),  # not a version the program knows how to read
    ("0.20", "0.19.0", False),
    ("0.20.0", "unknown", False),
    ("0.20.0-rc1", "0.19.0", False),
])
def test_newer(candidate, installed, is_newer):
    assert newer(candidate, installed) is is_newer


def test_parsed():
    assert parsed("0.19.2") == (0, 19, 2)
    assert parsed(" 0.19.2\n") == (0, 19, 2)
    assert parsed("0.19") is None


def test_doctor_failures_are_the_failing_items_without_their_details():
    output = ("✅ Config file: ~/.family/config.yaml, 2 kids\n"
              "⚠️  Language fi: no reviewed text\n"
              "❌ Wilma (Mia): couldn't sign in: 401\n"
              "❌ Calendar: the feed answered 500\n"
              "\n13 checks: 10 OK, 1 warnings, 2 to fix\n")

    assert doctor_failures(output) == {"Wilma (Mia)", "Calendar"}


def test_no_failures_in_doctor_s_output():
    assert doctor_failures("✅ Config file: fine\n\n1 checks: 1 OK, 0 warnings, 0 to fix\n") == set()
