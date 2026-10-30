"""The sign-up service's own store (ADR 0031): the Invite Links the maintainers mint, and the
Hosted Households made from them, in an owner-only folder of the service's user.

An Invite Link is a random code with what the maintainers prefill: the Recipients and their
languages, and the page's language. It works until it expires, a set number of days after it was
minted, or until a Household is created from it, which retires it: its file goes, and with it the
Recipients' addresses. A link that fails, such as for a wrong Wilma password, stays good.

The Households' list holds only what the service needs to place the next one's evening jobs: each
Household's user and the time its Brief comes, never the family's addresses or Kids.
"""
from __future__ import annotations

import json
import os
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ..brief_text import Language

DAYS = 14  # how long a link works unless the maintainers give another number of days
CODE = re.compile(r"[A-Za-z0-9_-]{32}")  # token_urlsafe(24)'s
LANGUAGES: tuple[Language, ...] = ("fi", "en", "zh")  # the page's, which are the reviewed ones


@dataclass(frozen=True)
class Recipient:
    address: str
    language: Language


@dataclass(frozen=True)
class Invite:
    code: str
    expires: datetime
    recipients: list[Recipient] = field(default_factory=list)
    language: Language = "fi"  # the page's until the family picks another

    def to_json(self) -> dict[str, Any]:
        return {"expires": self.expires.isoformat(), "language": self.language,
                "recipients": [{"address": r.address, "language": r.language}
                               for r in self.recipients]}


@dataclass(frozen=True)
class HouseholdRecord:
    user: str
    evening: str  # HH:MM, when its Brief comes
    created: str


class Store:
    def __init__(self, folder: Path) -> None:
        self.folder = folder

    @property
    def _invites(self) -> Path:
        return self.folder / "invites"

    @property
    def _households(self) -> Path:
        return self.folder / "households.json"

    def mint(self, recipients: list[Recipient], language: Language, now: datetime,
             days: int = DAYS) -> Invite:
        """A new Invite Link's, written to the store, good for `days` from `now`."""
        invite = Invite(secrets.token_urlsafe(24), now + timedelta(days=days), recipients, language)
        self._write(self._invites / f"{invite.code}.json", json.dumps(invite.to_json(), indent=2))
        return invite

    def valid(self, code: str, now: datetime) -> Invite | None:
        """The Invite Link `code`, unless it isn't one, has expired or has been retired."""
        if not CODE.fullmatch(code):
            return None
        try:
            raw = json.loads((self._invites / f"{code}.json").read_text(encoding="utf-8"))
            invite = Invite(code, datetime.fromisoformat(raw["expires"]),
                            [Recipient(r["address"], r["language"]) for r in raw["recipients"]],
                            raw.get("language") or "fi")
        except (OSError, ValueError, KeyError, TypeError):
            return None
        return invite if now < invite.expires else None

    def retire(self, code: str) -> None:
        if CODE.fullmatch(code):
            (self._invites / f"{code}.json").unlink(missing_ok=True)

    def households(self) -> list[HouseholdRecord]:
        try:
            raw = json.loads(self._households.read_text(encoding="utf-8"))
            return [HouseholdRecord(h["user"], h["evening"], h["created"]) for h in raw]
        except (OSError, ValueError, KeyError, TypeError):
            return []

    def add_household(self, household: HouseholdRecord) -> None:
        listed = [*self.households(), household]
        self._write(self._households, json.dumps([h.__dict__ for h in listed], indent=2))

    def remove_household(self, user: str) -> bool:
        """Takes `user` off the list, and says whether it was on it."""
        listed = self.households()
        kept = [h for h in listed if h.user != user]
        if len(kept) == len(listed):
            return False
        self._write(self._households, json.dumps([h.__dict__ for h in kept], indent=2))
        return True

    def _write(self, path: Path, text: str) -> None:
        """Owner-only, whatever the umask, in one step, so a reader never sees half of it."""
        for folder in dict.fromkeys((self.folder, path.parent)):
            folder.mkdir(mode=0o700, parents=True, exist_ok=True)
            folder.chmod(0o700)
        _write(path, text)


def _write(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        os.fchmod(f.fileno(), 0o600)
        f.write(text + "\n")
    tmp.replace(path)
