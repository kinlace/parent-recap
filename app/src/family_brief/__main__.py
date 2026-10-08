from __future__ import annotations

import argparse
import json
import logging
import math
import re
import sys
import traceback
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Literal
from zoneinfo import ZoneInfo

from .actions import archive, calendar as calendar_action, email as email_action, ics as ics_action, imessage
from .collectors import (
    gmail as gmail_collector,
    myclub as myclub_collector,
    whatsapp as whatsapp_collector,
    wilma as wilma_collector,
)
from . import ai_filter, feedback, languages, private_files, run_lock
from .brief_text import PRODUCT_NAME, BriefText
from .collectors.base import UNREADABLE_MESSAGE, CalendarEvent, Message
from .config import Config
from .state import State
from .summarize import (CALL_BUDGET, add_the_nights_people, digest_of, extract_calendar_events, for_the_ai,
                        placeholders_for, summarize)
from .translate import translate
from .utils.dates import to_local, today_str


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("family_brief")


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


_COLLECTED = ("gmail", "myclub", "wilma", "whatsapp")  # the order the collectors run in


def _run_collectors(cfg: Config, state: State, sources: list[str],
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
        for source in _COLLECTED:
            if source not in sources:
                continue
            if reading:
                reading(source)
            count, error = 0, None
            try:
                if source == "myclub":
                    evs, msgs = myclub_collector.collect_events(cfg, state)
                    log.info("myclub: %d events, %d messages", len(evs), len(msgs))
                    # The feed gives UTC; the Brief and the model's already-queued list use local time.
                    direct_events.extend(e.in_zone(cfg.timezone) for e in evs)
                    count = len(evs)
                else:
                    collector = {"gmail": gmail_collector, "wilma": wilma_collector,
                                 "whatsapp": whatsapp_collector}[source]
                    msgs = collector.collect(cfg, state, kid_terms)
                    log.info("%s: collected %d messages", source, len(msgs))
                    count = len(msgs)
                messages.extend(msgs)
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


_SOURCE_NAMES = {"gmail": "Gmail", "wilma": "Wilma", "whatsapp": "WhatsApp", "myclub": "MyClub",
                 "calendar": "Calendar"}


def _problem(err: str) -> Literal["unreadable", "permission", "login", "other"]:
    """What kind of error a Source reported, for what the Brief says about it."""
    if err.startswith(UNREADABLE_MESSAGE):
        return "unreadable"
    low = err.lower()
    if "operation not permitted" in low or "permission" in low:
        return "permission"
    if "authenticationfailed" in low or "invalid credentials" in low or "login" in low:
        return "login"
    return "other"


def _short_reason(source: str, err: str, t: BriefText) -> str:
    # WhatsApp's permission is lost when Parent Recap's own Python changes (ADR 0011).
    permission = t.whatsapp_permission_denied if source == "whatsapp" else t.permission_denied
    return {"unreadable": t.message_unreadable,  # without the Message's id and the error, which are for the log
            "permission": permission,
            "login": t.login_failed}.get(_problem(err)) or err.splitlines()[0][:60]


def _coverage_note(coverage: dict[str, dict], t: BriefText) -> str:
    """One line saying what was read tonight, plus a warning for any source that failed."""
    if not coverage:
        return ""
    read, missed = [], []
    for source, c in coverage.items():
        name = _SOURCE_NAMES.get(source, source)
        read.append(f"{name} {(t.events if source == 'myclub' else t.messages)(c['count'])}")
        if c["error"]:
            missed.append((t.partly_read if c["count"] else t.not_read)
                          .format(source=name, reason=_short_reason(source, c["error"], t)))
    note = t.read_tonight.format(counts=" · ".join(read))
    if missed:
        note += "\n" + t.may_be_incomplete.format(missed=t.missed_sep.join(missed))
    return note


def _nothing_read(coverage: dict[str, dict], t: BriefText, assistant: str) -> str:
    """The short Brief of a night when no Source could be read: each Source's problem and its fix."""
    lines = [t.nothing_read]
    for source, c in coverage.items():
        name = _SOURCE_NAMES.get(source, source)
        fix = {"login": t.fix_login, "permission": t.fix_permission}.get(_problem(c["error"]), t.fix_other)
        lines.append(t.source_problem.format(source=name, reason=_short_reason(source, c["error"], t).rstrip(".。 "),
                                             fix=fix.format(assistant=assistant, source=name)))
    return "\n".join(lines)


def _send_nothing_read(cfg: Config, coverage: dict[str, dict], date_str: str, assistant: str) -> None:
    """Tell each Recipient, in their own language, that no Source could be read tonight. There is
    nothing to retry if it doesn't get out: the next night that fails says it again."""
    import html as _h

    def text(t: BriefText) -> str:
        return f"👨‍👩‍👧‍👦 {PRODUCT_NAME} {_header_date(date_str, t)}\n\n" + _nothing_read(coverage, t, assistant)
    if cfg.email.enabled:
        for language, to in cfg.brief_recipients_by_language().items():
            t = languages.text(cfg, language)
            note = _h.escape(_nothing_read(coverage, t, assistant)).replace("\n", "<br>")
            try:
                email_action.send(
                    subject=f"{PRODUCT_NAME} · {date_str}",
                    body_text=text(t),
                    body_html="<html><body style='font-family:-apple-system,Helvetica,Arial;font-size:14px'>"
                              f"<h2>👨‍👩‍👧‍👦 {PRODUCT_NAME} · {_h.escape(_header_date(date_str, t))}</h2>"
                              f"<p style='color:#a33'>{note}</p></body></html>",
                    from_addr=cfg.email.from_addr or cfg.gmail.username or "",
                    to_addrs=to,
                )
            except Exception as e:
                log.error("email send failed: %s", e)
    if cfg.imessage.enabled:
        try:
            imessage.send(cfg.imessage.recipients, text(languages.text(cfg, cfg.brief_language())))
        except Exception as e:
            log.error("imessage send failed: %s", e)


MAX_CATCH_UP_HOURS = 7 * 24


def _behind(coverage: dict[str, dict]) -> list[str]:
    """The Sources tonight's run didn't fully read, whose Messages may still be waiting."""
    return [source for source, c in coverage.items() if c["error"]]


def _catch_up_lookback(cfg: Config, state: State) -> None:
    """After undelivered nights, or nights a Source failed, widen each Source's lookback to reach
    back to the last run that was caught up for it, so its Messages fall inside the window again.
    Capped at a week."""
    for source, source_cfg in (("gmail", cfg.gmail), ("whatsapp", cfg.whatsapp), ("wilma", cfg.wilma)):
        caught_up_at = state.caught_up_at(source)
        if caught_up_at is None:
            continue
        gap = (datetime.now(timezone.utc) - caught_up_at).total_seconds() / 3600
        hours = min(math.ceil(gap) + 2, MAX_CATCH_UP_HOURS)  # same 2h slack as the 26h default
        if hours > source_cfg.lookback_hours:
            source_cfg.lookback_hours = hours
            log.info("%s lookback widened to %d hours to cover undelivered nights", source, hours)


def _wait_for_network(timeout_seconds: int = 60, probe_host: str = "api.anthropic.com") -> None:
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


def _ics_candidates(cfg: Config, state: State, events: list) -> list:
    """Events that would go into tonight's .ics attachment. MyClub is usually subscribed to
    directly in the calendar app, so it is skipped unless asked for."""
    return [e for e in events
            if (e.source != "myclub" or cfg.google_calendar.ics_include_myclub)
            and not calendar_action.already_created(state, e)]


def _as_created(events: list) -> list[dict]:
    """Events the Brief lists without a Google write, shaped like `create_events` results."""
    return [{"title": e.title, "start": e.start_iso(), "kid": e.kid} for e in events]


# How long `run --preview` tries a busy model for: the setup page gives up on the whole preview
# after 15 minutes (setup_server.BRIEF_SECONDS), reading the Sources included.
PREVIEW_BUDGET = 10 * 60


def _llm_host(cfg: Config) -> str:
    return "chatgpt.com" if cfg.llm.backend == "codex" else "api.anthropic.com"


def _fallback_summary(messages: list[Message], err: str, t: BriefText, cfg: Config) -> dict:
    """Rule-based digest when the LLM call fails. Never leaks the raw error to iMessage."""
    # Group by (kid_hint or 'unknown', source)
    buckets: dict[tuple[str, str], list[Message]] = {}
    for m in messages:
        key = (cfg.kid_called(m.kid_hint) or t.unsorted, m.source)
        buckets.setdefault(key, []).append(m)

    lines = [t.fallback_header, ""]
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


def _action_items(summary: dict) -> list[dict]:
    """Every Action Item in the Brief, each with its Kid."""
    return [{**a, "kid": k.get("kid", "")}
            for k in summary.get("per_kid", []) or [] for a in k.get("action_items") or []]


def _action_meta(a: dict, t: BriefText) -> str:
    """What the Brief says after an Action Item: its Kid, due date, assignee and Sources."""
    by = _due(str(a.get("by", "")), t)
    kid = str(a.get("kid", ""))
    source = " · ".join(_SOURCE_NAMES.get(s, s) for s in a.get("source") or [])
    return " · ".join(x for x in [f"({kid})" if kid else "", t.due.format(date=by) if by else "",
                                  str(a.get("who", "")), source] if x)


@dataclass(frozen=True)
class _HeldBack:
    """A Held-back Message as the Brief lists it, without the AI (ADR 0013): its Source, sender and
    subject (for WhatsApp, which has none, its group), and the link to read it at its Source."""
    source: str
    sender: str
    subject: str | None
    link: str | None

    def label(self, t: BriefText) -> str:
        subject = self.subject or t.no_subject
        about = [subject, self.sender] if self.source == "whatsapp" else [self.sender, subject]
        return " · ".join(x for x in [self.source_name, *about] if x)

    @property
    def source_name(self) -> str:
        return _SOURCE_NAMES.get(self.source, self.source)


_ADDRESS = re.compile(r"\s*<[^<>]*>$")


def _sender_name(sender: str | None) -> str:
    """A sender as the Brief names them: the name of `Name <address>`, or else the address."""
    sender = (sender or "").strip()
    return _ADDRESS.sub("", sender).strip().strip('"') or sender.strip("<>")


def _held_back(held: list[Message]) -> list[_HeldBack]:
    """Tonight's Held-back Messages as the Brief lists them: Gmail's with the link the Source gives,
    Wilma's with their page in Wilma, and WhatsApp's with none."""
    out = []
    for m in held:
        link = wilma_collector.link(m) if m.source == "wilma" else m.url
        out.append(_HeldBack(m.source, _sender_name(m.sender),
                             m.chat_name if m.source == "whatsapp" else m.subject,
                             link if link and link.startswith("https://") else None))
    return out


def _held_back_lines(held: list[_HeldBack], t: BriefText, assistant: str) -> list[str]:
    """The plain-text Brief's list of Held-back Messages, or nothing on a night without one."""
    if not held:
        return []
    return [t.held_back, t.held_back_note.format(assistant=assistant),
            *(f"• {h.label(t)} · {h.link or t.held_back_read_in.format(source=h.source_name)}" for h in held)]


def _header_date(date_str: str, t: BriefText) -> str:
    """Tonight's date at the top of the Brief; the subject keeps it as YYYY-MM-DD, so threads sort."""
    return t.on(date.fromisoformat(date_str))


def _due(by: str, t: BriefText) -> str:
    """An Action Item's due date as the language writes it, or as the model wrote it if it isn't one."""
    try:
        return t.on(date.fromisoformat(by[:10]))  # the date alone, as the archive reads it
    except ValueError:
        return by


def _start(start: str, t: BriefText, tz: str) -> str:
    """An event's start as the language writes it, in the Household's timezone (which a start
    without an offset is already in, as on the calendar). An all-day event's is the date alone."""
    try:
        if len(start) == 10:
            return t.on(date.fromisoformat(start))
        moment = datetime.fromisoformat(start)
    except ValueError:
        return start[:16]
    return t.at(moment.replace(tzinfo=ZoneInfo(tz)) if moment.tzinfo is None else to_local(moment, tz))


_HEADING = re.compile(r"#{2,3}\s+(?P<title>.+)"
                      r"|\*\*(?P<kid>[^*]+)\*\*(?P<note>\s*[(（][^()（）]*[)）])?")
_POINT = re.compile(r"\s*[-*•]\s+(\S.*)")
_BOLD = re.compile(r"\*\*(.+?)\*\*")


def _digest_html(digest: str) -> str:
    """The Digest's small Markdown subset as HTML: `##`/`###` lines and lines that are only a
    `**Kid**`, perhaps with a note in brackets (the Fallback's count), become headings, `- `
    lines a list, `**bold**` stays bold. Every piece of text is escaped before any tag goes
    around it, so nothing in a Message can inject HTML."""
    import html as _h

    def inline(text: str) -> str:
        return _BOLD.sub(r"<strong>\1</strong>", _h.escape(text.strip()))

    def title(heading: re.Match[str]) -> str:
        if heading["title"]:
            return _BOLD.sub(r"\1", _h.escape(heading["title"].strip()))
        return _h.escape(heading["kid"].strip() + (heading["note"] or ""))
    out: list[str] = []
    block = ""  # the open "p" or "ul", if any
    for line in digest.splitlines() + [""]:
        heading, point = _HEADING.fullmatch(line.strip()), _POINT.fullmatch(line)
        kind = "" if heading or not line.strip() else "ul" if point else "p"
        if block and block != kind:
            out.append(f"</{block}>")
        elif block == "p":
            out.append("<br>")
        if kind and block != kind:
            out.append(f"<{kind}>")
        block = kind
        if heading:
            out.append(f"<h4>{title(heading)}</h4>")
        elif point:
            out.append(f"<li>{inline(point[1])}</li>")
        elif kind:
            out.append(inline(line))
    return "".join(out)


def _daily_brief_html(summary: dict, calendar_created: list[dict],
                      date_str: str, t: BriefText, tz: str, note: str = "", ics_attached: bool = False,
                      coverage: str = "", footer: str = "",
                      feedback_link: Callable[..., str] | None = None,
                      original: dict | None = None, top_note: str = "",
                      held_back: list[_HeldBack] | None = None, assistant: str = "") -> str:
    """Simple HTML rendering of the Brief, safe for Gmail. `summary` is translated from
    `original` for a Recipient in another language; feedback links carry the original's text, so
    the Form gets the same words from every language version (ADR 0004). `top_note` holds the
    warnings shown above the Digest (see _top_note), and `held_back` the Held-back Messages, which
    were not sent to the `assistant`."""
    import html as _h
    original = original or summary

    def form_link(verdict: str, label: str, text: str, **kw) -> str:
        assert feedback_link is not None
        return (f'<a href="{_h.escape(feedback_link(verdict, text, **kw))}"'
                f" style='color:#888;text-decoration:none'>{label}</a>")
    parts = [f"<h2>👨‍👩‍👧‍👦 {PRODUCT_NAME} · {_h.escape(_header_date(date_str, t))}</h2>"]
    if top_note:
        warnings = _h.escape(top_note).replace("\n", "<br>")
        parts.append(f"<p style='color:#a33'>{warnings}</p>")
    digest = digest_of(summary)
    if digest:
        parts.append(_digest_html(digest))
        if feedback_link:
            parts.append("<p><small>"
                         f"{form_link(feedback.DIGEST_WRONG, t.feedback_digest_wrong, digest_of(original))}"
                         "</small></p>")
    if held_back:
        parts.append(f"<h3>{_h.escape(t.held_back)}</h3><p style='color:#666'>"
                     f"{_h.escape(t.held_back_note.format(assistant=assistant))}</p><ul>")
        for h in held_back:
            where = (f'<a href="{_h.escape(h.link)}">{_h.escape(t.held_back_open)}</a>' if h.link
                     else _h.escape(t.held_back_read_in.format(source=h.source_name)))
            parts.append(f"<li>{_h.escape(h.label(t))} · {where}</li>")
        parts.append("</ul>")
    all_actions = _action_items(summary)
    if all_actions:
        parts.append(f"<h3>{t.action_items}</h3><ul>")
        for a, as_written in zip(all_actions, _action_items(original)):
            what = _h.escape(str(a.get("what", "")))
            meta = _h.escape(_action_meta(a, t))
            links = ""
            if feedback_link:
                kw = {"kid": str(as_written.get("kid", "")), "source": as_written.get("source") or []}
                what_text = str(as_written.get("what", ""))
                links = (f" <small>{form_link(feedback.SAVED, t.feedback_saved, what_text, **kw)}"
                         f" · {form_link(feedback.WRONG, t.feedback_wrong, what_text, **kw)}</small>")
            parts.append(f"<li>{what}<small style='color:#666'> {meta}</small>{links}</li>")
        parts.append("</ul>")
    if calendar_created:
        # After a mid-run Google failure the list mixes written events with attached ones.
        mixed = any(ev.get("google_event_id") for ev in calendar_created)
        ics_hint = t.ics_hint_mixed if mixed else t.ics_hint
        parts.append(f"<h3>{t.new_events}</h3>" + (
            f"<p style='color:#666'>{ics_hint}</p>" if ics_attached else "") + "<ul>")
        for ev in calendar_created:
            title = _h.escape(str(ev.get("title", "")))
            start = _h.escape(_start(str(ev.get("start", "")), t, tz))
            kid = _h.escape(str(ev.get("kid") or ""))
            link = ev.get("htmlLink") or ""
            head = f'<a href="{_h.escape(link)}">{title}</a>' if link else title
            parts.append(f"<li>{head} — {start} {('('+kid+')') if kid else ''}</li>")
        parts.append("</ul>")
    if note:
        parts.append(f"<p style='color:#a33'>{_h.escape(note)}</p>")
    if coverage:
        parts.append("<p style='color:#888;font-size:12px'>"
                     + _h.escape(coverage).replace("\n", "<br>") + "</p>")
    if footer:
        parts.append(f"<p style='color:#888;font-size:12px'>{_h.escape(footer)}</p>")
    return "<html><body style='font-family:-apple-system,Helvetica,Arial;font-size:14px'>" \
           + "".join(parts) + "</body></html>"


def _format_imessage_body(summary: dict, calendar_created: list[dict], date_str: str,
                          t: BriefText, tz: str, top_note: str = "",
                          held_back: list[str] | None = None) -> str:
    parts = [f"👨‍👩‍👧‍👦 {PRODUCT_NAME} {_header_date(date_str, t)}"]
    if top_note:
        parts += ["", top_note]
    digest = digest_of(summary)
    if digest:
        parts += ["", digest]
    if held_back:
        parts += ["", *held_back]
    actions = _action_items(summary)
    if actions:
        parts += ["", t.action_items]
        parts += [f"• {a.get('what', '')} {_action_meta(a, t)}".rstrip() for a in actions]
    if calendar_created:
        parts += ["", t.new_events_line]
        for ev in calendar_created[:10]:
            tag = f" ({ev['kid']})" if ev.get("kid") else ""
            parts.append(f"• {ev['title']} — {_start(ev['start'], t, tz)}{tag}")
    return "\n".join(parts)


def _top_note(summary: dict, t: BriefText, assistant: str, translation_note: str = "") -> str:
    """The warnings at the top of the Brief: its translation failed, or the model's reply was cut off."""
    cut_off = t.reply_incomplete.format(assistant=assistant) if summary.get("_incomplete") else ""
    return "\n".join(note for note in (translation_note, cut_off) if note)


def _brief_text(summary: dict, created: list[dict], date_str: str, t: BriefText, tz: str,
                coverage: str, calendar_note: str, top_note: str = "",
                held_back: list[_HeldBack] | None = None, assistant: str = "") -> str:
    """The plain-text Brief: the email's text part, and the iMessage. `held_back` are the
    Held-back Messages, which were not sent to the `assistant`."""
    body = _format_imessage_body(summary, created, date_str, t, tz, top_note,
                                 _held_back_lines(held_back or [], t, assistant))
    for note in (coverage, calendar_note):
        if note:
            body += "\n\n" + note
    return body


# Why Google Calendar did not take tonight's events, and the error when it says more.
CalendarProblem = tuple[Literal["not_authorized", "expired", "write_failed"], str]


def _calendar_note(problem: CalendarProblem | None, ics_fallback: bool, t: BriefText,
                   assistant: str) -> str:
    """The warning when Google Calendar did not take tonight's events."""
    if problem is None:
        return ""
    kind, error = problem
    reauth = t.reauth.format(assistant=assistant)
    note = {"not_authorized": t.calendar_not_authorized + reauth,
            "expired": t.calendar_expired + reauth,
            "write_failed": t.calendar_write_failed.format(error=error)}[kind]
    if ics_fallback:
        note += t.ics_fallback
    # English sentences end with a space for the next one; the last needs none.
    return note.rstrip()


@dataclass
class _Version:
    """Tonight's Brief as the Recipients who read one language get it."""
    t: BriefText
    to: list[str]
    summary: dict
    # Tonight's model-written calendar events in this language, by the original's (title, start).
    events: dict[tuple[str, str], CalendarEvent] = field(default_factory=dict)
    translation_note: str = ""  # at the top of the original when the translation failed

    def translated_event(self, ev: CalendarEvent) -> CalendarEvent:
        return self.events.get((ev.title, ev.start_iso()), ev)

    def translated_list(self, created: list[dict]) -> list[dict]:
        """Tonight's new calendar events as this version lists them, Google links and all."""
        out = []
        for c in created:
            ev = self.events.get((c["title"], c["start"]))
            out.append({**c, "title": ev.title, "kid": ev.kid} if ev else c)
        return out


def _versions(cfg: Config, summary: dict, model_events: list[CalendarEvent],
              messages: list[Message], placeholders: ai_filter.Placeholders | None = None) -> list[_Version]:
    """One version of tonight's Brief per language among the Recipients, the original first.
    Everything the Household shares (Google Calendar, the archive, feedback text) stays the
    original's (ADR 0004). Translations use tonight's `placeholders`."""
    original = cfg.brief_language()
    versions = []
    for language, to in (cfg.brief_recipients_by_language() or {original: []}).items():
        t = languages.text(cfg, language)
        if language == original:
            versions.append(_Version(t, to, summary))
        elif "_llm_error" in summary:
            # The model is down, so there is nothing to translate; the raw list comes in their words.
            versions.append(_Version(t, to, _fallback_summary(messages, summary["_llm_error"], t, cfg)))
        else:
            try:
                translated = translate(cfg, summary, original, language, placeholders)
            except Exception as e:
                log.error("Translating the Brief into %s failed: %s (sending the original)", language, e)
                versions.append(_Version(languages.text(cfg, original), to, summary,
                                         translation_note=t.translation_failed))
                continue
            in_language = extract_calendar_events(translated, cfg.timezone, t.untitled)
            versions.append(_Version(t, to, translated, {(ev.title, ev.start_iso()): tev
                                                         for ev, tev in zip(model_events, in_language)}))
    return versions


def cmd_run(args: argparse.Namespace) -> int:
    cfg = Config.load(args.config)
    args.dry_run = args.dry_run or args.preview
    if args.dry_run:  # writes nothing, so it can't clash with a run that is going
        return _run(cfg, args)
    return run_lock.run_alone(cfg, "Brief", lambda: _run(cfg, args))


def _preview_step(step: str, **more: object) -> None:
    """How far `run --preview` has got, as one line of JSON for the setup page."""
    print(json.dumps({"preview": step, **more}, ensure_ascii=False), flush=True)


def _print_preview(cfg: Config, summary: dict, created: list[dict], coverage: dict[str, dict],
                   body: str, date_str: str, t: BriefText, assistant: str, model_wrote: bool,
                   held_back: list[_HeldBack] | None = None, form: feedback.Links | None = None) -> None:
    """The first Recipient's email as tonight's Brief would send it, as one line of JSON: its
    subject, text and HTML, and for a pilot Household (with its `form` links) the Digest's
    feedback link."""
    html = _daily_brief_html(summary, created, date_str, t, cfg.timezone,
                             coverage=_coverage_note(coverage, t),
                             footer=t.written_by.format(assistant=assistant) if model_wrote else "",
                             feedback_link=form.link if form and model_wrote else None,
                             top_note=_top_note(summary, t, assistant), held_back=held_back, assistant=assistant)
    wrong = form.link(feedback.DIGEST_WRONG, digest_of(summary) if model_wrote else "") if form else None
    _preview_step("made", subject=f"{PRODUCT_NAME} · {date_str}", text=body, html=html,
                  feedback=wrong)


def _form_people(cfg: Config, placeholders: ai_filter.Placeholders | None, for_ai: list[Message],
                 earlier: list[dict]) -> ai_filter.Placeholders:
    """The placeholders the pilot feedback links carry for other people: tonight's, as the AI saw
    them. With the AI filter off, new ones for the same people, since the links go to the team."""
    if placeholders is not None:
        return placeholders
    people = ai_filter.Placeholders()
    add_the_nights_people(cfg, people, [m.to_dict() for m in for_ai], earlier)
    return people


def _run(cfg: Config, args: argparse.Namespace) -> int:
    if args.lookback_hours is not None:
        cfg.gmail.lookback_hours = args.lookback_hours
        cfg.whatsapp.lookback_hours = args.lookback_hours
        cfg.wilma.lookback_hours = args.lookback_hours
        log.info("Lookback overridden to %d hours (all sources)", args.lookback_hours)
    private_files.tighten(cfg)
    state = State(cfg.resolved_state_path())
    if args.lookback_hours is None:
        _catch_up_lookback(cfg, state)

    sources = args.sources.split(",") if args.sources else cfg.default_sources()
    log.info("Running collectors: %s (dry_run=%s)", sources, args.dry_run)
    _wait_for_network(probe_host=_llm_host(cfg))

    coverage: dict[str, dict] = {}
    if args.preview:
        _preview_step("reading", sources=[s for s in _COLLECTED if s in sources])
    messages, direct_events = _run_collectors(
        cfg, state, sources, coverage,
        reading=(lambda source: _preview_step("reading", source=source)) if args.preview else None)
    earlier = archive.recent_points(cfg, today_str(cfg.timezone))
    due_soon = archive.has_due_soon(earlier, today_str(cfg.timezone))
    assistant = "Codex" if cfg.llm.backend == "codex" else "Claude"
    # A preview shows a quiet night's Brief too, so the family sees what one looks like.
    if not messages and not direct_events and not due_soon and not args.preview:
        if set(cfg.default_sources()) <= set(coverage) and all(c["error"] for c in coverage.values()):
            # Silence would look like a quiet night, so the parents hear that nothing could be read.
            log.warning("No Source could be read; sending a short Brief that says so")
            if args.dry_run:
                log.info("DRY-RUN: body:\n%s", _nothing_read(coverage, languages.text(cfg, cfg.brief_language()),
                                                            assistant))
            else:
                languages.prepare_each(cfg, cfg.brief_languages())
                _send_nothing_read(cfg, coverage, today_str(cfg.timezone), assistant)
        else:
            log.info("Nothing new; exiting.")
        state.mark_run_now()
        state.mark_caught_up_now(read=coverage, behind=_behind(coverage))
        if not args.dry_run:
            state.save()
        return 0

    if not args.dry_run:  # a dry run leaves no trace, so it uses what is already there
        languages.prepare_each(cfg, cfg.brief_languages())
    t = languages.text(cfg, cfg.brief_language())
    mode = cfg.google_calendar.mode
    google_ready = mode == "google" and calendar_action.is_configured()

    # Existing Google Calendar events give the LLM context to avoid duplicates (google mode only).
    upcoming: list[dict] = []
    if google_ready:
        try:
            upcoming = calendar_action.get_upcoming_events(cfg, cfg.google_calendar.lookahead_days_context)
        except Exception as e:
            log.warning("Failed to fetch upcoming events (continuing): %s", e)

    # Tonight's placeholders while the AI filter is on: the Brief's call and each translation share
    # them, so a value has the same placeholder in every prompt (ADR 0013).
    placeholders = placeholders_for(cfg)
    # Messages that look sensitive reach no model call: the Brief lists them itself (ADR 0013).
    for_ai, held = for_the_ai(cfg, messages)
    if for_ai or due_soon:
        if args.preview:
            _preview_step("writing")
        try:
            # Pass direct_events so the LLM doesn't duplicate them into calendar_events.
            already_captured = [e.to_dict() for e in direct_events]
            summary = summarize(cfg, for_ai, upcoming, already_captured, earlier,
                                budget=PREVIEW_BUDGET if args.preview else CALL_BUDGET,
                                placeholders=placeholders)
        except Exception as e:
            log.error("Summarizer failed: %s (falling back to rule-based digest)", e)
            summary = _fallback_summary(for_ai, str(e), t, cfg)
    else:
        summary = {"per_kid": [], "calendar_events": [], "message_digest": ""}

    # The rule-based fallback is a raw message list the model never wrote, and a night with only
    # MyClub events or Held-back Messages asks no model: nothing to judge and no model to credit.
    model_wrote = bool(for_ai or due_soon) and "_llm_error" not in summary
    summary["_coverage"] = coverage  # kept in the archive for later checks
    if held:  # the messages in the archive that the AI never saw
        summary["_held_back"] = [m.external_id for m in held]
    listed_held_back = _held_back(held)
    model_events = extract_calendar_events(summary, cfg.timezone, t.untitled)
    events = model_events + direct_events
    created: list[dict] = []
    ics_events = []
    calendar_problem: CalendarProblem | None = None
    if args.dry_run:
        preview = _ics_candidates(cfg, state, events) if mode == "ics" else events
        log.info("DRY-RUN: calendar mode=%s, %d candidate events", mode, len(preview))
        created = _as_created(preview)
    elif mode == "google":
        if not google_ready:
            calendar_problem = ("not_authorized", "")
        else:
            try:
                calendar_action.create_events(cfg, state, events, created)
            except Exception as e:
                log.error("Calendar write failed: %s\n%s", e, traceback.format_exc())
                msg = str(e)
                if "invalid_grant" in msg or "expired" in msg or "revoked" in msg:
                    calendar_problem = ("expired", "")
                else:
                    calendar_problem = ("write_failed", msg[:120].rstrip('.。 '))
        if calendar_problem:
            # Nothing is lost when Google fails: tonight's events go out as .ics instead. Events
            # written before a mid-loop failure are already in state, so the filter drops them
            # from the .ics; they stay listed with their Google links.
            ics_events = _ics_candidates(cfg, state, events)
            created = created + _as_created(ics_events)
    elif mode == "ics":
        ics_events = _ics_candidates(cfg, state, events)
        created = _as_created(ics_events)

    # A Source's events carry the Kid's configured name, which their calendar identity keeps.
    created = [{**c, "kid": cfg.kid_called(c.get("kid"))} for c in created]
    date_str = today_str(cfg.timezone)
    form = feedback.Links(cfg, date_str, _form_people(cfg, placeholders, for_ai, earlier)) \
        if cfg.feedback.active() else None
    body = _brief_text(summary, created, date_str, t, cfg.timezone, _coverage_note(coverage, t),
                       _calendar_note(calendar_problem, bool(ics_events), t, assistant),
                       _top_note(summary, t, assistant), listed_held_back, assistant)
    if args.preview:
        _print_preview(cfg, summary, created, coverage, body, date_str, t, assistant, model_wrote,
                       listed_held_back, form)
    if args.dry_run:
        # Dry runs leave no trace: no archive, no state, no email.
        log.info("DRY-RUN: body (%d chars):\n%s", len(body), body)
        return 0

    # Delivered once any enabled channel gets the Brief out; with none enabled there is nothing to retry.
    email_sent = False
    delivered = not (cfg.email.enabled or cfg.imessage.enabled)
    if cfg.email.enabled:
        links = form.link if form and model_wrote else None
        for v in _versions(cfg, summary, model_events, for_ai, placeholders):
            try:
                listed = v.translated_list(created)
                coverage_note = _coverage_note(coverage, v.t)
                calendar_note = _calendar_note(calendar_problem, bool(ics_events), v.t, assistant)
                top_note = _top_note(v.summary, v.t, assistant, v.translation_note)
                attachments = [(f"parent-recap-{date_str}.ics",
                                ics_action.build(ics_events, cfg.timezone, v.translated_event), "text/calendar")] \
                    if ics_events else []
                html = _daily_brief_html(v.summary, listed, date_str, v.t, cfg.timezone, calendar_note,
                                         ics_attached=bool(attachments), coverage=coverage_note,
                                         footer=v.t.written_by.format(assistant=assistant) if model_wrote else "",
                                         feedback_link=links, original=summary, top_note=top_note,
                                         held_back=listed_held_back, assistant=assistant)
                email_action.send(
                    subject=f"{PRODUCT_NAME} · {date_str}",
                    body_text=_brief_text(v.summary, listed, date_str, v.t, cfg.timezone, coverage_note,
                                          calendar_note, top_note, listed_held_back, assistant),
                    body_html=html,
                    from_addr=cfg.email.from_addr or cfg.gmail.username or "",
                    to_addrs=v.to,
                    attachments=attachments,
                )
                email_sent = delivered = True
                state.mark_delivered_now(v.to)
            except Exception as e:
                log.error("email send failed: %s", e)
    if cfg.imessage.enabled:
        try:
            imessage.send(cfg.imessage.recipients, body)
            delivered = True
            state.mark_delivered_now(cfg.imessage.recipients)
        except Exception as e:
            log.error("imessage send failed: %s", e)

    md_path = archive.write(cfg, date_str, messages, summary, created, delivered)
    log.info("Archive written to %s", md_path)

    # Only mark .ics events as sent once the email actually went out, so a failed send retries tomorrow.
    if email_sent:
        for e in ics_events:
            state.mark_event_created(calendar_action.event_hash(e), "ics")
    if delivered:
        state.mark_caught_up_now(read=coverage, behind=_behind(coverage))
    else:
        # Nobody saw tonight's Brief: leave its Messages unseen so tomorrow's Brief picks them up.
        log.warning("Brief not delivered; its messages will be collected again next run")
        state.forget_new_seen_messages()
    state.mark_run_now()
    state.save()
    return 0


def cmd_collect(args: argparse.Namespace) -> int:
    """Print collected messages + events as JSON (debug; no state write)."""
    cfg = Config.load(args.config)
    state = State(cfg.resolved_state_path())
    messages, events = _run_collectors(cfg, state, args.source.split(","))
    print(json.dumps({
        "messages": [m.to_dict() for m in messages],
        "events": [e.to_dict() for e in events],
    }, ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_sync_attendees(args: argparse.Namespace) -> int:
    cfg = Config.load(args.config)
    result = calendar_action.sync_attendees(cfg)
    log.info("Sync complete: scanned=%d patched=%d", result["scanned"], result["patched"])
    return 0


def cmd_weekend_events(args: argparse.Namespace) -> int:
    from . import weekend_pipeline
    cfg = Config.load(args.config)
    private_files.tighten(cfg)

    def run() -> int:
        _wait_for_network(probe_host=_llm_host(cfg))
        return weekend_pipeline.run(cfg, dry_run=args.dry_run)
    return run() if args.dry_run else run_lock.run_alone(cfg, "Weekend Picks", run)


def cmd_language(args: argparse.Namespace) -> int:
    """Get a language's program text ready ahead of the first night that needs it."""
    cfg = Config.load(args.config)
    try:
        for table in languages.TABLES:  # the Brief's and Weekend Picks' text
            languages.prepare(cfg, args.language, table)
    except Exception as e:
        log.error("Couldn't translate the program's Brief text into %s: %s", args.language, e)
        return 1
    print(f"{args.language}: {languages.describe(cfg, args.language)[1]}")
    return 0


def cmd_summarize(args: argparse.Namespace) -> int:
    """Summarize messages from an archive dump."""
    cfg = Config.load(args.config)
    raw = json.loads(Path(args.input).read_text())
    msgs = [Message(
        source=m["source"], external_id=m["external_id"],
        timestamp=__import__("datetime").datetime.fromisoformat(m["timestamp"]),
        sender=m.get("sender"), subject=m.get("subject"), body=m.get("body", ""),
        chat_name=m.get("chat_name"), kid_hint=m.get("kid_hint"), url=m.get("url"),
        metadata=m.get("metadata", {}),
    ) for m in raw.get("messages", [])]
    summary = summarize(cfg, for_the_ai(cfg, msgs)[0], [])
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="parent-recap")
    p.add_argument("-c", "--config", default=None, help="Path to config.yaml")
    sub = p.add_subparsers(dest="cmd", required=True)

    prun = sub.add_parser("run", help="Full pipeline")
    prun.add_argument("--dry-run", action="store_true")
    prun.add_argument("--preview", action="store_true",
                      help="A dry run that prints, as JSON lines, how far it is and then the "
                           "first Recipient's email, even on a quiet night (the setup page's)")
    prun.add_argument("--sources", default=None,
                      help="Comma-separated: gmail,wilma,whatsapp,myclub (default: all)")
    prun.add_argument("--lookback-hours", type=int, default=None,
                      help="Override the Gmail / WhatsApp / Wilma lookback window (default: per-source config)")
    prun.set_defaults(func=cmd_run)

    pcol = sub.add_parser("collect", help="Debug only: print raw collected messages (includes bodies)")
    pcol.add_argument("--source", default="gmail")
    pcol.set_defaults(func=cmd_collect)

    psum = sub.add_parser("summarize", help="Summarize from a raw JSON dump")
    psum.add_argument("--input", required=True)
    psum.set_defaults(func=cmd_summarize)

    psync = sub.add_parser("sync-attendees",
                           help="Backfill cfg.google_calendar.invite_attendees onto existing events")
    psync.set_defaults(func=cmd_sync_attendees)

    plang = sub.add_parser("language", help="Get the program's Brief text ready in a language "
                           "(translated once when it has no reviewed text)")
    plang.add_argument("language", type=languages.code, help="A language code, such as sv")
    plang.set_defaults(func=cmd_language)

    pwe = sub.add_parser("weekend-events",
                         help="Friday-afternoon: fetch weekend events, rank via LLM, email")
    pwe.add_argument("--dry-run", action="store_true")
    pwe.set_defaults(func=cmd_weekend_events)

    from . import ai_filter_report, ops, setup_steps, uninstall
    ops.register(sub)
    setup_steps.register(sub)
    uninstall.register(sub)
    ai_filter_report.register(sub)

    args = p.parse_args()
    private_files.restrict_new_files()  # archives, logs and diagnostics hold the Household's messages
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
