"""The Sources, one record each, in the order the evening run reads them (ADR 0022). Code that
would name the Sources asks this registry instead: what a Source is called, whether a Household's
config connects it, how the evening run reads it and widens its lookback, what the Brief's coverage
line counts of it, which systems can read it, whether the setup page has a step for it and lets a
family skip it, the mail it sends of its own news, which of its messages went to everyone, which it
gives only as a stub and how the Brief lists one held back (ADR 0013), how the calendar treats its
events, what `doctor` checks of it, why one the Household set up can't be read tonight, whether a
Hosted Household can have it, and what uninstall removes for it.

A Source in a folder of its own, `sources/<group>/<name>` (ADR 0051), keeps its record there, and
this list is one of the few places shared code may name it (docs/sources.md, "Adding a Source").
The other Sources' records are still here, their code in `collectors` and the shared modules."""
from __future__ import annotations

from typing import TYPE_CHECKING

from .fi.eepos.record import EEPOS
from .fi.evaka.record import EVAKA
from .fi.myclub.record import MYCLUB
from .nl.konnect.record import KONNECT
from .nl.socialschools.record import SOCIALSCHOOLS
from .record import (EVERY_SYSTEM, HOSTED_SYSTEM, NEEDS_PAIRING, Add, Source, Unread, Window,
                     _collector, _in, _same_everywhere)

if TYPE_CHECKING:
    from ..config import Config
    from ..state import State

__all__ = ["EVERY_SYSTEM", "HOSTED_SYSTEM", "NEEDS_PAIRING", "Add", "Source", "Unread", "Window"]

# A Gmail with no sender ticked is never read: reading it would search the whole inbox (#361).
# It never sends the Brief either (ADR 0032), so a Household can skip it. Last on Connect's list:
# few families read it, and its App Password is where setup gets stuck most (#708). The hosted
# sign-up has no Gmail step yet (ADR 0029, 0031).
GMAIL = Source("gmail", _same_everywhere("Gmail"),
               lambda cfg: bool(cfg.gmail.allowlist_domains or cfg.gmail.allowlist_senders),
               skippable=True, connect_rank=8, collect=_collector("gmail"), hosted=False,
               lookback=lambda cfg: cfg.gmail, check=_in("ops", "check_gmail"),
               secrets=_in("uninstall", "gmail_secrets"), notes=_in("uninstall", "gmail_notes"),
               sent_to_everyone=_in("collectors.gmail", "sent_to_everyone"),
               checked_body=_in("collectors.gmail", "checked_body"), held_back_link=lambda m: m.url)
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

# Messages from plain files the family puts in a folder it picks, for news from any place Parent
# Recap doesn't read itself (ADR 0044). Turned on, and its folder chosen, with the manage skill
# (`setup save`), never on the setup page. Only read on the family's own computer: a Hosted
# Household never has it. A message in a chat has no subject: the Brief lists it by its chat.
LOCAL_FILES = Source("local_files", {"en": "Local Files", "fi": "Paikalliset tiedostot", "zh": "本地文件"},
                     _in("collectors.local_files", "connected"), skippable=True, connect_rank=9,
                     collect=_collector("local_files"), systems=frozenset({"macos", "windows"}),
                     hosted=False, setup_page=False, lookback=lambda cfg: cfg.local_files,
                     check=_in("ops", "check_local_files"), notes=_in("uninstall", "local_files_notes"),
                     account=False, group=lambda m: m.chat_name)

ALL: tuple[Source, ...] = (GMAIL, MYCLUB, WILMA, WHATSAPP, EEPOS, EVAKA, SOCIALSCHOOLS, KONNECT, LOCAL_FILES)


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


def not_hosted() -> list[Source]:
    """The Sources a Hosted Household can't have: those the hosted version's system can't read and
    those neither its sign-up nor the maintainers set up (ADR 0029, 0031), of those the setup page
    offers, in the order its Connect list shows them. One the setup page doesn't offer, such as
    Local Files, isn't one a family expects to have, so the hosted Brief doesn't name it."""
    return sorted((s for s in ALL if s.setup_page and (not s.hosted or HOSTED_SYSTEM not in s.systems)),
                  key=lambda s: s.connect_rank)


def in_connect_order() -> list[Source]:
    """The Sources this system can read, in the order the setup page's Connect list shows them,
    with those it has no step for at their place too, as `doctor` checks them."""
    return sorted(available(), key=lambda s: s.connect_rank)


def unread(cfg: Config, state: State) -> dict[str, Unread]:
    """The Sources the Household set up that aren't read tonight, by name, with why."""
    return {s.name: why for s in available() if (why := s.unread(cfg, state)) is not None}


def used(cfg: Config) -> list[Source]:
    """The Sources the Household's evening run reads, in run order. With nothing connected that's
    Gmail, the first one the run reads, so the run says it could read nothing."""
    return [s for s in available() if s.connected(cfg)] or [GMAIL]
