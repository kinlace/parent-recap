"""The per-language fan-out the Brief and Weekend Picks share (translate.per_language): Recipients
grouped by language, one translation each, and the original with a note when one fails."""
from __future__ import annotations

import pytest

from family_brief.translate import per_language


def translated(language: str) -> str:
    if language == "de":
        raise ValueError("the model is down")
    return f"hello in {language}"


def in_language(language: str, to: list[str], text: str) -> tuple:
    return (text, to, "")


def as_written(to: list[str], note: str) -> tuple:
    return ("hello", to, note)


def failed(language: str) -> str:
    return f"{language}: not translated"


def fan_out(by_language: dict[str, list[str]], translated=translated, in_language=in_language) -> list[tuple]:
    return per_language("the Brief", "en", by_language, translated, in_language, as_written, failed)


def test_each_language_reads_its_own_version_the_original_first():
    assert fan_out({"en": ["a@x", "b@x"], "zh": ["c@x"], "fi": ["d@x"]}) == [
        ("hello", ["a@x", "b@x"], ""), ("hello in zh", ["c@x"], ""), ("hello in fi", ["d@x"], "")]


def test_a_failed_translation_sends_that_language_the_original_with_a_note_in_theirs():
    assert fan_out({"en": ["a@x"], "de": ["b@x"], "zh": ["c@x"]}) == [
        ("hello", ["a@x"], ""), ("hello", ["b@x"], "de: not translated"), ("hello in zh", ["c@x"], "")]


def test_a_single_language_household_translates_nothing():
    def never(language: str) -> str:
        raise AssertionError("translated")
    assert fan_out({"en": ["a@x", "b@x"]}, translated=never) == [("hello", ["a@x", "b@x"], "")]


def test_no_recipients_gives_the_original_alone_to_nobody():
    assert fan_out({}) == [("hello", [], "")]


def test_only_the_translation_falls_back_to_the_original():
    # A version that can't be made is a bug to see, not a translation to report as failed.
    def broken(language: str, to: list[str], text: str) -> tuple:
        raise KeyError("no such event")
    with pytest.raises(KeyError):
        fan_out({"en": ["a@x"], "zh": ["c@x"]}, in_language=broken)
