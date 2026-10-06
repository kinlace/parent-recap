"""setup save — write the Household's setup answers into the config, and keep setup's progress.

Both the setup page and the chat setup save through this command, so they write the same config
in the same way and can hand over to each other at the step reached (ADR 0006). It reads the
answers as JSON on stdin, every key optional:

  {"language": "zh",
   "city": "Espoo",
   "kids": [{"name": "Mia Virtanen", "everyday_name": "Mia", "aliases": ["米娅"]}],
   "recipients": [{"address": "parent@gmail.com"}, {"address": "partner@gmail.com", "language": "fi"}],
   "ai": "claude",
   "evening": "21:00",
   "sources": {"gmail": {"address": "parent@gmail.com", "allowlist_domains": ["espoo.fi"]},
               "wilma": {"enabled": true},
               "whatsapp": {"enabled": true, "chats": [{"name": "3B", "kid": "Mia Virtanen", "label": "class"}]}},
   "feedback": {"enabled": true, "household_label": "Virtanen family"},
   "progress": {"phase": "connect", "source": "gmail", "sources": {"wilma": "done"},
                "partner": {"address": "partner@gmail.com", "language": "fi"},
                "whatsapp_chats": [{"name": "3B", "last": "2026-09-25", "archived": false,
                                    "hint": {"kids": ["Mia Virtanen"], "matched": ["3B"]}}],
                "gmail_senders": [{"domain": "edu.espoo.fi", "count": 5, "example": "Opettaja",
                                   "likely": true}]}}

The progress's `partner` is Welcome's choice, or null for only me, kept until the setup parent's
own address is known and the Recipients can be saved with the parent first. Its `whatsapp_chats`
are the groups `setup whatsapp` found, and its `gmail_senders` the senders `discover
gmail-senders --json` found, each as it reports them, kept for the check page.

What the answers don't mention stays as it was. The Kids given are the Household's Kids: a Kid
left out is removed, and each one given keeps what the answers don't say about them, such as a
MyClub link, which only `setup myclub` and the setup page's MyClub field save. Turning pilot
feedback on writes the pilot Form shipped with the program (feedback.pilot_form), and is refused
in a version that ships none; until the answers give a `household_label`, it's the setup parent's
email user. The merged config is checked against the config model before it's written. `--read` saves nothing and only reads the progress back.

It prints one line of JSON, with the progress in it. Invalid answers are refused with the path
of each problem; the values aren't repeated, since a config can hold links that are secrets.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Literal, get_args

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from . import feedback
from .brief_text import Language
from .config import Config, WhatsAppChat

Phase = Literal["welcome", "connect", "working", "check", "first-brief", "finish"]
SourceName = Literal["wilma", "gmail", "ai", "whatsapp", "myclub"]  # in the Source list's order
Status = Literal["to-do", "done", "skipped"]
SOURCES: tuple[str, ...] = get_args(SourceName)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class KidAnswer(_Strict):
    name: str                       # as Wilma spells it, or as the parent typed it
    everyday_name: str | None = None
    aliases: list[str] | None = None
    school: str | None = None
    class_name: str | None = None
    grade: int | None = None


class RecipientAnswer(_Strict):
    address: str
    language: Language | None = None


class GmailAnswer(_Strict):
    address: str | None = None
    allowlist_domains: list[str] | None = None
    allowlist_senders: list[str] | None = None


class WilmaAnswer(_Strict):
    enabled: bool


class WhatsAppAnswer(_Strict):
    enabled: bool | None = None
    chats: list[WhatsAppChat] | None = None


class SourcesAnswer(_Strict):
    gmail: GmailAnswer | None = None
    wilma: WilmaAnswer | None = None
    whatsapp: WhatsAppAnswer | None = None


class FeedbackAnswer(_Strict):
    """Pilot feedback: whether the Household opts in, and the label that tells its rows apart in
    the team's Sheet. The Form itself is the one shipped with the program."""
    enabled: bool | None = None
    household_label: str | None = None


class ChatHint(_Strict):
    kids: list[str]
    matched: list[str]


class FoundChat(_Strict):
    """A WhatsApp group `setup whatsapp` found, as it reports it."""
    name: str
    last: str | None = None
    archived: bool = False
    hint: ChatHint | None = None


class FoundSender(_Strict):
    """A Gmail sender domain `discover gmail-senders --json` found, as it reports it."""
    domain: str
    count: int
    example: str
    likely: bool
    public: bool | None = None


class ProgressAnswer(_Strict):
    phase: Phase | None = None
    source: SourceName | None = None
    sources: dict[SourceName, Status] = Field(default_factory=dict)
    # Welcome's partner choice: null for only me. It waits here until the setup parent's own
    # address is known, since the partner mustn't be the first Recipient.
    partner: RecipientAnswer | None = None
    # The WhatsApp groups found once WhatsApp could be read, for the check page to pick from.
    whatsapp_chats: list[FoundChat] | None = None
    # The Gmail senders found in Working, for the check page to pick from.
    gmail_senders: list[FoundSender] | None = None


class Answers(_Strict):
    language: Language | None = None
    city: str | None = None         # the Household's town, in Finnish, as in Wilma's list
    kids: list[KidAnswer] | None = None
    recipients: list[RecipientAnswer] | None = None
    ai: Literal["claude", "codex"] | None = None
    evening: str | None = None      # HH:MM, when the Brief comes
    sources: SourcesAnswer | None = None
    feedback: FeedbackAnswer | None = None
    progress: ProgressAnswer | None = None

    @field_validator("evening")
    @classmethod
    def _time(cls, v: str | None) -> str | None:
        if v is None:
            return v
        hour, _, minute = v.partition(":")
        if not (hour.isdigit() and minute.isdigit() and len(hour) <= 2 and len(minute) == 2
                and int(hour) < 24 and int(minute) < 60):
            raise ValueError("should be a time as HH:MM, such as 21:00")
        return v


def register(steps) -> None:
    p = steps.add_parser("save", help="Save the Household's setup answers (JSON on stdin) into "
                         "the config, and setup's progress")
    p.add_argument("--read", action="store_true", help="Save nothing, only read the progress back")
    p.set_defaults(func=cmd_save)


def progress_path(config: Path) -> Path:
    return config.parent / "setup-progress.json"


def cmd_save(args: argparse.Namespace) -> int:
    config = Path(os.path.expanduser(os.path.expandvars(args.config or "~/.family/config.yaml")))
    if args.read:
        return _print(read(config))
    if sys.stdin.isatty():
        return _print(outcome("no-answers", "Give the answers as JSON on stdin, such as: parent-recap "
                           "setup save <<< '{\"evening\": \"21:00\"}'"))
    try:
        raw = json.loads(sys.stdin.read())
    except ValueError as e:
        return _print(outcome("invalid-answers", "The answers aren't JSON. Fix them and save again.",
                           errors=[f"not JSON: {e.msg} at line {e.lineno} column {e.colno}"]))
    return _print(save(config, raw))


def read(config: Path) -> dict[str, Any]:
    """What `setup save --read` prints: the progress of the setup whose config is at `config`."""
    return outcome("read", progress=_read_progress(progress_path(config)))


def save(config: Path, raw: Any) -> dict[str, Any]:
    """Saves the answers `raw`, as parsed from JSON, into the config at `config` and setup's
    progress, and returns what `setup save` prints. The setup page saves through this too."""
    if isinstance(raw, dict) and any(isinstance(k, dict) and "myclub_ical_url" in k
                                     for k in raw.get("kids") or []):
        return outcome("invalid-answers", "A Kid's MyClub link is a secret and isn't an answer: "
                    "parent-recap setup myclub --kid NAME asks for it and saves it. Save the "
                    "answers again without it.", errors=["kids: myclub_ical_url isn't an answer"])
    try:
        answers = Answers.model_validate(raw)
    except ValidationError as e:
        return invalid(e)
    form = feedback.pilot_form()
    if answers.feedback is not None and answers.feedback.enabled and form is None:
        return outcome("invalid-answers", "This version of Parent Recap has no pilot feedback "
                       "form, so pilot feedback can't be turned on. Save the answers again "
                       "without it.", errors=["feedback.enabled: this version has no pilot form"])

    data: dict[str, Any] | None = None
    if _config_answers(answers):
        try:
            data = _read_config(config)
        except (OSError, ValueError, yaml.YAMLError, ValidationError):
            return outcome("bad-config", f"The config at {config} can't be read, so nothing was "
                        "saved. Run parent-recap doctor, fix what it names, then save again.")
        _merge(data, answers, form)
        try:
            Config.model_validate(data)
        except ValidationError as e:
            return invalid(e)

    if data is not None:
        _write_private(config, yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
    progress = progress_path(config)
    current = _read_progress(progress)
    if answers.progress is not None:
        current = _merged_progress(current, answers.progress)
        _write_private(progress, json.dumps(current, ensure_ascii=False, indent=2))
    return outcome("saved", progress=current)


def _config_answers(answers: Answers) -> bool:
    return bool(answers.model_fields_set - {"progress"})


def _read_config(config: Path) -> dict[str, Any]:
    if not config.exists():
        return {"kids": []}
    data = yaml.safe_load(config.read_text()) or {}
    if not isinstance(data, dict):
        raise ValueError("the config isn't a mapping")
    Config.model_validate(data)
    data.setdefault("kids", [])
    return data


def _merge(data: dict[str, Any], a: Answers, form: dict[str, Any] | None) -> None:
    if a.language is not None:
        data["summary_language"] = a.language
    if a.city is not None:
        data["city"] = a.city
    if a.kids is not None:
        before = {k.get("name"): k for k in data.get("kids") or [] if isinstance(k, dict)}
        data["kids"] = [{**before.get(k.name, {}), **_given(k)} for k in a.kids]
    if a.recipients is not None:
        _section(data, "email")["to"] = [r.address if r.language is None
                                         else {"address": r.address, "language": r.language}
                                         for r in a.recipients]
    if a.ai is not None:
        _section(data, "llm")["backend"] = a.ai
    if a.evening is not None:
        hour, minute = a.evening.split(":")
        _section(data, "schedule").update(daily_hour=int(hour), daily_minute=int(minute))
    sources = a.sources or SourcesAnswer()
    if sources.gmail is not None:
        gmail = _given(sources.gmail)
        if "address" in gmail:
            gmail["username"] = gmail.pop("address")
        _section(data, "gmail").update(gmail)
    if sources.wilma is not None:
        _section(data, "wilma")["enabled"] = sources.wilma.enabled
    if sources.whatsapp is not None:
        _section(data, "whatsapp").update(_given(sources.whatsapp))
    if a.feedback is not None:
        if a.feedback.enabled and form is not None:
            _section(data, "feedback").update(form)
        _section(data, "feedback").update(_given(a.feedback))
    fb, gmail = data.get("feedback"), data.get("gmail")
    username = gmail.get("username") if isinstance(gmail, dict) else None
    if isinstance(fb, dict) and fb.get("enabled") and not fb.get("household_label") \
            and isinstance(username, str) and username:
        # Until the family changes it on the check page: the setup parent's email user.
        fb["household_label"] = username.split("@")[0]


def _given(answer: BaseModel) -> dict[str, Any]:
    """What an answer says: a key it leaves out or gives as null leaves the config's as it was."""
    return answer.model_dump(exclude_unset=True, exclude_none=True)


def _section(data: dict[str, Any], key: str) -> dict[str, Any]:
    if not isinstance(data.get(key), dict):
        data[key] = {}
    return data[key]


def _read_progress(path: Path) -> dict[str, Any]:
    start: dict[str, Any] = {"phase": "welcome", "source": None,
                             "sources": {s: "to-do" for s in SOURCES}}
    try:
        saved = ProgressAnswer.model_validate(json.loads(path.read_text()))
    except (OSError, ValueError, ValidationError):  # none yet, or not one this program wrote
        return start
    return _merged_progress(start, saved)


def _merged_progress(current: dict[str, Any], p: ProgressAnswer) -> dict[str, Any]:
    out = {**current, "sources": {**current["sources"], **p.sources}}
    if "phase" in p.model_fields_set and p.phase is not None:
        out["phase"] = p.phase
    if "source" in p.model_fields_set:
        out["source"] = p.source
    if "partner" in p.model_fields_set:
        out["partner"] = p.partner and _given(p.partner)
    if "whatsapp_chats" in p.model_fields_set:
        out["whatsapp_chats"] = p.whatsapp_chats and [_given(c) for c in p.whatsapp_chats]
    if "gmail_senders" in p.model_fields_set:
        out["gmail_senders"] = p.gmail_senders and [_given(s) for s in p.gmail_senders]
    return out


def _write_private(path: Path, text: str) -> None:
    """Writes `text` owner-only, in one step, so a reader never sees half of it."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def invalid(e: ValidationError) -> dict[str, Any]:
    # Only where each problem is and what's wrong: pydantic's own message can quote the value.
    errors = [f"{'.'.join(str(p) for p in err['loc']) or 'answers'}: {err['msg']}"
              for err in e.errors(include_input=False, include_url=False)]
    return outcome("invalid-answers", "Nothing was saved. Fix the answers named in errors and "
                "save again.", errors=errors)


def outcome(result: str, next_: str | None = None, **extra: Any) -> dict[str, Any]:
    """A step's outcome as `setup save` prints it, for the setup page to return too."""
    return {"result": result, **extra, **({"next": next_} if next_ else {})}


def _print(out: dict[str, Any]) -> int:
    from .setup_steps import _report
    extra = dict(out)
    return _report(extra.pop("result"), extra.pop("next", None), **extra)
