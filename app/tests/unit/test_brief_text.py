"""The program's own Brief and Weekend Picks text (brief_text): counts, dates and the reviewed tables."""
from __future__ import annotations

from dataclasses import fields
from datetime import date, datetime
from string import Formatter

import pytest
from pydantic import TypeAdapter, ValidationError

from family_brief.brief_text import TEXT, WEEKEND_TEXT, BriefText, Language, Plural

LANGUAGE = TypeAdapter(Language)


@pytest.mark.parametrize("n, words", [(0, "0 viestiä"), (1, "1 viesti"), (2, "2 viestiä")])
def test_plural_picks_the_form_for_the_count(n, words):
    assert Plural("{n} viesti", "{n} viestiä")(n) == words


@pytest.mark.parametrize("language, on, at", [
    ("en", "Thu 1 Oct", "Thu 1 Oct 09:05"),
    ("zh", "10月1日 周四", "10月1日 周四 09:05"),
    ("fi", "to 1.10.", "to 1.10. klo 9.05"),
])
def test_each_language_writes_a_date_and_a_time_its_own_way(language, on, at):
    t = TEXT[language]

    assert t.on(date(2026, 10, 1)) == on
    assert t.at(datetime(2026, 10, 1, 9, 5)) == at


def test_placeholder_words_name_each_kind_in_the_language():
    assert TEXT["fi"].placeholder_words() == {"phone": "puhelinnumero", "email": "sähköpostiosoite",
                                              "link": "linkki", "person": "henkilö"}


@pytest.mark.parametrize("given, code", [("en", "en"), (" ZH ", "zh"), ("Sv", "sv"), ("smn", "smn")])
def test_a_language_is_its_code_in_lower_case(given, code):
    assert LANGUAGE.validate_python(given) == code


@pytest.mark.parametrize("given", ["en-GB", "e", "english", "", 5])
def test_anything_but_a_language_code_is_refused(given):
    with pytest.raises(ValidationError):
        LANGUAGE.validate_python(given)


def placeholders(text: str) -> set[str]:
    return {name for _, name, _, _ in Formatter().parse(text) if name}


@pytest.mark.parametrize("language", TEXT)
def test_every_reviewed_table_has_the_same_placeholders_as_english(language):
    # A translated string missing {assistant} or adding {n} would break the Brief at format time.
    for f in fields(BriefText):
        en, other = getattr(TEXT["en"], f.name), getattr(TEXT[language], f.name)
        if isinstance(en, str) and f.name not in ("date", "date_time"):  # those may pick their parts
            assert placeholders(other) == placeholders(en), f.name
        elif isinstance(en, tuple):
            assert len(other) == len(en), f.name


@pytest.mark.parametrize("language", WEEKEND_TEXT)
def test_every_weekend_table_has_the_same_placeholders_as_english(language):
    en, other = WEEKEND_TEXT["en"], WEEKEND_TEXT[language]
    for name in vars(en):
        if name != "when":
            assert placeholders(getattr(other, name)) == placeholders(getattr(en, name)), name
