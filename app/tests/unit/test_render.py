"""The evening run's render core on its own: each golden night's Brief, as written, in; the mails
and iMessages each Recipient gets out, compared to the same golden files the end-to-end nights
make. The Briefs are the nights' model replies (nights.py) with the Sources their refs cite."""
from __future__ import annotations

import copy
import html
from datetime import timezone
from types import SimpleNamespace

import pytest

from conftest import NOW, TZ, html_for_golden, ics_for_golden
from fake_system import LINUX
from nights import ENGLISH_REPLY, normal_night

from family_brief import render
from family_brief.brief import Brief, parse
from family_brief.brief_text import TEXT, BriefText
from family_brief.collectors.base import Message
from family_brief.summarize import extract_calendar_events

DATE = "2026-09-27"
STAMP = NOW.astimezone(timezone.utc)
PARENT, PARTNER = "parent@example.com", "partner@example.com"
COVERAGE = {"gmail": {"count": 2, "error": None}, "myclub": {"count": 1, "error": None},
            "wilma": {"count": 1, "error": None}, "whatsapp": {"count": 2, "error": None}}


def written(reply: dict, sources: dict[str, str]) -> Brief:
    """`reply` as the run hands it on: each Action Item and event with the Sources its refs cite."""
    brief = parse(copy.deepcopy(reply))
    for kid in brief.kids:
        for a in kid.action_items:
            a.source = sorted({sources[r] for r in a.refs or []})
    for ev in brief.events:
        assert ev.refs
        ev.external_id, ev.source = ev.refs[0], sources[ev.refs[0]]
    return brief


def the_normal_night(language: str) -> tuple[dict, dict[str, str]]:
    """The normal night's reply in `language`, and the Source of each Message it may cite."""
    night = SimpleNamespace(config={})
    normal_night(night, language)
    sources = {m.external_id: m.source for got in night.sources.values() for m in got if isinstance(m, Message)}
    return night.model_reply, sources


def brief_of(brief: Brief, t: BriefText, **more) -> render.Night:
    """Tonight's Night for `brief`, its model-written events attached as .ics, as in ics mode."""
    events = extract_calendar_events(brief, TZ, t.untitled)
    created = [{"title": e.title, "start": e.start_iso(), "kid": e.kid} for e in events]
    return render.Night(**{"date": DATE, "tz": TZ, "t": t, "assistant": "Claude", "brief": brief,
                           "coverage": COVERAGE, "created": created, "ics_events": events, "stamp": STAMP,
                           **more})


def mails(out: list[render.Outgoing]) -> list[render.Mail]:
    assert all(isinstance(o, render.Mail) for o in out)
    return out  # type: ignore[return-value]


def check(golden, name: str, mail: render.Mail, ics: bool = False) -> None:
    golden(f"{name}.txt", mail.text)
    golden(f"{name}.html", html_for_golden(mail.html))
    if ics:
        [(file, payload, mime)] = mail.attachments
        assert (file, mime) == (f"parent-recap-{DATE}.ics", "text/calendar")
        golden(f"{name}.ics", ics_for_golden(payload))


@pytest.mark.parametrize("language", ["en", "zh", "fi"])
def test_normal_night(golden, language):
    reply, sources = the_normal_night(language)
    t = TEXT[language]
    night = brief_of(written(reply, sources), t)

    [mail] = mails(render.brief(night, [render.Version(t, [PARENT, PARTNER], night.brief)], []))

    assert (mail.subject, mail.to) == ("Parent Recap · 2026-09-27", [PARENT, PARTNER])
    check(golden, f"normal_night.{language}", mail, ics=True)


HELD_BACK = [
    render.HeldBack("gmail", "Koulupsykologi Laine", "Leon koulunkäynti",
                    "https://mail.google.com/mail/u/0/#search/rfc822msgid:selvitys-12@kilo.example.fi"),
    render.HeldBack("wilma", "Opettaja Virtanen", "Välituntitilanne",
                    "https://espoo.inschool.fi/!7731905/messages/812"),
    render.HeldBack("whatsapp", "李妈妈", "3B parents", None, group_first=True),
]


@pytest.mark.parametrize("language", ["en", "zh", "fi"])
def test_held_back_night(golden, language):
    reply, sources = the_normal_night(language)
    t = TEXT[language]
    coverage = {**COVERAGE, "gmail": {"count": 3, "error": None}, "wilma": {"count": 2, "error": None},
                "whatsapp": {"count": 3, "error": None}}
    night = brief_of(written(reply, sources), t, coverage=coverage, held_back=HELD_BACK)

    [mail] = mails(render.brief(night, [render.Version(t, [PARENT, PARTNER], night.brief)], []))

    check(golden, f"held_back.{language}", mail)


# The coverage of a night the Linked Device needed a new pairing, since a time or not
NEEDS_PAIRING = {"count": 0, "error": "needs a new pairing", "since": "2026-09-25T14:05:00+03:00"}


@pytest.mark.parametrize("language, line", [
    ("en", "⚠️ WhatsApp not read since Fri 25 Sep 14:05 (needs a new pairing). Tonight's Brief may be "
           "incomplete."),
    ("zh", "⚠️ WhatsApp 自 9月25日 周五 14:05 起没读到（需要重新关联设备），今天的日报可能缺这一块。"),
    ("fi", "⚠️ WhatsApp on jäänyt lukematta pe 25.9. klo 14.05 lähtien (laite on linkitettävä uudelleen). "
           "Tämäniltaisesta koosteesta voi puuttua jotain."),
])
def test_a_linked_device_that_needs_a_new_pairing_is_not_read_since_then(language, line):
    reply, sources = the_normal_night(language)
    t = TEXT[language]
    night = brief_of(written(reply, sources), t, coverage={**COVERAGE, "whatsapp": NEEDS_PAIRING})

    [mail] = mails(render.brief(night, [render.Version(t, [PARENT], night.brief)], []))

    assert mail.text.endswith("\n" + line)


@pytest.mark.parametrize("language, lines", [
    ("en", "📥 Read tonight: MyClub 1 event · Wilma 2 messages\n"
           "WhatsApp isn't read in the hosted version."),
    ("zh", "📥 今晚读取：MyClub 1 个日程 · Wilma 2 条\n托管版不读取 WhatsApp。"),
    ("fi", "📥 Luettu tänä iltana: MyClub 1 tapahtuma · Wilma 2 viestiä\n"
           "WhatsApp ei ole mukana ylläpidetyssä versiossa."),
])
def test_the_hosted_version_s_coverage_line_says_whatsapp_isn_t_read_there(system, language,
                                                                           lines):
    system.profile = LINUX
    reply, sources = the_normal_night(language)
    t = TEXT[language]
    coverage = {"myclub": {"count": 1, "error": None}, "wilma": {"count": 2, "error": None}}
    night = brief_of(written(reply, sources), t, coverage=coverage)

    [mail] = mails(render.brief(night, [render.Version(t, [PARENT], night.brief)], []))

    assert mail.text.endswith("\n" + lines)
    assert html.escape(lines.splitlines()[-1]) in mail.html


def test_one_never_paired_since_an_unknown_time_is_not_read_for_that_reason():
    reply, sources = the_normal_night("en")
    t = TEXT["en"]
    night = brief_of(written(reply, sources), t, coverage={**COVERAGE, "whatsapp": {**NEEDS_PAIRING, "since": None}})

    [mail] = mails(render.brief(night, [render.Version(t, [PARENT], night.brief)], []))

    assert mail.text.endswith("\n⚠️ WhatsApp not read (needs a new pairing). Tonight's Brief may be incomplete.")


def test_a_night_nothing_could_be_read_says_to_pair_again():
    t = TEXT["en"]

    [mail] = mails(render.nothing_read({**NOTHING_READ, "whatsapp": NEEDS_PAIRING}, DATE, "Claude", t,
                                       [(t, [PARENT])], None, []))

    assert mail.text.endswith("\n• WhatsApp: needs a new pairing. Tell Claude “pair WhatsApp again” to fix it.")


def fallback(t: BriefText) -> Brief:
    """The rule-based Brief the run writes when the model fails: the normal night's Messages."""
    def at(month: int, day: int, hour: int, minute: int) -> str:
        return t.message_time.format(month=month, day=day, hour=hour, minute=minute)

    def group(kid: str, source: str, *lines: str) -> list[str]:
        return [f"**{kid} · {source}**{t.fallback_count.format(count=t.messages(len(lines)))}",
                *(f"  • {line}" for line in lines), ""]
    digest = [t.fallback_header, "",
              *group("Leo", "whatsapp", f"{at(9, 27, 18, 2)} Anna (piano): Piano lesson moves to Wednesday 17:30 this week"),
              *group("Mia", "whatsapp", f"{at(9, 27, 19, 45)} Parent rep: Remember reissuvihko signatures 🙏"),
              *group("Mia", "wilma", f"{at(9, 27, 12, 0)} Opettaja Virtanen: Koe 5.10."),
              *group(t.unsorted, "gmail", f"{at(9, 27, 8, 15)} teacher.3b@kilo.example.fi: Retki Nuuksioon to 1.10.",
                     f"{at(9, 27, 10, 40)} office@kilo.example.fi: Valokuvaus ke 30.9.")]
    return Brief("\n".join(digest))


@pytest.mark.parametrize("language", ["en", "zh", "fi"])
def test_night_the_model_failed(golden, language):
    t = TEXT[language]
    night = brief_of(fallback(t), t, model_wrote=False)

    [mail] = mails(render.brief(night, [render.Version(t, [PARENT, PARTNER], night.brief)], []))

    check(golden, f"model_failed.{language}", mail)
    assert not mail.attachments


NOTHING_READ = {
    "gmail": {"count": 0, "error": "b'[AUTHENTICATIONFAILED] Invalid credentials (Failure)'"},
    "myclub": {"count": 0, "error": "MyClub fetch failed for Mia: example.myclub.fi: OSError"},
    "wilma": {"count": 0, "error": "wilma: request timed out"},
    "whatsapp": {"count": 0, "error": "[Errno 1] Operation not permitted"},
}


@pytest.mark.parametrize("language", ["en", "zh", "fi"])
def test_night_no_source_could_be_read(golden, language):
    t = TEXT[language]

    [mail] = mails(render.nothing_read(NOTHING_READ, DATE, "Claude", t, [(t, [PARENT, PARTNER])], None, []))

    assert (mail.subject, mail.to) == ("Parent Recap · 2026-09-27", [PARENT, PARTNER])
    check(golden, f"nothing_read.{language}", mail)


def test_night_no_source_could_be_read_reaches_imessage_in_the_brief_language():
    zh, en = TEXT["zh"], TEXT["en"]

    out = render.nothing_read(NOTHING_READ, DATE, "Claude", zh, [(en, [PARTNER])], None, ["+358401234567"])

    [mail, imessage] = out
    assert mail.to == [PARTNER] and "No Source could be read tonight" in mail.text
    assert imessage == render.IMessage(["+358401234567"], imessage.text)
    assert "今晚所有信息源都没读到" in imessage.text


def test_night_in_two_languages(golden):
    reply, sources = the_normal_night("zh")
    zh, en = TEXT["zh"], TEXT["en"]
    night = brief_of(written(reply, sources), zh)
    translated = written(ENGLISH_REPLY, sources)
    events = {(e.title, e.start_iso()): te for e, te in
              zip(night.ics_events, extract_calendar_events(translated, TZ, en.untitled))}

    first, partner = mails(render.brief(night, [render.Version(zh, [PARENT], night.brief),
                                                render.Version(en, [PARTNER], translated, events)], []))

    assert (first.to, partner.to) == ([PARENT], [PARTNER])
    check(golden, "normal_night.zh", first, ics=True)
    check(golden, "two_languages.en", partner, ics=True)


def test_night_its_translation_failed(golden):
    reply, sources = the_normal_night("zh")
    zh = TEXT["zh"]
    night = brief_of(written(reply, sources), zh)
    failed = render.Version(zh, [PARTNER], night.brief, translation_note=TEXT["en"].translation_failed)

    first, partner = mails(render.brief(night, [render.Version(zh, [PARENT], night.brief), failed], []))

    check(golden, "normal_night.zh", first)
    check(golden, "failed_translation.en", partner)
    golden("normal_night.zh.ics", ics_for_golden(partner.attachments[0][1]))


def test_the_imessage_is_the_plain_text_brief_in_the_brief_language(golden):
    reply, sources = the_normal_night("zh")
    night = brief_of(written(reply, sources), TEXT["zh"])

    [imessage] = render.brief(night, [], ["+358401234567"])

    assert isinstance(imessage, render.IMessage) and imessage.to == ["+358401234567"]
    golden("normal_night.zh.txt", imessage.text)
