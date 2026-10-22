"""The evening run: it reads the Sources, has the model write the Brief, writes the calendar, sends
each Recipient their version and archives the night, reaching outside the program through `IO`.
What the Brief says is render.py's, and whether tonight sends one and leaves a trace is plan()'s."""
from __future__ import annotations

import json
import logging
import math
import re
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Literal

from . import ai_filter, feedback, gateway, languages, llm, private_files, render
from . import sources as source_registry
from .actions import archive, calendar as calendar_action, email as email_action, imessage
from .brief_text import BriefText
from .collectors.base import CalendarEvent, Message
from .config import Config
from .state import State
from .summarize import add_the_nights_people, extract_calendar_events, for_the_ai, placeholders_for, summarize
from .translate import translate
from .utils.dates import today_str

log = logging.getLogger("family_brief")


@dataclass(frozen=True)
class Options:
    """How `parent-recap run` was asked to run. A preview is a dry run that prints, for the setup
    page, how far it is and then the first Recipient's email."""
    dry_run: bool = False
    preview: bool = False
    sources: list[str] | None = None  # the connected ones when None
    lookback_hours: int | None = None  # each Source's own, caught up, when None
    if_missed: bool = False


@dataclass(frozen=True)
class IO:
    """The ports the evening run reaches the world through, other than the Sources and the model:
    the mailer, iMessage, Google Calendar, the archive, the state and the network it waits for.
    `real()` gives the program's own."""
    send_mail: Callable[[render.Mail], None]
    send_imessage: Callable[[list[str], str], None]
    calendar_ready: Callable[[], bool]
    upcoming_events: Callable[[Config, int], list[dict]]
    create_events: Callable[[Config, State, list[CalendarEvent], list[dict]], None]
    recent_points: Callable[[Config, str], list[dict]]
    write_archive: Callable[[Config, str, list[Message], dict, list[dict], bool], Path]
    load_state: Callable[[Path], State]
    wait_for_network: Callable[[str], None]

    @classmethod
    def real(cls) -> IO:
        return cls(
            send_mail=lambda m: email_action.send(subject=m.subject, body_text=m.text, body_html=m.html,
                                                  from_addr=m.from_addr, to_addrs=m.to,
                                                  attachments=m.attachments),
            send_imessage=lambda to, text: imessage.send(to, text),
            calendar_ready=calendar_action.is_configured,
            upcoming_events=calendar_action.get_upcoming_events,
            create_events=calendar_action.create_events,
            recent_points=lambda cfg, today: archive.recent_points(cfg, today),
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
    Collectors often log and return nothing rather than raise (e.g. WhatsApp permission)."""

    def __init__(self) -> None:
        super().__init__(logging.ERROR)
        self.first: dict[str, str] = {}

    def emit(self, record: logging.LogRecord) -> None:
        self.first.setdefault(record.name.rsplit(".", 1)[-1], record.getMessage())


def collect(cfg: Config, state: State, sources: list[str],
            coverage: dict[str, dict] | None = None,
            reading: Callable[[str], None] | None = None) -> tuple[list[Message], list[CalendarEvent]]:
    """Run each collector, isolating failures. If `coverage` is given, it is filled with
    {source: {"count": n, "error": first error or None}} for the brief's coverage line.
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
            count, error = 0, None
            try:
                got = record.collect(cfg, state, kid_terms)
                log.info("%s: collected %d messages, %d events", source, len(got.messages), len(got.events))
                messages.extend(got.messages)
                # A feed gives UTC; the Brief and the model's already-queued list use local time.
                direct_events.extend(e.in_zone(cfg.timezone) for e in got.events)
                count = len(got.events if record.counts_events else got.messages)
            except Exception as e:
                log.error("%s collector failed: %s\n%s", source, e, traceback.format_exc())
                error = str(e)
                # Its result is dropped, so the Messages it read before failing must stay unseen.
                state.forget_new_seen_messages(source)
            if coverage is not None:
                coverage[source] = {"count": count, "error": error or errors.first.get(source)}
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


def _catch_up_lookback(cfg: Config, state: State) -> None:
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
    import time
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
    """What tonight's run read: the Sources' Messages and events, what each Source gave
    (`coverage`), and the earlier Briefs' points, some perhaps due soon."""
    messages: list[Message]
    direct_events: list[CalendarEvent]
    coverage: dict[str, dict]
    earlier: list[dict]
    due_soon: bool

    @property
    def anything_new(self) -> bool:
        return bool(self.messages or self.direct_events or self.due_soon)


def _read(cfg: Config, options: Options, io: IO, state: State) -> _Read:
    sources = options.sources or cfg.default_sources()
    log.info("Running collectors: %s (dry_run=%s)", sources, options.dry_run or options.preview)
    io.wait_for_network(llm.backend(cfg).probe_host)
    coverage: dict[str, dict] = {}
    if options.preview:
        _preview_step("reading", sources=[s.name for s in source_registry.ALL if s.name in sources])
    messages, direct_events = collect(
        cfg, state, sources, coverage,
        reading=(lambda source: _preview_step("reading", source=source)) if options.preview else None)
    earlier = io.recent_points(cfg, today_str(cfg.timezone))
    return _Read(messages, direct_events, coverage, earlier,
                 archive.has_due_soon(earlier, today_str(cfg.timezone)))


# ── Writing the Brief

# How long `run --preview` tries a busy model for: the setup page gives up on the whole preview
# after 15 minutes (setup_server.Timeouts.brief), reading the Sources included.
PREVIEW_BUDGET = 10 * 60


def _fallback_summary(messages: list[Message], err: str, t: BriefText, cfg: Config) -> dict:
    """Rule-based digest when the LLM call fails. Never leaks the raw error to iMessage."""
    # Group by (kid_hint or 'unknown', source)
    buckets: dict[tuple[str, str], list[Message]] = {}
    for m in messages:
        key = (cfg.kid_called(m.kid_hint) or t.unsorted, m.source)
        buckets.setdefault(key, []).append(m)

    # A Gateway Key out of credit for the day is no fault to look into: it says so, and when it ends.
    lines = [t.fallback_spent if gateway.spent(err) else t.fallback_header, ""]
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
    return {
        "per_kid": [],
        "calendar_events": [],
        "message_digest": "\n".join(lines),
        "_llm_error": err,
    }


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


def _as_created(events: list) -> list[dict]:
    """Events the Brief lists without a Google write, shaped like `create_events` results."""
    return [{"title": e.title, "start": e.start_iso(), "kid": e.kid} for e in events]


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
    """Tonight's Brief as the model wrote it, with what the run needs to send it."""
    night: render.Night
    for_ai: list[Message]
    held: list[Message]
    model_events: list[CalendarEvent]
    placeholders: ai_filter.Placeholders | None


def _write(cfg: Config, options: Options, io: IO, state: State, read: _Read, sends: bool) -> _Written:
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
    if for_ai or read.due_soon:
        if options.preview:
            _preview_step("writing")
        try:
            # Pass direct_events so the LLM doesn't duplicate them into calendar_events.
            already_captured = [e.to_dict() for e in read.direct_events]
            summary = summarize(cfg, for_ai, upcoming, already_captured, read.earlier,
                                budget=PREVIEW_BUDGET if options.preview else llm.CALL_BUDGET,
                                placeholders=placeholders)
        except Exception as e:
            log.error("Summarizer failed: %s (falling back to rule-based digest)", e)
            summary = _fallback_summary(for_ai, str(e), t, cfg)
    else:
        summary = {"per_kid": [], "calendar_events": [], "message_digest": ""}

    summary["_coverage"] = read.coverage  # kept in the archive for later checks
    if held:  # the messages in the archive that the AI never saw
        summary["_held_back"] = [m.external_id for m in held]
    model_events = extract_calendar_events(summary, cfg.timezone, t.untitled)
    calendar = _write_calendar(cfg, io, state, model_events + read.direct_events, sends, google_ready)
    date_str = today_str(cfg.timezone)
    form = feedback.Links(cfg, date_str, _form_people(cfg, placeholders, for_ai, read.earlier)) \
        if cfg.feedback.active() else None
    night = render.Night(
        date=date_str, tz=cfg.timezone, t=t, assistant=llm.backend(cfg).display_name, summary=summary,
        coverage=read.coverage, created=calendar.created, held_back=_held_back(held),
        calendar_problem=calendar.problem, ics_events=calendar.ics_events,
        # The rule-based fallback is a raw message list the model never wrote, and a night with only
        # MyClub events or Held-back Messages asks no model: nothing to judge and no model to credit.
        model_wrote=bool(for_ai or read.due_soon) and "_llm_error" not in summary, form=form)
    return _Written(night, for_ai, held, model_events, placeholders)


def _form_people(cfg: Config, placeholders: ai_filter.Placeholders | None, for_ai: list[Message],
                 earlier: list[dict]) -> ai_filter.Placeholders:
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
    summary = written.night.summary
    original = cfg.brief_language()
    versions = []
    for language, to in (cfg.brief_recipients_by_language() or {original: []}).items():
        t = languages.text(cfg, language)
        if language == original:
            versions.append(render.Version(t, to, summary))
        elif "_llm_error" in summary:
            # The model is down, so there is nothing to translate; the raw list comes in their words.
            versions.append(render.Version(t, to, _fallback_summary(written.for_ai, summary["_llm_error"], t, cfg)))
        else:
            try:
                translated = translate(cfg, summary, original, language, written.placeholders)
            except Exception as e:
                log.error("Translating the Brief into %s failed: %s (sending the original)", language, e)
                versions.append(render.Version(languages.text(cfg, original), to, summary,
                                               translation_note=t.translation_failed))
                continue
            in_language = extract_calendar_events(translated, cfg.timezone, t.untitled)
            versions.append(render.Version(t, to, translated, {
                (ev.title, ev.start_iso()): tev for ev, tev in zip(written.model_events, in_language)}))
    return versions


# ── The setup page's preview

def _preview_step(step: str, **more: object) -> None:
    """How far `run --preview` has got, as one line of JSON for the setup page."""
    print(json.dumps({"preview": step, **more}, ensure_ascii=False), flush=True)


def _log_preview(cfg: Config, coverage: dict[str, dict], held: list[Message], summary: dict,
                 created: list[dict]) -> None:
    """One line about the setup page's Brief in the evening job's log, which a preview otherwise
    leaves no trace in, so a "my first Brief looks wrong" report can be looked into afterwards.
    Counts only: no message text, names or subjects, and no error text, which can name an address."""
    read = ", ".join(f"{s} failed" if c["error"] else f"{s} {c['count']}" for s, c in coverage.items())
    kids = summary.get("per_kid") or []
    line = (f"Setup preview: read {read or 'nothing'}; {len(held)} held back; "
            f"Digest {len(summary.get('message_digest') or '')} chars, "
            f"{sum(len(k.get('notices') or []) for k in kids)} Notices, "
            f"{sum(len(k.get('action_items') or []) for k in kids)} Action Items, {len(created)} new events")
    if "_llm_error" in summary:
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

def _send(io: IO, item: render.Outgoing) -> bool:
    """Send one mail or iMessage; a failure is logged, and the others still go."""
    try:
        if isinstance(item, render.Mail):
            io.send_mail(item)
        else:
            io.send_imessage(item.to, item.text)
        return True
    except Exception as e:
        log.error("%s send failed: %s", "email" if isinstance(item, render.Mail) else "imessage", e)
        return False


def _send_nothing_read(cfg: Config, io: IO, read: _Read) -> None:
    """Tell each Recipient, in their own language, that no Source could be read tonight. There is
    nothing to retry if it doesn't get out: the next night that fails says it again."""
    languages.prepare_each(cfg, cfg.brief_languages())
    readers = [(languages.text(cfg, language), to)
               for language, to in cfg.brief_recipients_by_language().items()] if cfg.email.enabled else []
    for item in render.nothing_read(read.coverage, today_str(cfg.timezone), llm.backend(cfg).display_name,
                                    languages.text(cfg, cfg.brief_language()), readers, _from_addr(cfg),
                                    cfg.imessage.recipients if cfg.imessage.enabled else []):
        _send(io, item)


def _from_addr(cfg: Config) -> str:
    return cfg.email.from_addr or cfg.gmail.username or ""


def _deliver(cfg: Config, io: IO, state: State, written: _Written) -> tuple[bool, bool]:
    """Send tonight's Brief to each Recipient: whether an email went out, and whether anyone got
    it. Delivered once any enabled channel gets it out; with none enabled there is nothing to retry."""
    versions = _versions(cfg, written) if cfg.email.enabled else []
    email_sent = False
    delivered = not (cfg.email.enabled or cfg.imessage.enabled)
    for item in render.brief(written.night, versions, _from_addr(cfg),
                             cfg.imessage.recipients if cfg.imessage.enabled else []):
        if _send(io, item):
            email_sent = email_sent or isinstance(item, render.Mail)
            delivered = True
            state.mark_delivered_now(item.to)
    return email_sent, delivered


def _quiet_night(state: State, coverage: dict[str, dict], sends: bool) -> int:
    """The end of a night that sends no Brief: it still ran, and read what it read."""
    state.mark_run_now()
    state.mark_caught_up_now(read=coverage, behind=_behind(coverage))
    if sends:
        state.save()
    return 0


def run(cfg: Config, options: Options, io: IO) -> int:
    """Tonight's evening run on `cfg`, which it leaves as it was given."""
    cfg = cfg.model_copy(deep=True)  # tonight's lookbacks are tonight's alone
    if options.lookback_hours is not None:
        for _, source_cfg in _windows(cfg):
            source_cfg.lookback_hours = options.lookback_hours
        log.info("Lookback overridden to %d hours (all sources)", options.lookback_hours)
    private_files.tighten(cfg)
    state = io.load_state(cfg.resolved_state_path())
    if options.lookback_hours is None:
        _catch_up_lookback(cfg, state)

    read = _read(cfg, options, io, state)
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

    written = _write(cfg, options, io, state, read, tonight.sends)
    night = written.night
    tonight = plan(options, anything_new=True, nothing_could_be_read=_nothing_could_be_read(cfg, read.coverage),
                   nothing_to_show=render.nothing_to_show(night.summary, night.created, night.held_back,
                                                          night.t, night.assistant))
    if tonight.ending == "quiet":
        log.info("Nothing to tell the family tonight; no Brief sent.")
        return _quiet_night(state, read.coverage, tonight.sends)
    if tonight.shows:
        _preview_step("made", **render.preview(night))
        _log_preview(cfg, read.coverage, written.held, night.summary, night.created)
    if not tonight.sends:
        # Dry runs leave no trace but a preview's log line: no archive, no state, no email.
        body = night.text()
        log.info("DRY-RUN: body (%d chars):\n%s", len(body), body)
        return 0

    email_sent, delivered = _deliver(cfg, io, state, written)
    md_path = io.write_archive(cfg, night.date, read.messages, night.summary, night.created, delivered)
    log.info("Archive written to %s", md_path)

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
    state.mark_run_now()
    state.save()
    return 0


def _evening_missed(cfg: Config, state: State) -> bool:
    """Whether the evening run's latest time has gone by with no run since, on the PC's own clock,
    which the scheduler keeps. Before the first run, since the evening job was installed: it is
    installed with its first start still to come."""
    last = state.last_run_at or state.scheduled_at
    if last is None:  # installed by a version that didn't note when
        return False
    now = datetime.now().astimezone()
    evening = now.replace(hour=cfg.schedule.daily_hour, minute=cfg.schedule.daily_minute,
                          second=0, microsecond=0)
    if evening > now:
        evening -= timedelta(days=1)
    return last < evening


def run_unless_caught_up(cfg: Config, options: Options, io: IO) -> int:
    """`run`, or with `if_missed` only when the evening run was missed (#386). Checked under the
    run lock, so a catch-up started beside the evening run sees the state that run left."""
    if options.if_missed:
        if not _evening_missed(cfg, io.load_state(cfg.resolved_state_path())):
            log.info("The evening run has run since its last time, so there is nothing to catch up")
            return 0
        log.info("The evening run was missed, so it runs now")
    return run(cfg, options, io)
