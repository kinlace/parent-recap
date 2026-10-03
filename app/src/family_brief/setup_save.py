"""setup save — write the Household's setup answers into the config, and keep setup's progress.

Both the setup page and the chat setup save through this command, so they write the same config
in the same way and can hand over to each other at the step reached (ADR 0006). It reads the
answers as JSON on stdin, every key optional:

  {"language": "zh",
   "kids": [{"name": "Mia Virtanen", "everyday_name": "Mia", "aliases": ["米娅"]}],
   "recipients": [{"address": "parent@gmail.com"}, {"address": "partner@gmail.com", "language": "fi"}],
   "ai": "claude",
   "evening": "21:00",
   "sources": {"gmail": {"address": "parent@gmail.com", "allowlist_domains": ["espoo.fi"]},
               "wilma": {"enabled": true},
               "whatsapp": {"enabled": true, "chats": [{"name": "3B", "kid": "Mia Virtanen", "label": "class"}]}},
   "feedback": {the pilot feedback section as the Parent Recap team sent it},
   "progress": {"phase": "connect", "source": "gmail", "sources": {"wilma": "done"}}}

What the answers don't mention stays as it was. The Kids given are the Household's Kids: a Kid
left out is removed, and each one given keeps what the answers don't say about them, such as a
MyClub link, which only `setup myclub` saves. The merged config is checked against the config
model before it's written. `--read` saves nothing and only reads the progress back.

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

from .brief_text import Language
from .config import Config, FeedbackConfig, WhatsAppChat

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


class ProgressAnswer(_Strict):
    phase: Phase | None = None
    source: SourceName | None = None
    sources: dict[SourceName, Status] = Field(default_factory=dict)


class Answers(_Strict):
    language: Language | None = None
    kids: list[KidAnswer] | None = None
    recipients: list[RecipientAnswer] | None = None
    ai: Literal["claude", "codex"] | None = None
    evening: str | None = None      # HH:MM, when the Brief comes
    sources: SourcesAnswer | None = None
    feedback: FeedbackConfig | None = None
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
    progress = progress_path(config)
    if args.read:
        return _report("read", progress=_read_progress(progress))

    if sys.stdin.isatty():
        return _report("no-answers", "Give the answers as JSON on stdin, such as: family-brief "
                       "setup save <<< '{\"evening\": \"21:00\"}'")
    try:
        raw = json.loads(sys.stdin.read())
    except ValueError as e:
        return _report("invalid-answers", "The answers aren't JSON. Fix them and save again.",
                       errors=[f"not JSON: {e.msg} at line {e.lineno} column {e.colno}"])
    if isinstance(raw, dict) and any(isinstance(k, dict) and "myclub_ical_url" in k
                                     for k in raw.get("kids") or []):
        return _report("invalid-answers", "A Kid's MyClub link is a secret and isn't an answer: "
                       "family-brief setup myclub --kid NAME asks for it and saves it. Save the "
                       "answers again without it.", errors=["kids: myclub_ical_url isn't an answer"])
    try:
        answers = Answers.model_validate(raw)
    except ValidationError as e:
        return _invalid(e)

    data: dict[str, Any] | None = None
    if _config_answers(answers):
        try:
            data = _read_config(config)
        except (OSError, ValueError, yaml.YAMLError, ValidationError):
            return _report("bad-config", f"The config at {config} can't be read, so nothing was "
                           "saved. Run family-brief doctor, fix what it names, then save again.")
        _merge(data, answers)
        try:
            Config.model_validate(data)
        except ValidationError as e:
            return _invalid(e)

    if data is not None:
        _write_private(config, yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
    current = _read_progress(progress)
    if answers.progress is not None:
        current = _merged_progress(current, answers.progress)
        _write_private(progress, json.dumps(current, ensure_ascii=False, indent=2))
    return _report("saved", progress=current)


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


def _merge(data: dict[str, Any], a: Answers) -> None:
    if a.language is not None:
        data["summary_language"] = a.language
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
        _section(data, "feedback").update(_given(a.feedback))


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


def _invalid(e: ValidationError) -> int:
    # Only where each problem is and what's wrong: pydantic's own message can quote the value.
    errors = [f"{'.'.join(str(p) for p in err['loc']) or 'answers'}: {err['msg']}"
              for err in e.errors(include_input=False, include_url=False)]
    return _report("invalid-answers", "Nothing was saved. Fix the answers named in errors and "
                   "save again.", errors=errors)


def _report(result: str, next_: str | None = None, **extra: Any) -> int:
    from .setup_steps import _report as report
    return report(result, next_, **extra)
