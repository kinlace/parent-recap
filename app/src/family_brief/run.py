"""The evening run: it reads the Sources, has the model write the Brief, writes the calendar, sends
each Recipient their version and archives the night, reaching outside the program through `IO`.
What the Brief says is render.py's, and whether tonight sends one and leaves a trace is plan()'s."""
from __future__ import annotations

import json
import logging
import math
import re
import time
import traceback
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Literal
from zoneinfo import ZoneInfo

from . import ai_filter, feedback, gateway, languages, last_run, llm, private_files, render, slept, wilma_egress
from . import sources as source_registry
from .actions import archive, calendar as calendar_action, email as email_action, imessage
from .brief import Brief, EarlierBrief, RunNotes
from .brief_text import BriefText, Language
from .collectors.base import CalendarEvent, Message
from .config import Config
from .state import State
from .summarize import add_the_nights_people, extract_calendar_events, for_the_ai, placeholders_for, summarize
from .translate import per_language, translate

log = logging.getLogger("family_brief")


@dataclass(frozen=True)
class Options:
    """How `parent-recap run` was asked to run. A preview is a dry run that prints, for the setup
    page, how far it is and then the first Recipient's email. A first Brief, the hosted sign-up's,
    is made even on a quiet night, as a preview is, and sent to every Recipient."""
    dry_run: bool = False
    preview: bool = False
    first: bool = False
    sources: list[str] | None = None  # the connected ones when None
    lookback_hours: int | None = None  # each Source's own, caught up, when None
    if_missed: bool = False
    # Started by the system's scheduler (jobs.SCHEDULED), not by hand: a start well after the
    # Brief's time catches up a missed evening, and only within CATCH_UP_LIMIT (#894).
    scheduled: bool = False


@dataclass(frozen=True)
class IO:
    """The ports the evening run reaches the world through, other than the Sources and the model:
    the mailer, iMessage, Google Calendar, the archive, the state and the network it waits for.
    `real()` gives the program's own."""
    send_mail: Callable[[Config, render.Mail], None]  # by the config's sender
    send_imessage: Callable[[list[str], str], None]
    calendar_ready: Callable[[], bool]
    upcoming_events: Callable[[Config, int], list[dict]]
    create_events: Callable[[Config, State, list[CalendarEvent], list[dict]], None]
    recent_points: Callable[[Config, str], list[EarlierBrief]]
    write_archive: Callable[[Config, str, list[Message], Brief, RunNotes, list[dict], bool], Path]
    load_state: Callable[[Path], State]
    wait_for_network: Callable[[str], None]

    @classmethod
    def real(cls) -> IO:
        return cls(
            send_mail=lambda cfg, m: email_action.send(cfg, subject=m.subject, body_text=m.text,
                                                       body_html=m.html, to_addrs=m.to,
                                                       attachments=m.attachments),
            send_imessage=imessage.send,
            calendar_ready=calendar_action.is_configured,
            upcoming_events=calendar_action.get_upcoming_events,
            create_events=calendar_action.create_events,
            recent_points=archive.recent_points,
            write_archive=archive.write,
            load_state=State,
            wait_for_network=lambda host: wait_for_network(probe_host=host),
        )


# ── What tonight does

@dataclass(frozen=True)
class Plan:
    """How tonight's run ends: with no Brief (a quiet night), with the short Brief that says no
    Source could be read, or with the Brief. Only a run that `sends` mails and iMessages, archives
    the night and saves the state; a dry run leaves no trace. A preview `shows` the setup page the
    Brief and sends nothing."""
    ending: Literal["quiet", "nothing_read", "brief"]
    sends: bool
    shows: bool = False


def plan(options: Options, *, anything_new: bool, nothing_could_be_read: bool,
         nothing_to_show: bool | None = None) -> Plan:
    """What tonight does, from what was read (`anything_new`: Messages, events, or Action Items due
    soon) and, once the model has written the Brief, whether it has `nothing_to_show`."""
    if options.preview:
        # A preview shows a quiet night's Brief too, so the family sees what one looks like.
        return Plan("brief", sends=False, shows=True)
    if options.first:
        # So does a first Brief, which the family is told has been sent.
        return Plan("brief", sends=not options.dry_run)
    sends = not options.dry_run
    if not anything_new:
        # Silence would look like a quiet night, so the parents hear that nothing could be read.
        return Plan("nothing_read" if nothing_could_be_read else "quiet", sends)
    # A night the model finds nothing worth saying about, such as an ordinary timetable alone, is
    # as quiet as one with nothing new (#385). When nothing could be read, the Brief's notes say so.
    if nothing_to_show and not nothing_could_be_read:
        return Plan("quiet", sends)
    return Plan("brief", sends)


# ── Reading the Sources

def _all_kid_terms(cfg: Config) -> list[str]:
    terms: list[str] = []
    for k in cfg.kids:
        terms.extend(k.match_terms())
    return terms


class _CollectorErrors(logging.Handler):
    """Remembers the first error each collector logs, so the brief can say what it missed.
    Collectors often log and return nothing rather than raise (e.g. MyClub's feed)."""

    def __init__(self) -> None:
        super().__init__(logging.ERROR)
        self.first: dict[str, str] = {}

    def emit(self, record: logging.LogRecord) -> None:
        self.first.setdefault(record.name.rsplit(".", 1)[-1], record.getMessage())


def collect(cfg: Config, state: State, sources: list[str],
            coverage: dict[str, dict] | None = None,
            reading: Callable[[str], None] | None = None) -> tuple[list[Message], list[CalendarEvent]]:
    """Run each collector, isolating failures. If `coverage` is given, it is filled with
    {source: {"count": n, "error": first error or None}} for the brief's coverage line, and for a
    Source that counts its events, "messages" when it had Messages too.
    `reading` is told each Source as its collector starts."""
    kid_terms = _all_kid_terms(cfg)
    messages: list[Message] = []
    direct_events: list[CalendarEvent] = []
    errors = _CollectorErrors()
    collectors_log = logging.getLogger("family_brief.collectors")
    collectors_log.addHandler(errors)
    try:
        for record in source_registry.ALL:
            source = record.name
            if source not in sources:
                continue
            if reading:
                reading(source)
            count, messages_too, error = 0, 0, None
            try:
                got = record.collect(cfg, state, kid_terms)
                log.info("%s: collected %d messages, %d events", source, len(got.messages), len(got.events))
                messages.extend(got.messages)
                # A feed gives UTC; the Brief and the model's already-queued list use local time.
                direct_events.extend(e.in_zone(cfg.timezone) for e in got.events)
                count = len(got.events if record.counts_events else got.messages)
                # A Source that counts its events and has Messages too, such as eVaka's daycare's.
                messages_too = len(got.messages) if record.counts_events else 0
            except Exception as e:
                log.error("%s collector failed: %s\n%s", source, e, traceback.format_exc())
                error = str(e)
                # Its result is dropped, so the Messages it read before failing must stay unseen.
                state.forget_new_seen_messages(source)
            if coverage is not None:
                coverage[source] = {"count": count, "error": error or errors.first.get(source)}
                if messages_too:
                    coverage[source]["messages"] = messages_too
    finally:
        collectors_log.removeHandler(errors)
    return messages, direct_events


MAX_CATCH_UP_HOURS = 7 * 24


def _behind(coverage: dict[str, dict]) -> list[str]:
    """The Sources tonight's run didn't fully read, whose Messages may still be waiting."""
    return [source for source, c in coverage.items() if c["error"]]


def _windows(cfg: Config) -> list[tuple[str, source_registry.Window]]:
    """Each Source that reads back a number of hours, by name, with the config section that says
    how many."""
    return [(s.name, s.lookback(cfg)) for s in source_registry.ALL if s.lookback]


def catch_up_lookback(cfg: Config, state: State) -> None:
    """After undelivered nights, or nights a Source failed, widen each Source's lookback to reach
    back to the last run that was caught up for it, so its Messages fall inside the window again.
    Capped at a week."""
    for source, source_cfg in _windows(cfg):
        caught_up_at = state.caught_up_at(source)
        if caught_up_at is None:
            continue
        gap = (datetime.now(timezone.utc) - caught_up_at).total_seconds() / 3600
        hours = min(math.ceil(gap) + 2, MAX_CATCH_UP_HOURS)  # same 2h slack as the 26h default
        if hours > source_cfg.lookback_hours:
            source_cfg.lookback_hours = hours
            log.info("%s lookback widened to %d hours to cover undelivered nights", source, hours)


def wait_for_network(timeout_seconds: int = 60, probe_host: str = "api.anthropic.com") -> None:
    """Block briefly until DNS+TCP to a key host works. Catches the wake-from-sleep case
    where launchd fires before Wi-Fi has reconnected."""
    import socket
    deadline = time.time() + timeout_seconds
    delay = 1
    while time.time() < deadline:
        try:
            socket.create_connection((probe_host, 443), timeout=3).close()
            return
        except OSError as e:
            log.info("Network not ready (%s); retry in %ds", e.__class__.__name__, delay)
            time.sleep(delay)
            delay = min(delay * 2, 8)
    log.warning("Network probe to %s never succeeded within %ds — proceeding anyway",
                probe_host, timeout_seconds)


def _nothing_could_be_read(cfg: Config, coverage: dict[str, dict]) -> bool:
    """Whether every Source was tried tonight and failed. A run of some Sources by hand didn't try
    the others, so it can't say."""
    return set(cfg.default_sources()) <= set(coverage) and all(c["error"] for c in coverage.values())


@dataclass
class _Read:
    """What tonight's run read: the Sources' Messages and events, whether one of those events is
    tomorrow, what each Source gave (`coverage`), and the earlier Briefs' points, some perhaps due
    soon. `started` is when the run started, in the Household's zone: the Brief is for that
    evening even when a computer that slept finishes it the next day (#887)."""
    started: datetime
    messages: list[Message]
    direct_events: list[CalendarEvent]
    coverage: dict[str, dict]
    earlier: list[EarlierBrief]
    due_soon: bool
    event_tomorrow: bool

    @property
    def today(self) -> str:
        return self.started.strftime("%Y-%m-%d")

    @property
    def anything_new(self) -> bool:
        return bool(self.messages or self.direct_events or self.due_soon)


def _read(cfg: Config, options: Options, io: IO, state: State, started: datetime) -> _Read:
    sources = options.sources or cfg.default_sources()
    log.info("Running collectors: %s (dry_run=%s)", sources, options.dry_run or options.preview)
    io.wait_for_network(llm.backend(cfg).probe_host)
    coverage: dict[str, dict] = {}
    if options.preview:
        _preview_step("reading", sources=[s.name for s in source_registry.ALL if s.name in sources])
    messages, direct_events = collect(
        cfg, state, sources, coverage,
        reading=(lambda source: _preview_step("reading", source=source)) if options.preview else None)
    # A Source the Household set up but that can't be read, such as a Linked Device that needs a
    # new pairing, is in the coverage line as one that failed, unless the run asked for others.
    for source, why in ({} if options.sources else source_registry.unread(cfg, state)).items():
        log.warning("%s not read: %s", source, why.reason)
        since = why.since.astimezone(ZoneInfo(cfg.timezone)).isoformat() if why.since else None
        coverage[source] = {"count": 0, "error": why.reason, "since": since}
    today = started.strftime("%Y-%m-%d")
    earlier = io.recent_points(cfg, today)
    tomorrow = started.date() + timedelta(days=1)
    return _Read(started, messages, direct_events, coverage, earlier, archive.has_due_soon(earlier, today),
                 any(e.start.date() == tomorrow for e in direct_events))


# ── Writing the Brief

# How long `run --preview` tries a busy model for: the setup page gives up on the whole preview
# after 15 minutes (setup_server.Timeouts.brief), reading the Sources included.
PREVIEW_BUDGET = 10 * 60


def _fallback_brief(messages: list[Message], err: str, t: BriefText, cfg: Config, slept: bool = False) -> Brief:
    """Rule-based digest when the LLM call fails. Never leaks the raw error to iMessage. After the
    computer `slept` during the run, it says the model failed after it woke."""
    # Group by (kid_hint or 'unknown', source)
    buckets: dict[tuple[str, str], list[Message]] = {}
    for m in messages:
        key = (cfg.kid_called(m.kid_hint) or t.unsorted, m.source)
        buckets.setdefault(key, []).append(m)

    # A Gateway Key out of credit for the day is no fault to look into: it says so, and when it ends.
    header = t.fallback_slept if slept else t.fallback_header
    lines = [t.fallback_spent if gateway.spent(err) else header, ""]
    for (kid, source), msgs in sorted(buckets.items()):
        lines.append(f"**{kid} · {source}**{t.fallback_count.format(count=t.messages(len(msgs)))}")
        for m in msgs[:15]:
            at = m.timestamp.astimezone()
            ts = t.message_time.format(month=at.month, day=at.day, hour=at.hour, minute=at.minute)
            who = m.sender or m.chat_name or ""
            subj = (m.subject or m.body.replace("\n", " "))[:60]
            lines.append(f"  • {ts} {who}: {subj}")
        if len(msgs) > 15:
            lines.append(t.fallback_more.format(n=len(msgs) - 15))
        lines.append("")
    return Brief(digest="\n".join(lines))


_ADDRESS = re.compile(r"\s*<[^<>]*>$")


def _sender_name(sender: str | None) -> str:
    """A sender as the Brief names them: the name of `Name <address>`, or else the address."""
    sender = (sender or "").strip()
    return _ADDRESS.sub("", sender).strip().strip('"') or sender.strip("<>")


def _held_back(held: list[Message]) -> list[render.HeldBack]:
    """Tonight's Held-back Messages as the Brief lists them, each with the link and the group its
    Source's record gives. With no record, by sender and subject and with no link."""
    out = []
    for m in held:
        source = source_registry.named(m.source)
        link = source.held_back_link(m) if source else None
        group = source.group if source else None
        out.append(render.HeldBack(m.source, _sender_name(m.sender), group(m) if group else m.subject,
                                   link if link and link.startswith("https://") else None,
                                   group_first=bool(group)))
    return out


def _ics_candidates(cfg: Config, state: State, events: list) -> list:
    """Events that would go into tonight's .ics attachment: those of a Source whose record lets
    them in (MyClub's only when asked for), and those the model found in the messages."""
    return [e for e in events
            if ((source := source_registry.named(e.source)) is None or source.in_ics(cfg))
            and not calendar_action.already_created(state, e)]


def _new_events(cfg: Config, state: State, events: list[CalendarEvent]) -> list[CalendarEvent]:
    """The Sources' events the Brief lists as new tonight: those not yet written, and in ics mode
    only those of a Source the .ics takes."""
    if cfg.google_calendar.mode == "ics":
        return _ics_candidates(cfg, state, events)
    return [e for e in events if not calendar_action.already_created(state, e)]


def _as_created(events: list) -> list[dict]:
    """Events the Brief lists without a Google write, shaped like `create_events` results."""
    return [{"title": e.title, "start": e.start_iso(), "kid": e.kid, "location": e.location,
             "description": e.description} for e in events]


@dataclass
class _Calendar:
    """Tonight's events as the Brief lists them, those attached as .ics, and why Google Calendar
    did not take them."""
    created: list[dict] = field(default_factory=list)
    ics_events: list[CalendarEvent] = field(default_factory=list)
    problem: render.CalendarProblem | None = None


def _write_calendar(cfg: Config, io: IO, state: State, events: list[CalendarEvent], sends: bool,
                    google_ready: bool) -> _Calendar:
    mode = cfg.google_calendar.mode
    out = _Calendar()
    if not sends:
        preview = _ics_candidates(cfg, state, events) if mode == "ics" else events
        log.info("DRY-RUN: calendar mode=%s, %d candidate events", mode, len(preview))
        out.created = _as_created(preview)
    elif mode == "google":
        if not google_ready:
            out.problem = ("not_authorized", "")
        else:
            try:
                io.create_events(cfg, state, events, out.created)
            except Exception as e:
                log.error("Calendar write failed: %s\n%s", e, traceback.format_exc())
                msg = str(e)
                if "invalid_grant" in msg or "expired" in msg or "revoked" in msg:
                    out.problem = ("expired", "")
                else:
                    out.problem = ("write_failed", msg[:120].rstrip('.。 '))
        if out.problem:
            # Nothing is lost when Google fails: tonight's events go out as .ics instead. Events
            # written before a mid-loop failure are already in state, so the filter drops them
            # from the .ics; they stay listed with their Google links.
            out.ics_events = _ics_candidates(cfg, state, events)
            out.created = out.created + _as_created(out.ics_events)
    elif mode == "ics":
        out.ics_events = _ics_candidates(cfg, state, events)
        out.created = _as_created(out.ics_events)
    # A Source's events carry the Kid's configured name, which their calendar identity keeps.
    out.created = [{**c, "kid": cfg.kid_called(c.get("kid"))} for c in out.created]
    return out


@dataclass
class _Written:
    """Tonight's Brief as the model wrote it, with what the run needs to send and archive it."""
    night: render.Night
    notes: RunNotes
    for_ai: list[Message]
    held: list[Message]
    model_events: list[CalendarEvent]
    placeholders: ai_filter.Placeholders | None


def _write(cfg: Config, options: Options, io: IO, state: State, read: _Read, sends: bool,
           watch: slept.Watch, caught_up: bool) -> _Written:
    """Tonight's Brief. When the computer slept since the run started (`watch`), the Brief says it
    was finished late; the hosted server doesn't sleep, and its families have no computer to close.
    A Brief that catches up a missed evening (`caught_up`) says so too (#894)."""
    if sends:  # a dry run leaves no trace, so it uses what is already there
        languages.prepare_each(cfg, cfg.brief_languages())
    t = languages.text(cfg, cfg.brief_language())
    google_ready = cfg.google_calendar.mode == "google" and io.calendar_ready()

    # Existing Google Calendar events give the LLM context to avoid duplicates (google mode only).
    upcoming: list[dict] = []
    if google_ready:
        try:
            upcoming = io.upcoming_events(cfg, cfg.google_calendar.lookahead_days_context)
        except Exception as e:
            log.warning("Failed to fetch upcoming events (continuing): %s", e)

    # Tonight's placeholders while the AI filter is on: the Brief's call and each translation share
    # them, so a value has the same placeholder in every prompt (ADR 0013).
    placeholders = placeholders_for(cfg)
    # Messages that look sensitive reach no model call: the Brief lists them itself (ADR 0013).
    for_ai, held = for_the_ai(cfg, read.messages)
    # Only the model tells a Source's event on the evening before it (#696), so tomorrow's asks it,
    # and one a parent must plan for when it's first listed (#777), so a new one does too.
    asks_model = bool(for_ai or read.due_soon or read.event_tomorrow or _new_events(cfg, state, read.direct_events))
    if asks_model:
        if options.preview:
            _preview_step("writing")
        try:
            # Pass direct_events so the LLM doesn't duplicate them into calendar_events.
            already_captured = [e.to_dict() for e in read.direct_events]
            brief, notes = summarize(cfg, for_ai, upcoming, already_captured, read.earlier,
                                     budget=PREVIEW_BUDGET if options.preview else llm.CALL_BUDGET,
                                     placeholders=placeholders, now=read.started)
        except Exception as e:
            log.error("Summarizer failed: %s (falling back to rule-based digest)", e)
            brief, notes = Brief(), RunNotes(error=str(e))
    else:
        brief, notes = Brief(), RunNotes()
    asleep = not cfg.hosted and watch.asleep()  # the server doesn't sleep, nor is it "your computer"
    if asleep:
        log.warning("The computer slept for %d min during tonight's run, so the Brief says it was finished late",
                    watch.slept() // 60)
    if notes.error is not None:
        brief = _fallback_brief(for_ai, notes.error, t, cfg, asleep)

    notes.coverage = read.coverage  # kept in the archive for later checks
    if held:  # the messages in the archive that the AI never saw
        notes.held_back = [m.external_id for m in held]
    model_events = extract_calendar_events(brief, cfg.timezone, t.untitled)
    calendar = _write_calendar(cfg, io, state, model_events + read.direct_events, sends, google_ready)
    date_str = read.today
    form = feedback.Links(cfg, date_str, _form_people(cfg, placeholders, for_ai, read.earlier)) \
        if cfg.feedback.active() else None
    night = render.Night(
        date=date_str, tz=cfg.timezone, t=t, assistant=llm.backend(cfg).display_name, brief=brief,
        coverage=read.coverage, created=calendar.created, held_back=_held_back(held),
        calendar_problem=calendar.problem, ics_events=calendar.ics_events,
        # The rule-based fallback is a raw message list the model never wrote, and a night with only
        # Source events listed before or Held-back Messages asks no model: nothing to judge and no model to credit.
        model_wrote=asks_model and notes.error is None, form=form,
        stamp=datetime.now(timezone.utc), incomplete=notes.incomplete, write_to=email_action.write_to(cfg),
        hosted=cfg.hosted, slept=asleep, caught_up=caught_up)
    return _Written(night, notes, for_ai, held, model_events, placeholders)


def _form_people(cfg: Config, placeholders: ai_filter.Placeholders | None, for_ai: list[Message],
                 earlier: list[EarlierBrief]) -> ai_filter.Placeholders:
    """The placeholders the pilot feedback links carry for other people: tonight's, as the AI saw
    them. With the AI filter off, new ones for the same people, since the links go to the team."""
    if placeholders is not None:
        return placeholders
    people = ai_filter.Placeholders()
    add_the_nights_people(cfg, people, [m.to_dict() for m in for_ai], earlier)
    return people


def _versions(cfg: Config, written: _Written) -> list[render.Version]:
    """One version of tonight's Brief per language among the Recipients, the original first.
    Everything the Household shares (Google Calendar, the archive, feedback text) stays the
    original's (ADR 0004). Translations use tonight's placeholders."""
    brief, error = written.night.brief, written.notes.error
    original = cfg.brief_language()

    def translate_into(language: Language) -> Brief | None:
        return None if error is not None else translate(cfg, brief, original, language, written.placeholders,
                                                        written.night.date)

    def in_language(language: Language, to: list[str], translated: Brief | None) -> render.Version:
        t = languages.text(cfg, language)
        if translated is None:
            # The model is down, so there is nothing to translate; the raw list comes in their words.
            assert error is not None
            return render.Version(t, to, _fallback_brief(written.for_ai, error, t, cfg, written.night.slept))
        events = extract_calendar_events(translated, cfg.timezone, t.untitled)
        return render.Version(t, to, translated, {
            (ev.title, ev.start_iso()): tev for ev, tev in zip(written.model_events, events)})

    return per_language(
        "the Brief", original, cfg.brief_recipients_by_language(), translate_into, in_language,
        lambda to, note: render.Version(languages.text(cfg, original), to, brief, translation_note=note),
        lambda language: languages.text(cfg, language).translation_failed)


# ── The setup page's preview

def _preview_step(step: str, **more: object) -> None:
    """How far `run --preview` has got, as one line of JSON for the setup page."""
    print(json.dumps({"preview": step, **more}, ensure_ascii=False), flush=True)


def _log_preview(cfg: Config, coverage: dict[str, dict], held: list[Message], written: _Written) -> None:
    """One line about the setup page's Brief in the evening job's log, which a preview otherwise
    leaves no trace in, so a "my first Brief looks wrong" report can be looked into afterwards.
    Counts only: no message text, names or subjects, and no error text, which can name an address."""
    read = ", ".join(f"{s} failed" if c["error"] else f"{s} {c['count']}" for s, c in coverage.items())
    brief, created = written.night.brief, written.night.created
    line = (f"Setup preview: read {read or 'nothing'}; {len(held)} held back; "
            f"Digest {len(brief.digest)} chars, "
            f"{sum(len(k.notices) for k in brief.kids)} Notices, "
            f"{sum(len(k.action_items) for k in brief.kids)} Action Items, {len(created)} new events")
    if written.notes.error is not None:
        line += "; the model failed, so it lists the messages"
    log.info(line)
    # The page's run sends its own output to the page, which drops it, so the line goes to the
    # evening job's log as well (`jobs.Job`'s <subcommand>-stderr.log), dated, to be read days later.
    path = cfg.archive.resolved_dir() / "logs" / "run-stderr.log"
    try:
        if not path.parent.is_dir():  # before the evening job is installed
            path.parent.mkdir(parents=True, exist_ok=True)
            private_files.tighten(cfg)
        with path.open("a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} INFO {log.name} | {line}\n")
    except OSError as e:
        log.warning("Couldn't add the preview's line to %s: %s", path, e)


# ── The run

def _send(cfg: Config, io: IO, channel: str, make: Callable[[], render.Outgoing]) -> render.Outgoing | None:
    """Make one mail or iMessage and send it, giving what was sent. A failure, making it or
    sending it, is logged, and the others still go."""
    try:
        item = make()
        if isinstance(item, render.Mail):
            io.send_mail(cfg, item)
        else:
            io.send_imessage(item.to, item.text)
        return item
    except Exception as e:
        log.error("%s send failed: %s", channel, e)
        return None


def _send_nothing_read(cfg: Config, io: IO, read: _Read) -> None:
    """Tell each Recipient, in their own language, that no Source could be read tonight. There is
    nothing to retry if it doesn't get out: the next night that fails says it again."""
    languages.prepare_each(cfg, cfg.brief_languages())
    readers = [(languages.text(cfg, language), to)
               for language, to in cfg.brief_recipients_by_language().items()] if cfg.email.enabled else []
    for item in render.nothing_read(read.coverage, read.today, llm.backend(cfg).display_name,
                                    languages.text(cfg, cfg.brief_language()), readers, email_action.write_to(cfg),
                                    cfg.imessage.recipients if cfg.sends_imessage() else [], cfg.hosted):
        _send(cfg, io, "email" if isinstance(item, render.Mail) else "imessage", lambda: item)


def _deliver(cfg: Config, io: IO, state: State, written: _Written) -> tuple[bool, bool, bool]:
    """Send tonight's Brief to each Recipient: whether an email went out, whether anyone got it,
    and whether everyone did. Delivered once any enabled channel gets it out; with none enabled
    there is nothing to retry."""
    versions = _versions(cfg, written) if cfg.email.enabled else []
    night = written.night
    # Each mail is made as it goes, so one that can't be made leaves the others to go out.
    mails = [("email", lambda v=v: render.mail(night, v)) for v in versions]
    imessages = [("imessage", lambda: render.imessage(night, cfg.imessage.recipients))] \
        if cfg.sends_imessage() else []
    email_sent, everyone = False, True
    delivered = not (cfg.email.enabled or cfg.sends_imessage())
    for channel, make in mails + imessages:
        sent = _send(cfg, io, channel, make)
        everyone = everyone and sent is not None
        if sent:
            email_sent = email_sent or channel == "email"
            delivered = True
            state.mark_delivered_now(sent.to)
    return email_sent, delivered, everyone


def _quiet_night(state: State, coverage: dict[str, dict], sends: bool) -> _Ended:
    """The end of a night that sends no Brief: it still ran, and read what it read."""
    state.mark_run_now()
    state.mark_caught_up_now(read=coverage, behind=_behind(coverage))
    if sends:
        state.save()
        return _Ended(0, "quiet")
    return _Ended(0)


@dataclass(frozen=True)
class _Ended:
    """How a run ended: its exit code, and `night`: "sent" when its Brief reached a Recipient,
    "quiet" when it decided to send nothing, None for a dry run or a Brief that went nowhere."""
    code: int
    night: Literal["sent", "quiet"] | None = None


def run(cfg: Config, options: Options, io: IO, caught_up: bool = False) -> int:
    """Tonight's evening run on `cfg`, which it leaves as it was given. `caught_up`: the scheduler
    started it late, for an evening the computer was off, asleep or signed out at (#894)."""
    return _run(cfg, options, io, caught_up).code


def _run(cfg: Config, options: Options, io: IO, caught_up: bool) -> _Ended:
    # The evening the run started, kept if it ends tomorrow, and whether the computer slept since.
    watch, started = slept.Watch(), datetime.now(ZoneInfo(cfg.timezone))
    cfg = cfg.model_copy(deep=True)  # tonight's lookbacks are tonight's alone
    if options.lookback_hours is not None:
        for _, source_cfg in _windows(cfg):
            source_cfg.lookback_hours = options.lookback_hours
        log.info("Lookback overridden to %d hours (all sources)", options.lookback_hours)
    private_files.tighten(cfg)
    state = io.load_state(cfg.resolved_state_path())
    if options.lookback_hours is None:
        catch_up_lookback(cfg, state)

    read = _read(cfg, options, io, state, started)
    tonight = plan(options, anything_new=read.anything_new,
                   nothing_could_be_read=_nothing_could_be_read(cfg, read.coverage))
    if tonight.ending == "nothing_read":
        log.warning("No Source could be read; sending a short Brief that says so")
        if tonight.sends:
            _send_nothing_read(cfg, io, read)
        else:
            log.info("DRY-RUN: body:\n%s", render.nothing_read_text(
                read.coverage, languages.text(cfg, cfg.brief_language()), llm.backend(cfg).display_name))
    if tonight.ending != "brief":
        if tonight.ending == "quiet":
            log.info("Nothing new; exiting.")
        return _quiet_night(state, read.coverage, tonight.sends)

    written = _write(cfg, options, io, state, read, tonight.sends, watch, caught_up)
    night = written.night
    tonight = plan(options, anything_new=True, nothing_could_be_read=_nothing_could_be_read(cfg, read.coverage),
                   nothing_to_show=render.nothing_to_show(night))
    if tonight.ending == "quiet":
        log.info("Nothing to tell the family tonight; no Brief sent.")
        return _quiet_night(state, read.coverage, tonight.sends)
    if tonight.shows:
        _preview_step("made", **render.preview(night))
        _log_preview(cfg, read.coverage, written.held, written)
    if not tonight.sends:
        # Dry runs leave no trace but a preview's log line: no archive, no state, no email.
        body = night.text()
        log.info("DRY-RUN: body (%d chars):\n%s", len(body), body)
        return _Ended(0)

    email_sent, delivered, everyone = _deliver(cfg, io, state, written)
    # Only mark .ics events as sent once the email actually went out, so a failed send retries tomorrow.
    if email_sent:
        for e in night.ics_events:
            state.mark_event_created(calendar_action.event_hash(e), "ics")
    if delivered:
        state.mark_caught_up_now(read=read.coverage, behind=_behind(read.coverage))
    else:
        # Nobody saw tonight's Brief: leave its Messages unseen so tomorrow's Brief picks them up.
        log.warning("Brief not delivered; its messages will be collected again next run")
        state.forget_new_seen_messages()
    # Saved before the archive, so a run that sent the Brief and then failed isn't caught up with a
    # second Brief (#776). The failure still shows in the log and last-run.json.
    state.mark_run_now()
    state.save()
    md_path = io.write_archive(cfg, night.date, read.messages, night.brief, written.notes, night.created, delivered)
    log.info("Archive written to %s", md_path)
    # A first Brief a Recipient didn't get fails, so the sign-up isn't said to have sent it.
    return _Ended(1 if options.first and not everyone else 0, "sent" if delivered else None)


# A start by the scheduler this long before the Brief's time still counts as at it: a clock set
# back as the job starts, or a scheduler a moment early.
EARLY = timedelta(minutes=5)


def _latest_evening(cfg: Config, now: datetime) -> datetime:
    """The evening run's latest time at or before `now`, in the Household's zone."""
    evening = now.replace(hour=cfg.schedule.daily_hour, minute=cfg.schedule.daily_minute,
                          second=0, microsecond=0)
    return evening - timedelta(days=1) if evening > now else evening


@dataclass(frozen=True)
class _SinceEvening:
    """How long ago the evening run's latest time, in the Household's zone, went by, and whether a
    run has been since. Before the first run, since the evening job was installed: it is installed
    with its first start still to come. A run that sent the Brief counts, even if a later step
    failed."""
    ago: timedelta
    ran: bool

    @property
    def missed(self) -> timedelta | None:
        """How long ago the evening went by with no run since; None when a run has been."""
        return None if self.ran else self.ago


def _since_evening(cfg: Config, state: State) -> _SinceEvening | None:
    """None when the state doesn't say when the last run was."""
    last = state.last_run_at or state.scheduled_at
    if last is None:  # installed by a version that didn't note when
        return None
    now = datetime.now(ZoneInfo(cfg.timezone))
    # A start just before the Brief's time is that evening's: without this the latest evening gone
    # by is yesterday's, which did run, and tonight's Brief would be skipped.
    evening = _latest_evening(cfg, now + EARLY)
    return _SinceEvening(max(now - evening, timedelta(0)), ran=last >= evening)


# A missed evening is caught up only this long after its time (#894): later, the next evening's
# run is near, and its lookback covers the missed one anyway.
CATCH_UP_LIMIT = timedelta(hours=10)
# A start by the scheduler this long after the Brief's time is still the evening's own, not a
# catch-up: a computer woken for the Brief, or the network it waits for, takes a while.
ON_TIME = timedelta(minutes=30)
# A start by hand through the scheduler (`schedule run-now`) leaves this file beside the state;
# the run it starts takes it, if it is this fresh, as a run by hand.
BY_HAND = "run-now"
BY_HAND_FRESH = timedelta(minutes=10)


def by_hand_path(cfg: Config) -> Path:
    """Where `schedule run-now` leaves its note for the run it starts."""
    return cfg.resolved_state_path().with_name(BY_HAND)


def mark_started_by_hand(cfg: Config) -> None:
    """Notes that a person is starting the evening job now, through the scheduler, so that run
    runs at any hour and isn't a catch-up."""
    path = by_hand_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")


def forget_started_by_hand(cfg: Config) -> None:
    """Takes back mark_started_by_hand's note, when the job wasn't started."""
    by_hand_path(cfg).unlink(missing_ok=True)


def _started_by_hand(cfg: Config) -> bool:
    """Whether `schedule run-now` started this run, a few minutes ago. Its note is used up."""
    path = by_hand_path(cfg)
    try:
        noted = path.read_text(encoding="utf-8").strip()
        path.unlink()
        at = datetime.fromisoformat(noted)
    except (OSError, ValueError):
        return False
    return abs(datetime.now(timezone.utc) - at) < BY_HAND_FRESH


def run_unless_caught_up(cfg: Config, options: Options, io: IO,
                         then: Callable[[], object] | None = None) -> int:
    """`run`, or with `if_missed` only when the evening run was missed (#386). A start by the
    scheduler (`scheduled`, or `if_missed`, the Windows sign-in task's) well after the Brief's time
    with no run since catches up the missed evening, with a line that says so, but only within
    CATCH_UP_LIMIT of it; after that it is left to the next evening run (#894). One that finds a
    run since, such as Windows' late start of a missed task after its sign-in task caught the
    evening up, does nothing. Checked under the run lock, so a catch-up started beside the evening
    run sees the state that run left. A run that isn't a dry run notes how it ended in
    last-run.json, a crash as exit 1, with how long it took."""
    caught_up = False
    by_hand = options.scheduled and _started_by_hand(cfg)
    if options.if_missed or (options.scheduled and not by_hand and not cfg.hosted):
        since = _since_evening(cfg, io.load_state(cfg.resolved_state_path()))
        missed = since.missed if since else None
        if (options.if_missed and missed is None) or (since and since.ran):
            log.info("The evening run has run since its last time, so there is nothing to catch up")
            return 0
        if missed is not None and missed > ON_TIME and not cfg.hosted:
            if missed >= CATCH_UP_LIMIT:
                log.info("The evening run was missed %d hours ago, too long ago to catch up (%d hours "
                         "or more), so it is left to the next evening run, which covers it",
                         missed // timedelta(hours=1), CATCH_UP_LIMIT // timedelta(hours=1))
                return 0
            caught_up = True
            log.info("The evening run was missed, so it runs now")
    if options.dry_run:
        return run(cfg, options, io, caught_up)
    started = time.monotonic()
    try:
        ended = _run(cfg, options, io, caught_up)
    except BaseException:
        last_run.record(cfg, 1, time.monotonic() - started, wilma_egress.causes())
        raise
    last_run.record(cfg, ended.code, time.monotonic() - started, wilma_egress.causes())
    if then is not None and _updates_after(cfg, options, by_hand, caught_up, ended):
        try:
            then()
        except Exception:  # noqa: BLE001 - the Brief is out; the next evening tries again
            log.exception("The update after the evening run stopped; the run's result stands")
    return ended.code


def _updates_after(cfg: Config, options: Options, by_hand: bool, caught_up: bool,
                   ended: _Ended) -> bool:
    """Whether the program's update follows this run (run_unless_caught_up's `then`)."""
    if not options.scheduled or by_hand or cfg.hosted or options.first or options.preview:
        return False
    return ended.night == "sent" or (ended.night == "quiet" and not caught_up)
