"""The Sources, one record each, in the order the evening run reads them (ADR 0022). Code that
would name the Sources asks this registry instead: what a Source is called, whether a Household's
config connects it, how the evening run reads it and widens its lookback, what the Brief's coverage
line counts of it, whether the setup page lets a family skip it, and the mail it sends of its own
news. A new Source is its collector module, its config model and its record here."""
from __future__ import annotations

import importlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from .collectors.base import Collected
    from .config import Config
    from .state import State


class Window(Protocol):
    """A Source's config section, whose lookback the run widens after nights it missed."""
    lookback_hours: int


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
    lookback: Callable[[Config], Window] | None = None  # its section with lookback_hours, if any
    permission_text: str = "permission_denied"  # the BriefText line for a permission it lacks
    macos_only: bool = False            # read only on macOS (system/macos_only.py, ADR 0020)
    sender_domain: str | None = None    # the domain of the mail it sends of its own news (#361)

    def label(self, language: str = "en") -> str:
        """What `language` calls this Source; English for a language without reviewed text."""
        return self.labels.get(language) or self.labels["en"]


def _collector(module: str) -> Callable[[Config, State, list[str]], Collected]:
    """The `collect` of collectors/`module`, imported when called: a collector imports the config,
    which asks this registry."""
    def collect(cfg: Config, state: State, kid_terms: list[str]) -> Collected:
        return importlib.import_module(f"{__package__}.collectors.{module}").collect(cfg, state, kid_terms)
    return collect


def _same_everywhere(label: str) -> dict[str, str]:
    """A brand's name, which each language writes as it is."""
    return {"en": label, "fi": label, "zh": label}


# A Gmail with no sender ticked only sends the Brief and is never read: reading it would search
# the whole inbox (#361).
GMAIL = Source("gmail", _same_everywhere("Gmail"),
               lambda cfg: bool(cfg.gmail.allowlist_domains or cfg.gmail.allowlist_senders),
               skippable=False, connect_rank=1, collect=_collector("gmail"),
               lookback=lambda cfg: cfg.gmail)
MYCLUB = Source("myclub", _same_everywhere("MyClub"),
                lambda cfg: any(k.myclub_ical_url for k in cfg.kids),
                skippable=True, connect_rank=4, collect=_collector("myclub"), counts_events=True,
                sender_domain="myclub.fi")
# Skippable for a school that doesn't use it.
WILMA = Source("wilma", _same_everywhere("Wilma"), lambda cfg: cfg.wilma.enabled,
               skippable=True, connect_rank=0, collect=_collector("wilma"), lookback=lambda cfg: cfg.wilma,
               sender_domain="inschool.fi")
# WhatsApp for Mac's own database (ADR 0012). Its permission is lost when Parent Recap's own Python
# changes (ADR 0011), which its permission line says.
WHATSAPP = Source("whatsapp", _same_everywhere("WhatsApp"),
                  lambda cfg: cfg.whatsapp.enabled and bool(cfg.whatsapp.chats),
                  skippable=True, connect_rank=3, collect=_collector("whatsapp"),
                  lookback=lambda cfg: cfg.whatsapp, permission_text="whatsapp_permission_denied",
                  macos_only=True)

ALL: tuple[Source, ...] = (GMAIL, MYCLUB, WILMA, WHATSAPP)


def named(name: str) -> Source | None:
    """The Source called `name`, or None for anything else."""
    return next((s for s in ALL if s.name == name), None)


def label_of(name: str, language: str = "en") -> str:
    """What `language` calls the Source `name`, or `name` itself when no Source has it."""
    source = named(name)
    return source.label(language) if source else name


def in_connect_order() -> list[Source]:
    """The Sources in the order the setup page's Connect list shows them."""
    return sorted(ALL, key=lambda s: s.connect_rank)


def used(cfg: Config) -> list[Source]:
    """The Sources the Household's evening run reads, in run order. With nothing connected that's
    Gmail, which sends the Brief and so is always set up."""
    return [s for s in ALL if s.connected(cfg)] or [GMAIL]
