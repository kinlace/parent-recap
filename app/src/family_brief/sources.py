"""The Sources, one record each, in the order the evening run reads them (ADR 0022). Code that
would name the Sources asks this registry instead: what a Source is called, whether a Household's
config connects it, how the evening run reads it and widens its lookback, what the Brief's coverage
line counts of it, which systems can read it, whether the setup page lets a family skip it, the
mail it sends of its own news, which of its messages went to everyone and how the Brief lists one
held back (ADR 0013), how the calendar treats its events, what `doctor` checks of it, why one the
Household set up can't be read tonight, and what uninstall removes for it. A new Source is its
collector module, its config model and its record here."""
from __future__ import annotations

import importlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol

from .system import profile

if TYPE_CHECKING:
    from pathlib import Path

    from .collectors.base import Collected, Message
    from .config import Config
    from .state import State
    from .uninstall import Survey

# doctor's `add`, given a check's status, key and detail (ops.health_checks).
Add = Callable[..., None]


class Window(Protocol):
    """A Source's config section, whose lookback the run widens after nights it missed."""
    lookback_hours: int


def _nothing(*_args: Any) -> list[str]:
    return []


# Why a Linked Device's Source isn't read: WhatsApp ended the link, or it was never paired.
NEEDS_PAIRING = "needs a new pairing"


@dataclass(frozen=True)
class Unread:
    """Why a Source the Household set up isn't read tonight, and since when, if that's known. The
    Brief's coverage line says so as it says a Source failed."""
    reason: str
    since: datetime | None = None


def _never_unread(*_args: Any) -> Unread | None:
    return None


EVERY_SYSTEM = frozenset({"macos", "windows", "linux"})


@dataclass(frozen=True)
class Source:
    """One Source as the program knows it everywhere but its collector and its setup panel."""
    name: str                          # in the config, the state, Message.source and the page's steps
    labels: Mapping[str, str]           # what each reviewed language (brief_text.TEXT) calls it
    connected: Callable[[Config], bool]  # whether the Household's config has it read
    skippable: bool                     # whether the setup page lets a family skip it, to add later
    connect_rank: int                   # its place on the setup page's Connect list, first lowest
    collect: Callable[[Config, State, list[str]], Collected]  # reads it, given the Kids' terms
    counts_events: bool = False         # the coverage line counts its events rather than its Messages
    # the systems it can be read on, by their profile's name; elsewhere nothing offers, reads,
    # checks or removes it, and the Brief's coverage line says the hosted version doesn't read it
    systems: frozenset[str] = EVERY_SYSTEM
    lookback: Callable[[Config], Window] | None = None  # its section with lookback_hours, if any
    # why it isn't read tonight though the Household set it up, given the config and the state
    unread: Callable[[Config, State], Unread | None] = _never_unread
    sender_domain: str | None = None    # the domain of the mail it sends of its own news (#361)
    # doctor's checks of it, given the config, `add` and the config's path; each does nothing for
    # a Source the Household hasn't set up
    check: Callable[[Config, Add, str | None], None] | None = None
    check_items: Mapping[str, str] | None = None  # each check's key and item; its name and label if None
    secrets: Callable[[Config], list[str]] = _nothing  # the secret store's accounts setup made for it
    # anything else uninstall removes for it, such as a sign-in profile or its own program, given
    # where Parent Recap's program is
    survey: Callable[[Survey, list[Path]], None] | None = None
    # what uninstall leaves the family to do for it
    notes: Callable[[Config], list[str]] = _nothing
    # ── Held-back Messages (ADR 0013). A Source without these holds a message back when in doubt and
    # lists it by sender and subject, with no link.
    sent_to_everyone: Callable[[Message], bool] = lambda m: False  # so it is never held back
    checked_body: Callable[[Message], str] = lambda m: m.body or ""  # the part of the body checked
    group: Callable[[Message], str | None] | None = None  # listed by in place of a subject, ahead of the sender
    held_back_link: Callable[[Message], str | None] = lambda m: None  # where the Brief links it to
    # ── The calendar
    in_ics: Callable[[Config], bool] = lambda cfg: True  # its events go into the .ics attachment
    utc_hash_before_0_4: bool = False   # its event starts were hashed in UTC before 0.4.0

    def label(self, language: str = "en") -> str:
        """What `language` calls this Source; English for a language without reviewed text."""
        return self.labels.get(language) or self.labels["en"]

    def available(self) -> bool:
        """Whether it can be read on the system the program runs on."""
        return profile.current().system in self.systems

    def doctor_items(self) -> Mapping[str, str]:
        """The key of each of doctor's checks of it, and the item doctor prints for it."""
        return self.check_items if self.check_items is not None else {self.name: self.label()}


def _in(module: str, function: str) -> Callable[..., Any]:
    """`function` of `module`, imported when called: those modules import the config, which asks
    this registry."""
    def call(*args: Any) -> Any:
        return getattr(importlib.import_module(f"{__package__}.{module}"), function)(*args)
    return call


def _collector(module: str) -> Callable[[Config, State, list[str]], Collected]:
    """The `collect` of collectors/`module`."""
    return _in(f"collectors.{module}", "collect")


def _same_everywhere(label: str) -> dict[str, str]:
    """A brand's name, which each language writes as it is."""
    return {"en": label, "fi": label, "zh": label}


# A Gmail with no sender ticked is never read: reading it would search the whole inbox (#361).
# It never sends the Brief either (ADR 0032), so a Household can skip it.
GMAIL = Source("gmail", _same_everywhere("Gmail"),
               lambda cfg: bool(cfg.gmail.allowlist_domains or cfg.gmail.allowlist_senders),
               skippable=True, connect_rank=1, collect=_collector("gmail"),
               lookback=lambda cfg: cfg.gmail, check=_in("ops", "check_gmail"),
               secrets=_in("uninstall", "gmail_secrets"), notes=_in("uninstall", "gmail_notes"),
               sent_to_everyone=_in("collectors.gmail", "sent_to_everyone"),
               checked_body=_in("collectors.gmail", "checked_body"), held_back_link=lambda m: m.url)
# Checked once for each Kid with a subscription link. Its feed is usually subscribed to in the
# calendar app directly, so its events go into the .ics only when asked.
MYCLUB = Source("myclub", _same_everywhere("MyClub"),
                lambda cfg: any(k.myclub_ical_url for k in cfg.kids),
                skippable=True, connect_rank=4, collect=_collector("myclub"), counts_events=True,
                sender_domain="myclub.fi", check=_in("ops", "check_myclub"),
                check_items={"myclub": "MyClub ({})"},
                in_ics=lambda cfg: cfg.google_calendar.ics_include_myclub, utc_hash_before_0_4=True)
# Skippable for a school that doesn't use it.
WILMA = Source("wilma", _same_everywhere("Wilma"), lambda cfg: cfg.wilma.enabled,
               skippable=True, connect_rank=0, collect=_collector("wilma"), lookback=lambda cfg: cfg.wilma,
               sender_domain="inschool.fi", check=_in("ops", "check_wilma"),
               survey=_in("uninstall", "survey_wilma"), notes=_in("uninstall", "wilma_notes"),
               sent_to_everyone=_in("collectors.wilma", "sent_to_everyone"),
               held_back_link=_in("collectors.wilma", "link"))
# Read as a Linked Device (ADR 0027): connected once it is paired and has groups picked. Picked
# groups without a pairing, or a link WhatsApp ended, need a new pairing. Setup pairs it with a
# QR code on a family's own computer (setup_server/whatsapp.py); the hosted version, on Linux,
# never holds a Linked Device (ADR 0029). A post in a group, where incidents get
# discussed, is always checked, and has no subject: the Brief lists it by its group.
WHATSAPP = Source("whatsapp", _same_everywhere("WhatsApp"), _in("collectors.whatsapp", "connected"),
                  skippable=True, connect_rank=3, collect=_collector("whatsapp"),
                  systems=frozenset({"macos", "windows"}), lookback=lambda cfg: cfg.whatsapp,
                  unread=_in("collectors.whatsapp", "unread"),
                  check=_in("ops", "check_whatsapp"), survey=_in("uninstall", "survey_whatsapp"),
                  notes=_in("uninstall", "whatsapp_notes"), group=lambda m: m.chat_name)

ALL: tuple[Source, ...] = (GMAIL, MYCLUB, WILMA, WHATSAPP)


def named(name: str) -> Source | None:
    """The Source called `name`, or None for anything else."""
    return next((s for s in ALL if s.name == name), None)


def label_of(name: str, language: str = "en") -> str:
    """What `language` calls the Source `name`, or `name` itself when no Source has it."""
    source = named(name)
    return source.label(language) if source else name


def available() -> list[Source]:
    """The Sources that can be read on the system the program runs on, in run order."""
    return [s for s in ALL if s.available()]


def unavailable() -> list[Source]:
    """The Sources the system the program runs on can't read, which the hosted version leaves out
    (ADR 0029), in run order."""
    return [s for s in ALL if not s.available()]


def in_connect_order() -> list[Source]:
    """The Sources this system can read, in the order the setup page's Connect list shows them."""
    return sorted(available(), key=lambda s: s.connect_rank)


def unread(cfg: Config, state: State) -> dict[str, Unread]:
    """The Sources the Household set up that aren't read tonight, by name, with why."""
    return {s.name: why for s in available() if (why := s.unread(cfg, state)) is not None}


def used(cfg: Config) -> list[Source]:
    """The Sources the Household's evening run reads, in run order. With nothing connected that's
    Gmail, the first Source setup asks about, so the run says it could read nothing."""
    return [s for s in available() if s.connected(cfg)] or [GMAIL]
