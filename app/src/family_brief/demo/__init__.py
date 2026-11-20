"""`parent-recap demo` (#1029): a complete Brief made from a made-up Household's week, for someone
to see before connecting anything: a parent, a creator, a reviewer.

The Household and its week are plain files beside this module (household.yaml, week.yaml), dated
from the evening the demo runs. They go through the evening run's own steps (run.send_demo): the
AI filter holds the sensitive Message back and masks the rest, the model writes the Brief, and it
is rendered with its new events as an .ics and sent to the one address given, in the language
given, saying at the top that it's a demo.

What it never does: read or sign in to a Source, read the Household's config but for its AI, or
write the config, the state, the archive or a Run Report. Its config's state and archive point into
a folder of its own that it removes. Only two things leave the computer: the model call, through
the Household's AI (before setup, Claude Code or Codex if the computer has one), and the email,
through the gateway's relay with the stored Gateway Key, the only secret it reads besides the AI's
own sign-in. With no key, or a key the relay won't send to that address with, it saves the Brief
as a page and opens it instead.
"""
from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Callable
from zoneinfo import ZoneInfo

import yaml

from .. import gateway, render
from .. import sources as source_registry
from ..actions import email as email_action
from ..collectors.base import CalendarEvent, Message
from ..config import Config, LLMConfig
from ..state import State
from ..system import desktop

if TYPE_CHECKING:
    from ..run import IO


HERE = Path(__file__).parent
# The languages with reviewed program text: any other would translate the program's text once and
# keep it beside the Household's state (languages.py), which the demo never writes.
LANGUAGES = ("en", "fi", "zh")
AIS = ("claude", "codex", "gateway")


# ── The week

@dataclass
class Week:
    """What the made-up Household's Sources give the evening run."""
    messages: list[Message]
    events: list[CalendarEvent]


_FI_WEEKDAYS = ("maanantai", "tiistai", "keskiviikko", "torstai", "perjantai", "lauantai", "sunnuntai")
_EN_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_TOKEN = re.compile(r"\{(date|weekday|iso|dow|in)([+-]\d+)\}")


def school_day(today: date, n: int) -> date:
    """The `n`th school day after `today` (before it, for a negative `n`); `today` for 0."""
    day, step = today, (1 if n > 0 else -1)
    for _ in range(abs(n)):
        day += timedelta(days=step)
        while day.weekday() >= 5:
            day += timedelta(days=step)
    return day


def _filled(text: str, today: date) -> str:
    """`text` with each {kind+N} of week.yaml written out for school day N."""
    def one(m: re.Match[str]) -> str:
        day = school_day(today, int(m.group(2)))
        ahead = (day - today).days
        return {"date": f"{day.day}.{day.month}.", "weekday": _FI_WEEKDAYS[day.weekday()],
                "iso": day.isoformat(), "dow": _EN_WEEKDAYS[day.weekday()],
                "in": {1: "tomorrow", 2: "day after tomorrow"}.get(ahead, f"in {ahead} days")}[m.group(1)]
    return _TOKEN.sub(one, text)


def _at(today: date, entry: dict, zone: ZoneInfo, key: str = "time") -> datetime:
    return datetime.combine(school_day(today, int(entry["day"])), time.fromisoformat(entry[key]), zone)


def _whatsapp(household: dict) -> dict[str, dict]:
    return {c["name"]: c for c in household.get("whatsapp", {}).get("chats", [])}


def week(now: datetime) -> Week:
    """The made-up Household's week for the evening of `now` (aware), as its Sources give it: a
    message of the evening itself later than `now` is moved to just before it, a minute apart."""
    data, household = _read("week.yaml"), _read("household.yaml")
    zone = ZoneInfo(household["timezone"])
    now = now.astimezone(zone)
    today, chats = now.date(), _whatsapp(household)
    messages = []
    for n, entry in enumerate(data["messages"]):
        at = _at(today, entry, zone)
        if at >= now:
            at = now - timedelta(minutes=len(data["messages"]) - n)
        metadata = {k: _filled(v, today) if isinstance(v, str) else v
                    for k, v in (entry.get("metadata") or {}).items()}
        chat, kid = entry.get("chat"), entry.get("kid")
        if entry["source"] == "whatsapp":  # as the WhatsApp Source takes them from the config
            kid = chats[chat].get("kid")
            metadata = {"from_me": False, **({"digest": True} if chats[chat].get("digest") else {})}
        messages.append(Message(
            source=entry["source"], external_id=str(entry["id"]), timestamp=at.astimezone(timezone.utc),
            sender=entry.get("sender"), subject=_filled(entry["subject"], today) if entry.get("subject") else None,
            body=_filled(entry["body"], today).strip(), chat_name=chat, kid_hint=kid, metadata=metadata))
    events = [CalendarEvent(
        source=e["source"], external_id=str(e["id"]), title=e["title"],
        start=_at(today, {**e, "time": e.get("time", "00:00")}, zone),
        end=_at(today, e, zone, "end") if e.get("end") else None, location=e.get("location"),
        description=e.get("description", ""), kid=e.get("kid"), all_day=bool(e.get("all_day")))
        for e in data["events"]]
    return Week(messages, events)


def coverage(w: Week) -> dict[str, dict]:
    """What each of the week's Sources gave, as the Brief's coverage line counts it: a Source that
    counts its events by them, with its Messages besides, and the others by their Messages."""
    out: dict[str, dict] = {}
    for source in [*dict.fromkeys(m.source for m in w.messages), *dict.fromkeys(e.source for e in w.events)]:
        if source in out:
            continue
        record = source_registry.named(source)
        messages = sum(m.source == source for m in w.messages)
        if record is not None and record.counts_events:
            out[source] = {"count": sum(e.source == source for e in w.events), "error": None,
                           **({"messages": messages} if messages else {})}
        else:
            out[source] = {"count": messages, "error": None}
    return out


def _read(name: str) -> dict:
    return yaml.safe_load((HERE / name).read_text(encoding="utf-8"))


# ── The Household

def ai_of(config: Path, ai: str | None) -> LLMConfig:
    """The AI the demo is written with: `ai` if given, else the Household's, else, before setup,
    Claude Code or Codex if the computer has one, else the gateway with a stored Gateway Key."""
    llm = LLMConfig()
    if config.exists():
        llm = Config.load(config).llm.model_copy()
    elif ai is None:
        from ..llm import claude, codex
        ai = "claude" if claude.find() else "codex" if codex.find_at(None) else "gateway"
    if ai is not None:
        llm = llm.model_copy(update={"backend": ai})
    return llm


def household(to: str, language: str, llm: LLMConfig, folder: Path) -> Config:
    """The made-up Household with `to` its one Recipient, reading `language`, written by `llm`, and
    its state and archive in `folder`, which nothing is meant to reach."""
    data = _read("household.yaml")
    data.update(summary_language=language, email={"to": [{"address": to, "language": language}]},
                llm=llm.model_dump(), state_path=str(folder / "state.json"),
                archive={"dir": str(folder / "archive")})
    return Config.model_validate(data)


# ── The demo

Send = Callable[[Config, render.Mail], None]


def _relay(sent: list[render.Mail], failed: list[Exception]) -> Send:
    """The demo's way out: the gateway's relay with the stored Gateway Key, as every Brief is sent
    (ADR 0032), but never asking for a key, nor binding the address to one (ADR 0033): that is the
    Household's own. Each mail handed to it is kept, and why one didn't go."""
    def send(cfg: Config, mail: render.Mail) -> None:
        sent.append(mail)
        try:
            email_action.gateway(cfg.llm.gateway_url, mail.subject, mail.text, mail.to, mail.html,
                                 mail.attachments)
        except Exception as e:
            failed.append(e)
            raise
    return send


def _io(send: Send) -> IO:
    """The ports of the evening run for the demo: its relay, and no calendar, archive, earlier
    Briefs or network to wait for."""
    from ..run import IO

    def nowhere(*_a: object) -> None:
        raise RuntimeError("the demo writes no calendar and no archive")
    return IO(send_mail=send, send_imessage=nowhere, calendar_ready=lambda: False,
              upcoming_events=lambda *_a: [], create_events=nowhere, recent_points=lambda *_a: [],
              write_archive=nowhere, load_state=State, wait_for_network=lambda _host: None)


def _why(error: Exception) -> str:
    if isinstance(error, gateway.NoKey):
        return "there is no Gateway Key on this computer to send it with"
    if isinstance(error, gateway.GatewayError) and error.status == 403:
        return ("the Gateway Key here sends only to this Household's confirmed Recipients, and "
                "the address isn't one")
    return str(error)


def _save(mail: render.Mail) -> Path:
    """The demo's Brief as a page, with its .ics beside it, in a new folder of its own."""
    folder = Path(tempfile.mkdtemp(prefix="parent-recap-demo-"))
    page = folder / "parent-recap-demo.html"
    page.write_text(mail.html or f"<pre>{mail.text}</pre>", encoding="utf-8")
    for name, payload, _mime in mail.attachments:
        (folder / name).write_bytes(payload)
    return page


def run(to: str, language: str, config: Path, ai: str | None = None) -> int:
    """Makes the demo's Brief and sends it to `to` in `language`; with no way to send it, saves it
    and opens it. 1 when the model couldn't write it."""
    from .. import run as evening
    llm = ai_of(config, ai)
    sent: list[render.Mail] = []
    failed: list[Exception] = []
    with tempfile.TemporaryDirectory(prefix="parent-recap-demo-") as folder:
        cfg = household(to, language, llm, Path(folder))
        started = datetime.now(ZoneInfo(cfg.timezone))
        w = week(started)
        result = evening.send_demo(cfg, _io(_relay(sent, failed)), w.messages, w.events, coverage(w), started)
    if result.error is not None:
        print(f"The demo Brief couldn't be written: {result.error}")
        return 1
    if result.delivered:
        print(f"The demo Brief was sent to {to}.")
        return 0
    if not sent:
        print("The demo Brief couldn't be made; the log above says why.")
        return 1
    page = _save(sent[-1])
    why = _why(failed[-1]) if failed else "it couldn't be sent"
    opened = desktop.open_url(page.as_uri())
    print(f"The demo Brief wasn't emailed: {why}. It is saved at {page}"
          + (" and opened in the browser." if opened else "; open it in a browser."))
    return 0


def register(sub) -> None:
    p = sub.add_parser("demo", help="Send one Brief made from a made-up family's week to an "
                       "address, to see what Parent Recap sends (reads no Source, changes nothing)")
    p.add_argument("--to", required=True, help="The email address to send the demo Brief to")
    p.add_argument("--language", required=True, choices=LANGUAGES, help="The Brief's language")
    p.add_argument("--ai", choices=AIS, default=None,
                   help="The AI that writes it (default: the Household's, or before setup the one "
                        "this computer has)")
    p.set_defaults(func=_cmd)


def _cmd(args) -> int:
    from ..config import _expand
    config = Path(_expand(args.config or "~/.family/config.yaml"))
    return run(args.to, args.language, config, args.ai)
