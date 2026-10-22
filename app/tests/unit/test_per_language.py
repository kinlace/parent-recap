"""The per-language fan-out the Brief and Weekend Picks share (translate.per_language): Recipients
grouped by language, one translation each, and the original with a note when one fails."""
from __future__ import annotations

from family_brief.translate import per_language


def as_written(to: list[str], note: str) -> tuple:
    return ("en", to, note)


def in_language(language: str, to: list[str]) -> tuple:
    if language == "de":
        raise ValueError("the model is down")
    return (language, to, "")


def failed(language: str) -> str:
    return f"{language}: not translated"


def test_each_language_reads_its_own_version_the_original_first():
    by_language = {"en": ["a@x", "b@x"], "zh": ["c@x"], "fi": ["d@x"]}
    assert per_language("the Brief", "en", by_language, as_written, in_language, failed) == [
        ("en", ["a@x", "b@x"], ""), ("zh", ["c@x"], ""), ("fi", ["d@x"], "")]


def test_a_failed_translation_sends_that_language_the_original_with_a_note_in_theirs():
    by_language = {"en": ["a@x"], "de": ["b@x"], "zh": ["c@x"]}
    assert per_language("the Brief", "en", by_language, as_written, in_language, failed) == [
        ("en", ["a@x"], ""), ("en", ["b@x"], "de: not translated"), ("zh", ["c@x"], "")]


def test_a_single_language_household_translates_nothing():
    def never(language: str, to: list[str]) -> tuple:
        raise AssertionError("translated")
    assert per_language("the Brief", "en", {"en": ["a@x", "b@x"]}, as_written, never, failed) == [
        ("en", ["a@x", "b@x"], "")]


def test_no_recipients_gives_the_original_alone_to_nobody():
    assert per_language("the Brief", "en", {}, as_written, in_language, failed) == [("en", [], "")]
