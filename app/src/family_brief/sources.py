"""The Sources, one record each, in the order the evening run reads them (ADR 0022). Code that
would name the Sources asks this registry instead: what a Source is called, whether a Household's
config connects it, whether the setup page lets a family skip it, and the mail it sends of its own
news. A new Source is its collector module, its config model and its record here."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Config


@dataclass(frozen=True)
class Source:
    name: str                           # in the config, the state, Message.source and the page's steps
    labels: Mapping[str, str]           # what each reviewed language (brief_text.TEXT) calls it
    connected: Callable[[Config], bool]  # whether the Household's config has it read
    skippable: bool                     # whether the setup page lets a family skip it, to add later
    connect_rank: int                   # its place on the setup page's Connect list, first lowest
    macos_only: bool = False            # read only on macOS (system/macos_only.py, ADR 0020)
    sender_domain: str | None = None    # the domain of the mail it sends of its own news (#361)

    def label(self, language: str = "en") -> str:
        """What `language` calls this Source; English for a language without reviewed text."""
        return self.labels.get(language) or self.labels["en"]


def _same_everywhere(label: str) -> dict[str, str]:
    """A brand's name, which each language writes as it is."""
    return {"en": label, "fi": label, "zh": label}


# A Gmail with no sender ticked only sends the Brief and is never read: reading it would search
# the whole inbox (#361).
GMAIL = Source("gmail", _same_everywhere("Gmail"),
               lambda cfg: bool(cfg.gmail.allowlist_domains or cfg.gmail.allowlist_senders),
               skippable=False, connect_rank=1)
MYCLUB = Source("myclub", _same_everywhere("MyClub"),
                lambda cfg: any(k.myclub_ical_url for k in cfg.kids),
                skippable=True, connect_rank=4, sender_domain="myclub.fi")
# Skippable for a school that doesn't use it.
WILMA = Source("wilma", _same_everywhere("Wilma"), lambda cfg: cfg.wilma.enabled,
               skippable=True, connect_rank=0, sender_domain="inschool.fi")
# WhatsApp for Mac's own database (ADR 0012).
WHATSAPP = Source("whatsapp", _same_everywhere("WhatsApp"),
                  lambda cfg: cfg.whatsapp.enabled and bool(cfg.whatsapp.chats),
                  skippable=True, connect_rank=3, macos_only=True)

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
