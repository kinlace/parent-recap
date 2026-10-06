from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from .brief_text import Language


def _expand(p: str | None) -> str | None:
    if p is None:
        return None
    return os.path.expanduser(os.path.expandvars(p))


class Kid(BaseModel):
    name: str                           # ideally as Wilma spells it
    everyday_name: str | None = None    # what the Brief calls the Kid everywhere, or else the name
    aliases: list[str] = Field(default_factory=list)
    grade: int | None = None
    class_name: str | None = None
    school: str | None = None
    activities: list[str] = Field(default_factory=list)
    wilma_tenant: str | None = None
    wilma_username: str | None = None
    myclub_ical_url: str | None = None

    def called(self) -> str:
        """The name the Brief calls this Kid by everywhere."""
        return self.everyday_name or self.name

    def match_terms(self) -> list[str]:
        return [self.name, *([self.everyday_name] if self.everyday_name else []), *self.aliases]

    def chat_hint_terms(self) -> list[str]:
        """What a WhatsApp chat about this Kid may be named after: the names, the first name of
        a full name as Wilma spells it, the class, the school and the clubs."""
        first = self.name.split()[0] if len(self.name.split()) > 1 else None
        terms = [*self.match_terms(), first, self.class_name, self.school, *self.activities]
        return [t for t in terms if t]


class GmailConfig(BaseModel):
    """Gmail via IMAP + App Password (stored in Keychain).

    Only senders on the allowlist are pulled in. Keyword-based catchall is
    disabled by default because it's too noisy and risks vacuuming up private
    self-notes (e.g. password lists mailed to self).
    """
    username: str | None = None
    lookback_hours: int = 26
    allowlist_domains: list[str] = Field(default_factory=list)
    allowlist_senders: list[str] = Field(default_factory=list)
    # Optional extra keywords — only applied *on top of* allowlist match (never as catchall).
    extra_keywords: list[str] = Field(default_factory=list)


class MyClubConfig(BaseModel):
    # Case-insensitive substrings; matching MyClub events are dropped (club-wide events unrelated to your kid).
    blocklist: list[str] = Field(default_factory=list)


class WilmaConfig(BaseModel):
    enabled: bool = False
    lookback_hours: int = 26  # older messages are marked seen without being summarized


class WhatsAppChat(BaseModel):
    name: str
    kid: str | None = None          # a kid's short name, "both", or None
    label: str | None = None        # short topical label e.g. "class", "football", "piano"


class WhatsAppConfig(BaseModel):
    enabled: bool = False
    chats: list[WhatsAppChat] = Field(default_factory=list)
    lookback_hours: int = 26

    def chat_names(self) -> list[str]:
        return [c.name for c in self.chats]

    def kid_for(self, name: str) -> str | None:
        for c in self.chats:
            if c.name == name:
                return c.kid
        return None


class CalendarConfig(BaseModel):
    # ics: new events ride along in the daily email as a .ics attachment (no OAuth needed)
    # google: written straight into Google Calendar via OAuth, with dedup + auto-invites
    mode: Literal["ics", "google", "off"] = "ics"
    ics_include_myclub: bool = False  # usually subscribe to the MyClub feed in the calendar app instead
    calendar_id: str = "primary"
    lookahead_days_context: int = 7
    invite_attendees: list[str] = Field(default_factory=list)   # emails to add to every event
    send_updates: str = "all"    # "all" | "externalOnly" | "none"


class IMessageConfig(BaseModel):
    enabled: bool = False
    recipients: list[str] = Field(default_factory=list)


class Recipient(BaseModel):
    """Someone who receives the Brief or Weekend Picks. A plain address in the config is a
    Recipient without a language of their own, who reads the Household's summary_language."""
    address: str
    language: Language | None = None   # any language: en, zh and fi are reviewed, others best effort

    @model_validator(mode="before")
    @classmethod
    def _plain_address(cls, v: object) -> object:
        return {"address": v} if isinstance(v, str) else v


class EmailConfig(BaseModel):
    """Email delivery via Gmail SMTP + App Password (same Keychain entry as inbound IMAP)."""
    enabled: bool = True
    from_addr: str | None = None                # falls back to gmail.username
    to: list[Recipient] = Field(default_factory=list)          # Brief Recipients
    weekend_to: list[Recipient] = Field(default_factory=list)  # Weekend Picks Recipients


class LLMConfig(BaseModel):
    # claude: `claude -p`, signed in with a Claude Pro/Max subscription
    # codex:  `codex exec`, signed in with a ChatGPT account
    backend: Literal["claude", "codex"] = "claude"
    model: str | None = None
    timeout_seconds: int = 300
    # Default: the codex bundled in Codex.app / ChatGPT.app, then PATH, the usual folders and the
    # login shell's PATH.
    codex_path: str | None = None

    @field_validator("backend", mode="before")
    @classmethod
    def _legacy_backend(cls, v: str) -> str:
        return "claude" if v == "cli" else v  # configs written before 0.3.0 say "cli"


class ArchiveConfig(BaseModel):
    dir: str = "~/ParentRecap"

    def resolved_dir(self) -> Path:
        return Path(_expand(self.dir))


class WeekendEventsConfig(BaseModel):
    """Friday-afternoon weekend-event scan for the coming Sat/Sun."""
    enabled: bool = False
    recipients: list[str] = Field(default_factory=list)  # if empty, falls back to imessage.recipients
    max_price_eur: float = 20.0
    regions: list[str] = Field(default_factory=lambda: ["Espoo", "Helsinki", "Vantaa"])
    max_candidates: int = 12
    kid_preferences: str = ""     # free-form; passed to LLM as context
    parent_preferences: str = ""
    dir: str = "~/ParentRecap/weekend_events"

    def resolved_dir(self) -> Path:
        return Path(_expand(self.dir))


class FeedbackFields(BaseModel):
    """The pilot Form's pre-fill ids (`entry.N`), as printed by ops/feedback-form."""
    verdict: str
    item_text: str
    source: str
    backend: str
    date: str
    household: str
    kid: str


class FeedbackConfig(BaseModel):
    """Pilot feedback: ⭐/❌ links in the HTML Brief that open the shared, pre-filled Google Form."""
    enabled: bool = False
    prefill_base_url: str = ""
    household_label: str = ""   # tells this Household's rows apart in the shared response Sheet
    fields: FeedbackFields | None = None

    def active(self) -> bool:
        return self.enabled and bool(self.prefill_base_url) and self.fields is not None


class ScheduleConfig(BaseModel):
    daily_hour: int = 21
    daily_minute: int = 0
    weekend_weekday: int = 5   # launchd Weekday: 0/7=Sun, 5=Fri
    weekend_hour: int = 16


class Config(BaseModel):
    timezone: str = "Europe/Helsinki"
    summary_language: Language = "en"   # the language of Recipients who haven't picked one
    city: str | None = None             # the Household's town, in Finnish, as in Wilma's list
    kids: list[Kid]
    gmail: GmailConfig = GmailConfig()
    wilma: WilmaConfig = WilmaConfig()
    myclub: MyClubConfig = MyClubConfig()
    whatsapp: WhatsAppConfig = WhatsAppConfig()
    google_calendar: CalendarConfig = CalendarConfig()
    imessage: IMessageConfig = IMessageConfig()
    email: EmailConfig = EmailConfig()
    llm: LLMConfig = LLMConfig()
    archive: ArchiveConfig = ArchiveConfig()
    weekend_events: WeekendEventsConfig = WeekendEventsConfig()
    feedback: FeedbackConfig = FeedbackConfig()
    schedule: ScheduleConfig = ScheduleConfig()
    state_path: str = "~/.family/state.json"

    def resolved_state_path(self) -> Path:
        return Path(_expand(self.state_path))

    def language_of(self, recipient: Recipient) -> Language:
        return recipient.language or self.summary_language

    def _by_language(self, recipients: list[Recipient]) -> dict[Language, list[str]]:
        groups: dict[Language, list[str]] = {}
        for r in recipients:
            groups.setdefault(self.language_of(r), []).append(r.address)
        return groups

    def _first_language(self, recipients: list[Recipient]) -> Language:
        return self.language_of(recipients[0]) if recipients else self.summary_language

    def brief_language(self) -> Language:
        """The language the model writes the Brief in: the first Recipient's (ADR 0004)."""
        return self._first_language(self.email.to)

    def brief_languages(self) -> list[Language]:
        """Every language tonight's Brief is in: the original's first."""
        return [*self.brief_recipients_by_language()] or [self.brief_language()]

    def brief_recipients_by_language(self) -> dict[Language, list[str]]:
        """The Brief's addresses grouped by the language they read, the first Recipient's first."""
        return self._by_language(self.email.to)

    def weekend_recipients(self) -> list[Recipient]:
        """Weekend Picks go to email.weekend_to, or to the Brief's Recipients when it is empty."""
        return self.email.weekend_to or self.email.to

    def weekend_language(self) -> Language:
        """The language the model writes Weekend Picks in: their first Recipient's."""
        return self._first_language(self.weekend_recipients())

    def weekend_languages(self) -> list[Language]:
        """Every language this week's Weekend Picks are in: the original's first."""
        return [*self.weekend_recipients_by_language()] or [self.weekend_language()]

    def weekend_recipients_by_language(self) -> dict[Language, list[str]]:
        """Weekend Picks' addresses grouped by the language they read, the first Recipient's first."""
        return self._by_language(self.weekend_recipients())

    def kid_called(self, name: str | None) -> str | None:
        """What the Brief calls the Kid known by `name`, any of their names in any case. Anything
        else, such as the Household or no Kid, is left as it is."""
        wanted = (name or "").strip().casefold()
        for k in self.kids:
            if wanted in (term.casefold() for term in k.match_terms()):
                return k.called()
        return name

    def default_sources(self) -> list[str]:
        sources = ["gmail"]
        if any(k.myclub_ical_url for k in self.kids):
            sources.append("myclub")
        if self.wilma.enabled:
            sources.append("wilma")
        if self.whatsapp.enabled and self.whatsapp.chats:
            sources.append("whatsapp")
        return sources

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        path = Path(_expand(str(path))) if path else Path(_expand("~/.family/config.yaml"))
        if not path.exists():
            raise FileNotFoundError(
                f"Config not found at {path}. Copy config.example.yaml and fill it in."
            )
        with path.open() as f:
            data = yaml.safe_load(f)
        return cls.model_validate(data)
