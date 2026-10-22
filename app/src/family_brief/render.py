"""The evening run's pure core: tonight's Brief, its coverage, Held-back list and each language's
text in, the mails and iMessages that go out to each Recipient out. Nothing here reads or writes
anything; run.py gathers what goes in and sends what comes out."""
from __future__ import annotations

import html as _h
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Callable, Literal, Union
from zoneinfo import ZoneInfo

from . import feedback
from . import sources as source_registry
from .actions import ics as ics_action
from .brief_text import PRODUCT_NAME, BriefText
from .collectors.base import UNREADABLE_MESSAGE, CalendarEvent
from .summarize import digest_of
from .utils.dates import to_local


@dataclass(frozen=True)
class Mail:
    """One email, to the Recipients who read one language."""
    to: list[str]
    subject: str
    text: str
    html: str
    from_addr: str
    attachments: list[tuple[str, bytes, str]] = field(default_factory=list)


@dataclass(frozen=True)
class IMessage:
    """The plain-text Brief, in the Brief's language, to every iMessage Recipient."""
    to: list[str]
    text: str


Outgoing = Union[Mail, IMessage]

_HTML_PAGE = "<html><body style='font-family:-apple-system,Helvetica,Arial;font-size:14px'>"


# ── What the Brief says about its Sources

def source_name(source: str) -> str:
    """What the Brief calls `source`: a Source, a brand written the same in every language, or the
    Google Calendar a conflict Notice cites (citations.py)."""
    return "Calendar" if source == "calendar" else source_registry.label_of(source)


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
    record = source_registry.named(source)
    permission = record.permission_lacked(t) if record else t.permission_denied
    return {"unreadable": t.message_unreadable,  # without the Message's id and the error, which are for the log
            "permission": permission,
            "login": t.login_failed}.get(_problem(err)) or err.splitlines()[0][:60]


def _coverage_note(coverage: dict[str, dict], t: BriefText) -> str:
    """One line saying what was read tonight, plus a warning for any source that failed."""
    if not coverage:
        return ""
    read, missed = [], []
    for source, c in coverage.items():
        name = source_name(source)
        counts_events = source_registry.named(source).counts_events
        read.append(f"{name} {(t.events if counts_events else t.messages)(c['count'])}")
        if c["error"]:
            missed.append((t.partly_read if c["count"] else t.not_read)
                          .format(source=name, reason=_short_reason(source, c["error"], t)))
    note = t.read_tonight.format(counts=" · ".join(read))
    if missed:
        note += "\n" + t.may_be_incomplete.format(missed=t.missed_sep.join(missed))
    return note


def nothing_read_text(coverage: dict[str, dict], t: BriefText, assistant: str) -> str:
    """The short Brief of a night when no Source could be read: each Source's problem and its fix."""
    lines = [t.nothing_read]
    for source, c in coverage.items():
        name = source_name(source)
        fix = {"login": t.fix_login, "permission": t.fix_permission}.get(_problem(c["error"]), t.fix_other)
        lines.append(t.source_problem.format(source=name, reason=_short_reason(source, c["error"], t).rstrip(".。 "),
                                             fix=fix.format(assistant=assistant, source=name)))
    return "\n".join(lines)


def nothing_read(coverage: dict[str, dict], date_str: str, assistant: str, t: BriefText,
                 readers: list[tuple[BriefText, list[str]]], from_addr: str,
                 imessage_to: list[str]) -> list[Outgoing]:
    """What goes out on a night when no Source could be read: a mail to the Recipients of each
    language among the `readers` saying so in it, and the iMessage in the Brief's language `t`."""
    def text(t: BriefText) -> str:
        return f"👨‍👩‍👧‍👦 {PRODUCT_NAME} {_header_date(date_str, t)}\n\n" + nothing_read_text(coverage, t, assistant)
    out: list[Outgoing] = []
    for reader_t, to in readers:
        note = _h.escape(nothing_read_text(coverage, reader_t, assistant)).replace("\n", "<br>")
        out.append(Mail(to=to, subject=f"{PRODUCT_NAME} · {date_str}", text=text(reader_t),
                        html=_HTML_PAGE
                        + f"<h2>👨‍👩‍👧‍👦 {PRODUCT_NAME} · {_h.escape(_header_date(date_str, reader_t))}</h2>"
                          f"<p style='color:#a33'>{note}</p></body></html>",
                        from_addr=from_addr))
    if imessage_to:
        out.append(IMessage(imessage_to, text(t)))
    return out


# ── The pieces of a Brief

def _action_items(summary: dict) -> list[dict]:
    """Every Action Item in the Brief, each with its Kid."""
    return [{**a, "kid": k.get("kid", "")}
            for k in summary.get("per_kid", []) or [] for a in k.get("action_items") or []]


def _action_meta(a: dict, t: BriefText) -> str:
    """What the Brief says after an Action Item: its Kid, due date, assignee and Sources."""
    by = _due(str(a.get("by", "")), t)
    kid = str(a.get("kid", ""))
    source = " · ".join(source_name(s) for s in a.get("source") or [])
    return " · ".join(x for x in [f"({kid})" if kid else "", t.due.format(date=by) if by else "",
                                  str(a.get("who", "")), source] if x)


@dataclass(frozen=True)
class HeldBack:
    """A Held-back Message as the Brief lists it, without the AI (ADR 0013): its Source, sender and
    subject (for a Source whose messages have none, such as WhatsApp, its group, listed first), and
    the link to read it at its Source."""
    source: str
    sender: str
    subject: str | None
    link: str | None
    group_first: bool = False

    def label(self, t: BriefText) -> str:
        subject = self.subject or t.no_subject
        about = [subject, self.sender] if self.group_first else [self.sender, subject]
        return " · ".join(x for x in [self.source_name, *about] if x)

    @property
    def source_name(self) -> str:
        return source_name(self.source)


def _held_back_lines(held: list[HeldBack], t: BriefText, assistant: str) -> list[str]:
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


def _shown_digest(summary: dict, t: BriefText) -> str:
    """The Digest the Brief shows. When the model wrote Notices but no Digest, the Notices stand in
    for it, as the Digest's Markdown grouped by Kid, so a night whose only news is Notices doesn't
    send an empty Brief (#365). On other nights the Notices reach only the archive."""
    digest = digest_of(summary)
    if digest:
        return digest
    sections = []
    for kid in summary.get("per_kid") or []:
        notices = [f"- {n['text']}" for n in kid.get("notices") or [] if n.get("text")]
        if notices:
            sections.append("\n".join([f"**{kid.get('kid') or t.household}**", *notices]))
    return "\n\n".join(sections)


def _daily_brief_html(summary: dict, calendar_created: list[dict],
                      date_str: str, t: BriefText, tz: str, note: str = "", ics_attached: bool = False,
                      coverage: str = "", footer: str = "",
                      feedback_link: Callable[..., str] | None = None,
                      original: dict | None = None, top_note: str = "",
                      held_back: list[HeldBack] | None = None, assistant: str = "") -> str:
    """Simple HTML rendering of the Brief, safe for Gmail. `summary` is translated from
    `original` for a Recipient in another language; feedback links carry the original's text, so
    the Form gets the same words from every language version (ADR 0004). `top_note` holds the
    warnings shown above the Digest (see _top_note), and `held_back` the Held-back Messages, which
    were not sent to the `assistant`."""
    original = original or summary

    def form_link(verdict: str, label: str, text: str, **kw) -> str:
        assert feedback_link is not None
        return (f'<a href="{_h.escape(feedback_link(verdict, text, **kw))}"'
                f" style='color:#888;text-decoration:none'>{label}</a>")
    parts = [f"<h2>👨‍👩‍👧‍👦 {PRODUCT_NAME} · {_h.escape(_header_date(date_str, t))}</h2>"]
    if top_note:
        warnings = _h.escape(top_note).replace("\n", "<br>")
        parts.append(f"<p style='color:#a33'>{warnings}</p>")
    digest = _shown_digest(summary, t)
    if digest:
        parts.append(_digest_html(digest))
        if feedback_link:
            as_written = _shown_digest(original, t)
            parts.append("<p><small>"
                         f"{form_link(feedback.DIGEST_WRONG, t.feedback_digest_wrong, as_written)}"
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
    return _HTML_PAGE + "".join(parts) + "</body></html>"


def _format_imessage_body(summary: dict, calendar_created: list[dict], date_str: str,
                          t: BriefText, tz: str, top_note: str = "",
                          held_back: list[str] | None = None) -> str:
    parts = [f"👨‍👩‍👧‍👦 {PRODUCT_NAME} {_header_date(date_str, t)}"]
    if top_note:
        parts += ["", top_note]
    digest = _shown_digest(summary, t)
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
                held_back: list[HeldBack] | None = None, assistant: str = "") -> str:
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


# ── Tonight's Brief

@dataclass(frozen=True)
class Night:
    """Tonight's Brief as written, in the Brief's language `t`, and what the run found around it:
    what was read (`coverage`), the Held-back Messages, the calendar events listed (`created`)
    and those attached as .ics, and why Google Calendar did not take them. The model wrote the
    summary unless it failed or wasn't asked (`model_wrote`); then the Brief has no feedback
    links and doesn't credit the `assistant`."""
    date: str
    tz: str
    t: BriefText
    assistant: str
    summary: dict
    coverage: dict[str, dict]
    created: list[dict] = field(default_factory=list)
    held_back: list[HeldBack] = field(default_factory=list)
    calendar_problem: CalendarProblem | None = None
    ics_events: list[CalendarEvent] = field(default_factory=list)
    model_wrote: bool = True
    form: feedback.Links | None = None
    stamp: datetime | None = None  # when the .ics is made; now when not given

    @property
    def subject(self) -> str:
        return f"{PRODUCT_NAME} · {self.date}"

    def text(self) -> str:
        """The plain-text Brief in the Brief's language: the iMessage, and what a dry run logs."""
        return _brief_text(self.summary, self.created, self.date, self.t, self.tz,
                           _coverage_note(self.coverage, self.t),
                           _calendar_note(self.calendar_problem, bool(self.ics_events), self.t, self.assistant),
                           _top_note(self.summary, self.t, self.assistant), self.held_back, self.assistant)


@dataclass(frozen=True)
class Version:
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


def _mail(night: Night, v: Version, from_addr: str) -> Mail:
    t, assistant = v.t, night.assistant
    listed = v.translated_list(night.created)
    coverage_note = _coverage_note(night.coverage, t)
    calendar_note = _calendar_note(night.calendar_problem, bool(night.ics_events), t, assistant)
    top_note = _top_note(v.summary, t, assistant, v.translation_note)
    attachments = [(f"parent-recap-{night.date}.ics",
                    ics_action.build(night.ics_events, night.tz, v.translated_event, night.stamp),
                    "text/calendar")] if night.ics_events else []
    links = night.form.link if night.form and night.model_wrote else None
    html = _daily_brief_html(v.summary, listed, night.date, t, night.tz, calendar_note,
                             ics_attached=bool(attachments), coverage=coverage_note,
                             footer=t.written_by.format(assistant=assistant) if night.model_wrote else "",
                             feedback_link=links, original=night.summary, top_note=top_note,
                             held_back=night.held_back, assistant=assistant)
    return Mail(to=v.to, subject=night.subject,
                text=_brief_text(v.summary, listed, night.date, t, night.tz, coverage_note,
                                 calendar_note, top_note, night.held_back, assistant),
                html=html, from_addr=from_addr, attachments=attachments)


def brief(night: Night, versions: list[Version], from_addr: str, imessage_to: list[str]) -> list[Outgoing]:
    """What goes out tonight: a mail per version, each to the Recipients who read its language,
    and the plain-text Brief to the iMessage Recipients."""
    out: list[Outgoing] = [_mail(night, v, from_addr) for v in versions]
    if imessage_to:
        out.append(IMessage(imessage_to, night.text()))
    return out


def preview(night: Night) -> dict[str, str | None]:
    """The first Recipient's email as tonight's Brief would send it, for the setup page: its
    subject, text and HTML, and for a pilot Household (with its Form links) the Digest's
    feedback link."""
    t, assistant, form = night.t, night.assistant, night.form
    html = _daily_brief_html(night.summary, night.created, night.date, t, night.tz,
                             coverage=_coverage_note(night.coverage, t),
                             footer=t.written_by.format(assistant=assistant) if night.model_wrote else "",
                             feedback_link=form.link if form and night.model_wrote else None,
                             top_note=_top_note(night.summary, t, assistant), held_back=night.held_back,
                             assistant=assistant)
    wrong = form.link(feedback.DIGEST_WRONG, _shown_digest(night.summary, t) if night.model_wrote else "") \
        if form else None
    return {"subject": night.subject, "text": night.text(), "html": html, "feedback": wrong}


def nothing_to_show(summary: dict, created: list[dict], held_back: list[HeldBack], t: BriefText,
                    assistant: str) -> bool:
    """Whether tonight's Brief would have only its header and the notes under it: no Digest, or
    Notices standing in for one, no Action Items, events or Held-back Messages, and no warning."""
    return not (_shown_digest(summary, t) or _action_items(summary) or created or held_back
                or _top_note(summary, t, assistant))
