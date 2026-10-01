"""Eval cases: one synthetic night each, as a YAML file in a case folder.

A folder holds `household.yaml` (the Config fields the prompt uses: `timezone`, `kids`,
`whatsapp.chats`) and one `<name>.yaml` per night:

    covers: [finnish-dates]            # which hard part this night exercises
    note: why the night is hard
    now: 2026-10-04T21:00:00+03:00     # the evening the Brief is written
    household: {...}                   # optional, replaces household.yaml for this night
    messages:                          # the night's Messages
      - {id: w-1, source: wilma, at: 2026-10-04T14:00:00+03:00, sender: ..., subject: ...,
         chat: ..., kid_hint: ..., body: ...}   # WhatsApp kid_hint defaults to the chat map
    calendar: [{id, summary, start, end, location}]   # existing Google Calendar, next 7 days
    queued: [{external_id, source, title, start, end, kid}]  # added from a Source, e.g. MyClub
    earlier_briefs: [{date, per_kid: [...]}]  # as archive.recent_points returns them
    expect:
      action_items: [{what: [keywords], kid: Aino, by: 2026-10-06, optional: false}]
      calendar_events: [{title: [keywords], start: 2026-10-08T09:00}]   # local wall clock
      notices:
        must: [{text: [keywords], kid: Aino, where: notices|action_items|both}]
        must_not: [{text: [keywords]}]   # `where` defaults to notices

Keyword lists are explained in `score.py`. An `optional` entry may appear or not without
affecting the score. A private folder in the same shape can be scored with `--cases DIR`."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ..collectors.base import Message
from ..config import Config

BUNDLED = Path(__file__).parent / "cases"


class _Loader(yaml.SafeLoader):
    """Keeps dates, times and numbers as the strings they were written as. PyYAML would read the
    keyword 18:00 as the number 1080 and 9.10 as 9.1; pydantic converts where a number is meant."""


_AS_WRITTEN = {"tag:yaml.org,2002:timestamp", "tag:yaml.org,2002:int", "tag:yaml.org,2002:float"}
_Loader.yaml_implicit_resolvers = {
    ch: [(tag, rx) for tag, rx in resolvers if tag not in _AS_WRITTEN]
    for ch, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}

Spec = list[str | list[str]]
KidSpec = str | list[str] | None


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExpectedAction(_Strict):
    what: Spec
    kid: KidSpec = None
    by: date | None = None
    optional: bool = False


class ExpectedEvent(_Strict):
    title: Spec
    start: str | None = None
    optional: bool = False

    @field_validator("start")
    @classmethod
    def _wall_clock(cls, v: str | None) -> str | None:
        if v is not None:
            datetime.fromisoformat(v)  # a date, or a date and local time without an offset
        return v


class NoticeRule(_Strict):
    text: Spec
    kid: KidSpec = None
    where: Literal["notices", "action_items", "both"] = "notices"


class NoticeRules(_Strict):
    must: list[NoticeRule] = Field(default_factory=list)
    must_not: list[NoticeRule] = Field(default_factory=list)


class Expect(_Strict):
    action_items: list[ExpectedAction] = Field(default_factory=list)
    calendar_events: list[ExpectedEvent] = Field(default_factory=list)
    notices: NoticeRules = NoticeRules()


class CaseMessage(_Strict):
    id: str
    source: Literal["gmail", "wilma", "whatsapp", "myclub"]
    at: datetime
    body: str = ""
    sender: str | None = None
    recipient: str | None = None
    subject: str | None = None
    chat: str | None = None
    kid_hint: str | None = None


class CaseFile(_Strict):
    covers: list[str]
    note: str = ""
    now: datetime
    household: dict[str, Any] | None = None
    messages: list[CaseMessage] = Field(default_factory=list)
    calendar: list[dict[str, Any]] = Field(default_factory=list)
    queued: list[dict[str, Any]] = Field(default_factory=list)
    earlier_briefs: list[dict[str, Any]] = Field(default_factory=list)
    expect: Expect = Expect()


@dataclass
class Case:
    name: str
    covers: list[str]
    now: datetime
    household: Config
    messages: list[Message]
    calendar: list[dict[str, Any]] = field(default_factory=list)
    queued: list[dict[str, Any]] = field(default_factory=list)
    earlier_briefs: list[dict[str, Any]] = field(default_factory=list)
    expect: dict[str, Any] = field(default_factory=dict)


def _read(path: Path) -> Any:
    with path.open() as f:
        return yaml.load(f, Loader=_Loader)  # noqa: S506 (a SafeLoader subclass)


def load_cases(folder: Path) -> list[Case]:
    """Every case in `folder`, sorted by name. Raises ValueError naming the file of a bad case."""
    household_file = folder / "household.yaml"
    household = _read(household_file) if household_file.exists() else None
    cases = []
    for path in sorted(folder.glob("*.yaml")):
        if path.name == "household.yaml":
            continue
        try:
            raw = CaseFile.model_validate(_read(path))
            if (raw.household or household) is None:
                raise ValueError("no household: add household.yaml to the folder or `household:` here")
            cfg = Config.model_validate(raw.household or household)
            cases.append(_case(path.stem, raw, cfg))
        except (ValidationError, ValueError, yaml.YAMLError) as e:
            raise ValueError(f"{path}: {e}") from e
    return cases


def _case(name: str, raw: CaseFile, cfg: Config) -> Case:
    tz = ZoneInfo(cfg.timezone)
    aware = lambda dt: dt if dt.tzinfo else dt.replace(tzinfo=tz)  # noqa: E731
    messages = [Message(
        source=m.source, external_id=m.id, timestamp=aware(m.at).astimezone(ZoneInfo("UTC")),
        sender=m.sender, recipient=m.recipient, subject=m.subject, body=m.body, chat_name=m.chat,
        kid_hint=m.kid_hint or (cfg.whatsapp.kid_for(m.chat) if m.source == "whatsapp" and m.chat else None),
    ) for m in raw.messages]
    return Case(name=name, covers=raw.covers, now=aware(raw.now), household=cfg, messages=messages,
                calendar=raw.calendar, queued=raw.queued, earlier_briefs=raw.earlier_briefs,
                expect=raw.expect.model_dump(mode="json", exclude_none=True))
