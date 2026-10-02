"""Program text for every language a Recipient can read (ADR 0004).

`en`, `zh` and `fi` have reviewed tables in brief_text.py, one for the Brief and one for Weekend Picks.
For any other language the model translates each English table once; the result is checked, kept on the Mac in a languages folder next to the
state file (~/.family/languages/) and reused on every later run. Until that has worked, the
language gets the English program text, and the next run tries again. Only the reviewed languages
have golden Briefs and eval cases."""
from __future__ import annotations

import dataclasses
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from string import Formatter, Template
from typing import Any, Generic, TypeVar

from pydantic import TypeAdapter, ValidationError

from .brief_text import EN, PRODUCT_NAME, TEXT, WEEKEND_TEXT, BriefText, Language, Plural, WeekendText
from .config import Config

log = logging.getLogger(__name__)

# Key Finnish words a Brief in any other language keeps as written, so parents can still search
# Wilma and talk to the teacher with them (ADR 0004).
FINNISH_WORDS = "reissuvihko, retki, vanhempainilta, eväät, vaatetus"


def is_finnish(language: Language) -> bool:
    return language == "fi"


_SYSTEM_PROMPT = Template("""You translate the fixed text of a program that emails a Household a daily Brief about their kids' school and hobbies in Finland. Translate every value of the JSON object from English into the language whose code is $code.

1. Reply with one JSON object with exactly the same keys, and the same number of entries in every list
2. Keep every {placeholder} in braces exactly as written, and keep the emoji, the Markdown (## and **) and the names Gmail, Wilma, WhatsApp, MyClub, Google Calendar, Claude, Codex, .ics, $product and ~/FamilyBrief as they are
3. language_name: the English name of the language (e.g. Swedish)
4. quotes: the quotation marks the language uses, shown around a word or two; never straight double quotes
5. weekdays: short weekday names, Monday first; who: the words for mom, dad and either parent
6. months: short month names, January first; date and date_time: a date, and a date with its time, the way people write them in a message in this language (order, separators, 24-hour clock), with {day}, the month as {month_name} or as its number {month}, {weekday} if the language usually writes it, and in date_time {hour} and {minute}
7. messages and events: "one" for a single one and "other" for several, with {n} for the number
8. The Brief is the daily message. Its Digest, Notices and Action Items, the Household and each Kid are what it is about: use the everyday words a parent reading this language would use
9. No markdown fences, no explanations
""")


def code(value: str) -> Language:
    """`value` as a language code, or ValueError; for the command line, since it names a file too."""
    try:
        return TypeAdapter(Language).validate_python(value)
    except ValidationError as e:
        raise ValueError(f"{value!r} is not a two- or three-letter language code") from e


T = TypeVar("T")


@dataclass(frozen=True)
class Table(Generic[T]):
    """One of the program's text tables: reviewed for some languages, translated once for any other."""
    what: str                    # what the text is in, for the log
    reviewed: dict[Language, T]  # English among them
    file: str                    # the stored translation's file name, with {language}
    prompt: Template             # the one-time translation's instructions, with $code (and $product)

    @property
    def english(self) -> T:
        return self.reviewed["en"]


BRIEF: Table[BriefText] = Table("Brief", TEXT, "{language}.json", _SYSTEM_PROMPT)
WEEKEND: Table[WeekendText] = Table("Weekend Picks", WEEKEND_TEXT, "{language}.weekend.json", Template(
    """You translate the fixed text of a program that emails a Household its Weekend Picks: family events for the coming weekend in Finland, picked for their kids. Translate every value of the JSON object from English into the language whose code is $code.

1. Reply with one JSON object with exactly the same keys
2. Keep every {placeholder} in braces exactly as written, and keep the emoji, the Markdown (**), the HTML tags (<b>) and the name $product as they are
3. Weekend Picks is the name of this email: use the everyday words a parent reading this language would use
4. No markdown fences, no explanations
"""))


def _path(cfg: Config, language: Language, table: Table) -> Path:
    return cfg.resolved_state_path().parent / "languages" / table.file.format(language=language)


_NUMBERS = {"n", "day", "month", "hour", "minute"}  # the placeholders the program fills with a number


def _placeholders(text: str) -> set[str]:
    return {name for _, name, _, _ in Formatter().parse(text) if name is not None}


# A date is written from whichever of its parts the language uses, but always with its day and
# month, and a date-time with its time too: {placeholder: the ones it needs, the ones it may use}.
_DATE_PARTS = {"weekday", "day", "month", "month_name"}
_DATES = {"date": ({"day"}, _DATE_PARTS),
          "date_time": ({"day", "hour", "minute"}, _DATE_PARTS | {"hour", "minute"})}


def _checked_text(english: str, got: Any, name: str = "") -> str:
    """`got` standing in for `english` (the entry `name`), with the English spacing the program
    joins pieces with."""
    if not isinstance(got, str) or not got.strip():
        raise ValueError(f"no text for {english!r}")
    needs, may = _DATES.get(name, (_placeholders(english), _placeholders(english)))
    found = _placeholders(got)
    if not needs <= found <= may or (name in _DATES and not found & {"month", "month_name"}):
        raise ValueError(f"{got!r} doesn't keep the placeholders of {english!r}")
    if PRODUCT_NAME in english and PRODUCT_NAME not in got:  # also one stored before the rename
        raise ValueError(f"{got!r} doesn't keep the name {PRODUCT_NAME}")
    try:  # a placeholder the program can't fill, such as {date:d} for a date's text, would fail on the night
        got.format_map({name: 1 if name in _NUMBERS else "text" for name in _placeholders(got)})
    except (ValueError, IndexError, KeyError) as e:
        raise ValueError(f"{got!r} can't be filled in: {e}") from e
    lead, trail = english[:len(english) - len(english.lstrip())], english[len(english.rstrip()):]
    return lead + got.strip() + trail


def _checked(data: Any, english_table: T) -> T:
    """A translated table as program text, or ValueError when an entry can't stand in for the English one."""
    if not isinstance(data, dict):
        raise ValueError("the program text is not an object")
    fields: dict[str, Any] = {}
    for f in dataclasses.fields(english_table):  # type: ignore[arg-type]
        english, got = getattr(english_table, f.name), data.get(f.name)
        if isinstance(english, Plural):
            if not isinstance(got, dict):
                raise ValueError(f"no {f.name}")
            fields[f.name] = Plural(_checked_text(english.one, got.get("one")),
                                    _checked_text(english.other, got.get("other")))
        elif isinstance(english, tuple):
            if not isinstance(got, list) or len(got) != len(english):
                raise ValueError(f"{f.name} needs {len(english)} entries")
            fields[f.name] = tuple(_checked_text(e, g) for e, g in zip(english, got))
        else:
            fields[f.name] = _checked_text(english, got, f.name)
    if '"' in fields.get("quotes", ""):
        raise ValueError("quotes would break the model's JSON")
    return type(english_table)(**fields)


def _stored(cfg: Config, language: Language, table: Table[T]) -> T | None:
    try:
        return _checked(json.loads(_path(cfg, language, table).read_text()), table.english)
    except (OSError, ValueError):
        return None


def own(cfg: Config, language: Language, table: Table[T]) -> T | None:
    """`language`'s own text in `table`: reviewed or stored, None until its translation has worked."""
    return table.reviewed.get(language) or _stored(cfg, language, table)


def text(cfg: Config, language: Language) -> BriefText:
    """The Brief's program text for `language`: its own, or else English, with the language's
    code standing in for its name so the model still writes in it."""
    return own(cfg, language, BRIEF) or dataclasses.replace(EN, language_name=f"the language whose code is {language}")


def prepare(cfg: Config, language: Language, table: Table = BRIEF) -> None:
    """Make sure `language` has its own text in `table`, translating the English table once if it
    has none. Raises when that translation fails or doesn't check out."""
    if own(cfg, language, table) is not None:
        return
    from .summarize import call_llm  # summarize reads program text from here
    log.info("Translating the program's %s text into %s, once", table.what, language)
    reply = call_llm(cfg, json.dumps(dataclasses.asdict(table.english), ensure_ascii=False, indent=2),
                     table.prompt.substitute(code=language, product=PRODUCT_NAME))
    translated = _checked(reply.data, table.english)
    path = _path(cfg, language, table)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dataclasses.asdict(translated), ensure_ascii=False, indent=2))


TABLES: tuple[Table, ...] = (BRIEF, WEEKEND)


def describe(cfg: Config, language: Language, tables: tuple[Table, ...] = TABLES) -> tuple[bool, str]:
    """Whether `language` has its own text in every one of `tables`, and where it comes from
    (language command, doctor)."""
    if language in TEXT:
        return True, "reviewed program text"
    if all(_stored(cfg, language, table) is not None for table in tables):
        return True, f"program text translated once, kept in {_path(cfg, language, BRIEF).parent}"
    return False, f"English program text until its translation works (family-brief language {language})"


def prepare_each(cfg: Config, languages: list[Language], tables: tuple[Table, ...] = (BRIEF,)) -> None:
    """Every table's text in every language a run needs. One whose translation fails is English
    this time and tried again on the next run."""
    for language in languages:
        for table in tables:
            try:
                prepare(cfg, language, table)
            except Exception as e:
                log.error("Translating the program's %s text into %s failed: %s (English this time)",
                          table.what, language, e)
