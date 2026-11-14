"""What a Source is to the program (ADR 0022): the record each Source has in the registry
(`family_brief.sources`), with the helpers records are written with. Only the shared types live
here, so a Source's own folder can import them while the registry imports the Source's record."""
from __future__ import annotations

import importlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol

from ..system import profile

if TYPE_CHECKING:
    from pathlib import Path
    from types import ModuleType

    from ..collectors.base import Collected, Message
    from ..config import Config
    from ..state import State
    from ..uninstall import Survey

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
HOSTED_SYSTEM = "linux"  # the system the hosted version's server runs (ADR 0029)


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
    # checks or removes it
    systems: frozenset[str] = EVERY_SYSTEM
    # whether a Hosted Household can have it: its sign-up sets it up (ADR 0031) or, as Eepos for
    # now, the maintainers can; a Hosted Household's Brief says the hosted version doesn't read
    # one it can't have, or one its system can't read
    hosted: bool = True
    # whether the setup page has a step for it; one without is set up from a terminal, by its
    # `setup` command, and the page's Connect list leaves it out
    setup_page: bool = True
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
    # whether it is an account of the family's, which uninstall says it never touches
    account: bool = True
    # ── Held-back Messages (ADR 0013). A Source without these holds a message back when in doubt and
    # lists it by sender and subject, with no link.
    sent_to_everyone: Callable[[Message], bool] = lambda m: False  # so it is never held back
    checked_body: Callable[[Message], str] = lambda m: m.body or ""  # the part of the body checked
    group: Callable[[Message], str | None] | None = None  # listed by in place of a subject, ahead of the sender
    held_back_link: Callable[[Message], str | None] = lambda m: None  # where the Brief links it to
    # a message the Source gave only as a stub, such as eVaka's sensitive one: held back whatever its
    # words, and with the AI filter off too, since a model could only guess at it
    withheld: Callable[[Message], bool] = lambda m: False
    # ── The calendar
    in_ics: Callable[[Config], bool] = lambda cfg: True  # its events go into the .ics attachment
    utc_hash_before_0_4: bool = False   # its event starts were hashed in UTC before 0.4.0
    # ── Where its code is
    # its folder under `sources`, `<group>/<name>` such as nl/konnect, which is the part of a
    # release that is its (ADR 0051); None for a Source whose code is still in the shared modules
    part: str | None = None

    def label(self, language: str = "en") -> str:
        """What `language` calls this Source; English for a language without reviewed text."""
        return self.labels.get(language) or self.labels["en"]

    def available(self) -> bool:
        """Whether it can be read on the system the program runs on."""
        return profile.current().system in self.systems

    def doctor_items(self) -> Mapping[str, str]:
        """The key of each of doctor's checks of it, and the item doctor prints for it."""
        return self.check_items if self.check_items is not None else {self.name: self.label()}

    def code(self, module: str) -> ModuleType:
        """`module` of its folder, such as `collector` or `setup`, for shared code that still has a
        part of this Source in it: the setup page's and the hosted sign-up's steps, until those
        pages are split by Source (ADR 0051). It reaches the folder through the registry, never by
        the folder's name."""
        if self.part is None:
            raise LookupError(f"{self.name} has no folder of its own")
        return importlib.import_module(f"{_PROGRAM}.sources.{self.part.replace('/', '.')}.{module}")


_PROGRAM = __name__.split(".")[0]  # family_brief, which `_in`'s modules are named within


def _in(module: str, function: str) -> Callable[..., Any]:
    """`function` of `module`, named within the program (`ops`, `sources.nl.konnect.collector`),
    imported when called: those modules import the config, which asks this registry."""
    def call(*args: Any) -> Any:
        return getattr(importlib.import_module(f"{_PROGRAM}.{module}"), function)(*args)
    return call


def _collector(module: str) -> Callable[[Config, State, list[str]], Collected]:
    """The `collect` of collectors/`module`."""
    return _in(f"collectors.{module}", "collect")


def _same_everywhere(label: str) -> dict[str, str]:
    """A brand's name, which each language writes as it is."""
    return {"en": label, "fi": label, "zh": label}
