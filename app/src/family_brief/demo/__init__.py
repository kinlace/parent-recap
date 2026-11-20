"""`parent-recap demo` (#1029): a complete Brief, made on the spot from a made-up Household's week
and sent to the address given, in the language given, for a parent, a creator or a reviewer to see
before connecting any account.

The Household and its week are plain files beside this one: household.yaml, its config, and
week.yaml, what each Source would have given the evening run. They go through the evening run's
own steps (run.demo_mail): the AI filter, a Held-back Message, the model's Digest, Notices, Action
Items and group digests, and the new events as an .ics. No Source is read and no Source's secret
is taken from the store: what leaves the computer is the model's call, by the AI the Household
already uses (Claude Code before setup), and the Brief, sent through the gateway with the Gateway
Key. The demo keeps its state and the program's translated text in a folder of its own, which it
deletes after, so the Household's config, state and archive stay as they were, and it is no
evening run: it leaves no Run Report.

A send-only Gateway Key sends only to the Household's confirmed Recipients (ADR 0033), and before
setup there is none: a Brief the gateway won't send is saved as a page in the temporary folder
instead, to open in a browser."""
from __future__ import annotations

import argparse
import json
import logging
import re
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

from .. import languages
from .. import sources as registry
from ..actions import email as email_action
from ..collectors.base import CalendarEvent, Message
from ..config import Config, LLMConfig
from ..llm.parse import diagnostics_in

log = logging.getLogger(__name__)

HOUSEHOLD = Path(__file__).with_name("household.yaml")
WEEK = Path(__file__).with_name("week.yaml")

# A message's weekday as a Finnish and an English one write it, Monday first, and the English month.
_FI_WEEKDAYS = ("ma", "ti", "ke", "to", "pe", "la", "su")
_EN_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_EN_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_DAY = re.compile(r"\{(en:)?([+-]?\d+w?)\}")


def day(today: date, when: str | int) -> date:
    """The day `when` (week.yaml's `day`) counts from `today`: a number of days, or of weekdays
    with a `w`."""
    spec = str(when)
    if not spec.endswith("w"):
        return today + timedelta(days=int(spec))
    n = int(spec[:-1])
    step, out = (1 if n > 0 else -1), today
    for _ in range(abs(n)):
        out += timedelta(days=step)
        while out.weekday() >= 5:
            out += timedelta(days=step)
    return out


def written(today: date, text: str) -> str:
    """`text` with each {+3w} as a Finnish message writes that day ("to 27.11.") and each
    {en:+3w} as an English one does ("Thu 27 Nov")."""
    def one(m: re.Match[str]) -> str:
        d = day(today, m[2])
        if m[1]:
            return f"{_EN_WEEKDAYS[d.weekday()]} {d.day} {_EN_MONTHS[d.month - 1]}"
        return f"{_FI_WEEKDAYS[d.weekday()]} {d.day}.{d.month}."
    return _DAY.sub(one, text)


@dataclass(frozen=True)
class Week:
    """The made-up Household's week, as the Sources would have given it this evening."""
    messages: list[Message]
    events: list[CalendarEvent]

    def coverage(self) -> dict[str, dict]:
        """What each Source gave, as the Brief's coverage line counts it, in the registry's order."""
        out: dict[str, dict] = {}
        for source in registry.ALL:
            messages = sum(m.source == source.name for m in self.messages)
            events = sum(e.source == source.name for e in self.events)
            if not (messages or (events and source.counts_events)):
                continue
            out[source.name] = {"count": events if source.counts_events else messages, "error": None}
            if source.counts_events and messages:
                out[source.name]["messages"] = messages
        return out


def week(now: datetime) -> Week:
    """week.yaml's week, counted from `now`, in the Household's zone."""
    data = yaml.safe_load(WEEK.read_text(encoding="utf-8"))
    zone, today = now.tzinfo, now.date()

    def at(item: dict[str, Any], key: str = "time") -> datetime:
        return datetime.combine(day(today, item["day"]), time.fromisoformat(item.get(key) or "00:00"), zone)

    messages = [Message(source=m["source"], external_id=str(m["id"]), timestamp=at(m),
                        sender=m.get("sender"), subject=m.get("subject"),
                        body=written(today, m["body"]).strip(), chat_name=m.get("chat"),
                        kid_hint=m.get("kid"), metadata=dict(m.get("metadata") or {}))
                for m in data["messages"]]
    events = [CalendarEvent(source=e["source"], external_id=e["id"], title=e["title"], start=at(e),
                            end=at(e, "end") if e.get("end") else None, location=e.get("location"),
                            description=e.get("description") or "", kid=e.get("kid"),
                            all_day=bool(e.get("all_day")))
              for e in data["events"]]
    return Week(messages, events)


def household(to: str, language: str, llm: LLMConfig, folder: Path) -> Config:
    """household.yaml's Household, whose one Recipient is `to`, reading `language`, written by
    the AI `llm`, with its state and archive in `folder`."""
    data = yaml.safe_load(HOUSEHOLD.read_text(encoding="utf-8"))
    data.update(summary_language=language, llm=llm.model_dump(),
                email={"enabled": True, "to": [{"address": to, "language": language}]},
                imessage={"enabled": False}, archive={"dir": str(folder / "archive")},
                state_path=str(folder / "state.json"))
    return Config.model_validate(data)


def _households_ai(config: str | None, backend: str | None) -> LLMConfig:
    """The AI the demo uses: `backend` when given, else the Household's, else Claude Code. Only
    the config's AI section is read, so a config that no longer loads whole still gives it."""
    llm = LLMConfig()
    path = Path(config).expanduser() if config else Path("~/.family/config.yaml").expanduser()
    try:
        llm = LLMConfig.model_validate((yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("llm") or {})
    except FileNotFoundError:
        pass  # before setup
    except Exception as e:  # noqa: BLE001 - the demo still runs, with Claude Code
        log.warning("Couldn't read the AI from %s (%s), so the demo uses %s", path, e, llm.backend)
    return llm.model_copy(update={"backend": backend}) if backend else llm


def run(to: str, language: str, llm: LLMConfig) -> dict[str, str]:
    """Makes the demo's Brief and sends it to `to`: what happened, as the command prints it."""
    from .. import run as evening
    with tempfile.TemporaryDirectory(prefix="parent-recap-demo-") as folder, \
            diagnostics_in(Path(folder) / "logs"):  # a failed model call's too
        cfg = household(to, language, llm, Path(folder))
        sample = week(datetime.now(ZoneInfo(cfg.timezone)))
        mail, error = evening.demo_mail(cfg, sample.messages, sample.events, sample.coverage())
    if error is not None:
        return {"demo": "failed", "why": f"the AI couldn't write the Brief: {error}"}
    try:
        email_action.gateway(cfg.llm.gateway_url, mail.subject, mail.text, mail.to, mail.html, mail.attachments)
    except Exception as e:  # noqa: BLE001 - whatever stopped it, the Brief is kept to open; a
        # gateway error comes without the key (email.gateway)
        page = Path(tempfile.gettempdir()) / f"parent-recap-demo-{language}.html"
        page.write_text(mail.html, encoding="utf-8")
        return {"demo": "saved", "file": str(page),
                "why": f"the Brief couldn't be sent to {to}: {e}. A Gateway Key sent on request sends only "
                       "to the Household's confirmed Recipients"}
    return {"demo": "sent", "to": to}


def _address(value: str) -> str:
    value = value.strip()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
        raise argparse.ArgumentTypeError(f"{value!r} is not an email address")
    return value


def cmd_demo(args: argparse.Namespace) -> int:
    result = run(args.to, args.language, _households_ai(args.config, args.ai))
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["demo"] == "sent" else 1


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("demo", help="Send a demo Brief made from a made-up family's week to an address, "
                                    "reading no Source and changing nothing of the Household's")
    p.add_argument("--to", required=True, type=_address, help="The address to send it to")
    p.add_argument("--language", required=True, type=languages.code,
                   help="The Brief's language, such as en, fi or zh")
    p.add_argument("--ai", choices=["claude", "codex", "gateway"], default=None,
                   help="The AI that writes it (default: the Household's, or Claude before setup)")
    p.set_defaults(func=cmd_demo)
