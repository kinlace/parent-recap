"""The evening run's render core on its own: each golden night's Brief, as written, in; the mails
and iMessages each Recipient gets out, compared to the same golden files the end-to-end nights
make. The Briefs are the nights' model replies (nights.py) with the Sources their refs cite."""
from __future__ import annotations

import copy
import dataclasses
import html
from datetime import timezone
from types import SimpleNamespace

import pytest
from urllib.parse import parse_qs, urlsplit

from conftest import NOW, TZ, html_for_golden, ics_for_golden, without_unread_note
from fake_system import LINUX
from nights import ENGLISH_REPLY, normal_night

from family_brief import render
from family_brief.brief import Brief, parse
from family_brief.brief_text import TEXT, BriefText, recommend_link
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
    """Tonight's Night for `brief`, its model-written events attached as .ics, as in ics mode, from
    Parent Recap's own address, which says to write to the setup parent (ADR 0032)."""
    events = extract_calendar_events(brief, TZ, t.untitled)
    created = [{"title": e.title, "start": e.start_iso(), "kid": e.kid, "location": e.location,
                "description": e.description} for e in events]  # as the run lists them
    return render.Night(**{"date": DATE, "tz": TZ, "t": t, "assistant": "Claude", "brief": brief,
                           "coverage": COVERAGE, "created": created, "ics_events": events, "stamp": STAMP,
                           "write_to": PARENT, **more})


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

    assert without_unread_note(mail.text).endswith("\n" + line)


@pytest.mark.parametrize("language, lines", [
    ("en", "📥 Read tonight: MyClub 1 event · Wilma 2 messages\n"
           "WhatsApp and Gmail aren't read in the hosted version."),
    ("zh", "📥 今晚读取：MyClub 1 个日程 · Wilma 2 条\n托管版不读取 WhatsApp 和 Gmail。"),
    ("fi", "📥 Luettu tänä iltana: MyClub 1 tapahtuma · Wilma 2 viestiä\n"
           "WhatsApp ja Gmail eivät ole mukana ylläpidetyssä versiossa."),
])
def test_a_hosted_household_s_coverage_line_names_every_source_it_can_t_have(system, language,
                                                                              lines):
    system.profile = LINUX
    reply, sources = the_normal_night(language)
    t = TEXT[language]
    coverage = {"myclub": {"count": 1, "error": None}, "wilma": {"count": 2, "error": None}}
    night = brief_of(written(reply, sources), t, coverage=coverage, hosted=True)

    [mail] = mails(render.brief(night, [render.Version(t, [PARENT], night.brief)], []))

    assert without_unread_note(mail.text).endswith("\n" + lines)
    assert html.escape(lines.splitlines()[-1]) in mail.html


@pytest.mark.parametrize("language, line", [
    ("en", "WhatsApp isn't read in the hosted version."),
    ("zh", "托管版不读取 WhatsApp。"),
    ("fi", "WhatsApp ei ole mukana ylläpidetyssä versiossa."),
])
def test_a_source_a_hosted_household_read_tonight_is_not_named_as_unread(system, language, line):
    system.profile = LINUX
    reply, sources = the_normal_night(language)
    t = TEXT[language]
    # The maintainers set up Eepos and eVaka by hand for a Hosted Household, as its sign-up can't
    # yet (#755, #768).
    coverage = {"gmail": {"count": 1, "error": None}, "wilma": {"count": 2, "error": None},
                "eepos": {"count": 3, "error": None}, "evaka": {"count": 4, "error": None}}
    night = brief_of(written(reply, sources), t, coverage=coverage, hosted=True)

    [mail] = mails(render.brief(night, [render.Version(t, [PARENT], night.brief)], []))

    assert without_unread_note(mail.text).endswith("\n" + line)


def test_a_family_s_own_computer_names_no_source_as_unread_in_the_hosted_version():
    reply, sources = the_normal_night("en")
    t = TEXT["en"]
    coverage = {"myclub": {"count": 1, "error": None}, "wilma": {"count": 2, "error": None}}
    night = brief_of(written(reply, sources), t, coverage=coverage)

    [mail] = mails(render.brief(night, [render.Version(t, [PARENT], night.brief)], []))

    assert without_unread_note(mail.text).endswith("📥 Read tonight: MyClub 1 event · Wilma 2 messages")


def test_one_never_paired_since_an_unknown_time_is_not_read_for_that_reason():
    reply, sources = the_normal_night("en")
    t = TEXT["en"]
    night = brief_of(written(reply, sources), t, coverage={**COVERAGE, "whatsapp": {**NEEDS_PAIRING, "since": None}})

    [mail] = mails(render.brief(night, [render.Version(t, [PARENT], night.brief)], []))

    assert without_unread_note(mail.text).endswith(
        "\n⚠️ WhatsApp not read (needs a new pairing). Tonight's Brief may be incomplete.")


def test_a_night_nothing_could_be_read_says_to_pair_again():
    t = TEXT["en"]

    [mail] = mails(render.nothing_read({**NOTHING_READ, "whatsapp": NEEDS_PAIRING}, DATE, "Claude", t,
                                       [(t, [PARENT])], None, []))

    assert mail.text.endswith("\n• WhatsApp: needs a new pairing. Tell Claude “pair WhatsApp again” to fix it."
                              "\n\nParent Recap, running on your own computer\nChange settings: "
                              "https://github.com/kinlace/parent-recap/blob/stable/README.md#change-settings"
                              f"\nRecommend to other parents: {recommend_link(t)}")


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


@pytest.mark.parametrize("language", ["en", "zh", "fi"])
def test_night_the_computer_slept_through(golden, language):
    reply, sources = the_normal_night(language)
    t = TEXT[language]
    night = brief_of(written(reply, sources), t, slept=True)

    [mail] = mails(render.brief(night, [render.Version(t, [PARENT, PARTNER], night.brief)], []))

    check(golden, f"slept.{language}", mail)
    assert mail.text.split("\n")[2] == t.slept  # under the heading, before the Brief


def test_a_note_that_the_computer_slept_alone_is_nothing_to_send():
    t = TEXT["en"]

    assert render.nothing_to_show(brief_of(Brief(), t, slept=True))


def test_a_catch_up_that_also_slept_says_both_the_catch_up_first():
    reply, sources = the_normal_night("en")
    t = TEXT["en"]
    night = brief_of(written(reply, sources), t, slept=True, caught_up=True)

    [mail] = mails(render.brief(night, [render.Version(t, [PARENT], night.brief)], []))

    assert mail.text.split("\n")[2:4] == [t.caught_up, t.slept]
    assert t.caught_up in mail.html


def test_a_note_that_the_brief_catches_up_alone_is_nothing_to_send():
    assert render.nothing_to_show(brief_of(Brief(), TEXT["en"], caught_up=True))


NOTHING_READ = {
    "gmail": {"count": 0, "error": "b'[AUTHENTICATIONFAILED] Invalid credentials (Failure)'"},
    "myclub": {"count": 0, "error": "MyClub fetch failed for Mia: example.myclub.fi: OSError"},
    "wilma": {"count": 0, "error": "wilma: request timed out"},
    "whatsapp": {"count": 0, "error": "[Errno 1] Operation not permitted"},
}


@pytest.mark.parametrize("language", ["en", "zh", "fi"])
def test_night_no_source_could_be_read(golden, language):
    t = TEXT[language]

    [mail] = mails(render.nothing_read(NOTHING_READ, DATE, "Claude", t, [(t, [PARENT, PARTNER])], PARENT, []))

    assert (mail.subject, mail.to) == ("Parent Recap · 2026-09-27", [PARENT, PARTNER])
    check(golden, f"nothing_read.{language}", mail)


def test_night_no_source_could_be_read_reaches_imessage_in_the_brief_language():
    zh, en = TEXT["zh"], TEXT["en"]

    out = render.nothing_read(NOTHING_READ, DATE, "Claude", zh, [(en, [PARTNER])], PARENT, ["+358401234567"])

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


def every_kind(t: BriefText, hosted: bool) -> dict[str, tuple[str, str]]:
    """Each kind of Brief, by name, as its first Recipient gets it, text and HTML: the normal one,
    the fallback list, the nothing-read one and the first one, from setup."""
    reply, sources = the_normal_night("en")
    normal = brief_of(written(reply, sources), t, hosted=hosted)
    failed = brief_of(fallback(t), t, model_wrote=False, hosted=hosted)
    [normal_mail] = mails(render.brief(normal, [render.Version(t, [PARENT], normal.brief)], []))
    [failed_mail] = mails(render.brief(failed, [render.Version(t, [PARENT], failed.brief)], []))
    [nothing] = mails(render.nothing_read(NOTHING_READ, DATE, "Claude", t, [(t, [PARENT])], PARENT, [],
                                          hosted))
    first = render.preview(normal)
    return {"normal": (normal_mail.text, normal_mail.html), "fallback": (failed_mail.text, failed_mail.html),
            "nothing read": (nothing.text, nothing.html), "first": (first["text"], first["html"])}


@pytest.mark.parametrize("language, hosted, line", [
    ("en", False, "Parent Recap, running on your own computer"),
    ("zh", False, "Parent Recap，在你家的电脑上运行"),
    ("fi", False, "Parent Recap, joka toimii omalla tietokoneellasi"),
    ("en", True, "Parent Recap hosted version, running on the parentrecap.com server"),
    ("zh", True, "Parent Recap 托管版，在 parentrecap.com 的服务器上运行"),
    ("fi", True, "Parent Recapin ylläpidetty versio, joka toimii parentrecap.com-palvelimella"),
])
def test_every_kind_of_brief_ends_saying_which_version_sent_it(language, hosted, line):
    t = TEXT[language]
    other = t.runs_on_own_computer if hosted else t.runs_hosted

    for kind, (text, page) in every_kind(t, hosted).items():
        end = text.rsplit("\n\n", 1)[1]
        assert end.startswith(line + "\n"), kind
        assert end.endswith("\n" + t.unread_sender.format(address=PARENT)), kind
        assert f"<p style='color:#888;font-size:12px'>{html.escape(line)}</p>" in page, kind
        assert other not in text and html.escape(other) not in page, kind


@pytest.mark.parametrize("language, label, readme", [
    ("en", "Change settings", "README.md"),
    ("zh", "修改设置", "README.zh-CN.md"),
    ("fi", "Muuta asetuksia", "README.fi.md"),
])
def test_a_brief_from_the_family_computer_links_how_to_change_settings_in_its_language(language, label, readme):
    t = TEXT[language]
    url = f"https://github.com/kinlace/parent-recap/blob/stable/{readme}#change-settings"

    for kind, (text, page) in every_kind(t, hosted=False).items():
        end = text.rsplit("\n\n", 1)[1].splitlines()
        assert end[:2] == [t.runs_on_own_computer, f"{label}: {url}"], kind
        assert (f"<p style='color:#888;font-size:12px'><a href=\"{url}\" style='color:#888;"
                f"text-decoration:underline'>{html.escape(label)}</a></p>") in page, kind


def test_a_hosted_brief_has_no_settings_link():
    for language, t in TEXT.items():
        for kind, (text, page) in every_kind(t, hosted=True).items():
            assert "#change-settings" not in text and "#change-settings" not in page, (language, kind)
            assert t.change_settings not in text and t.change_settings not in page, (language, kind)


@pytest.mark.parametrize("language, label, message", [
    ("en", "Recommend to other parents",
     "I've been using a free little tool that turns Wilma, school emails and the parents' group chats into "
     "one short summary every evening, in the language you choose. What the kids need to bring, hand in or "
     "sign up for is clear at a glance, and it saves me a lot of hassle. You could try it too: "
     "https://parentrecap.com"),
    ("zh", "推荐给其他家长",
     "我最近在用一个免费小工具，每晚把 Wilma、学校邮件和家长群的消息整理成一封中文日报，孩子要带什么、交什么、"
     "有什么活动一眼就清楚，省了不少事。你也可以试试：https://parentrecap.com"),
    ("fi", "Suosittele muille vanhemmille",
     "Olen käyttänyt ilmaista pientä työkalua, joka kokoaa joka ilta Wilman, koulun sähköpostit ja vanhempien "
     "WhatsApp-ryhmien viestit yhdeksi lyhyeksi koosteeksi haluamallasi kielellä. Siitä näkee yhdellä "
     "silmäyksellä, mitä lapsen pitää ottaa mukaan tai palauttaa ja mitä tapahtumia on tulossa. Säästää "
     "paljon vaivaa! Kokeile sinäkin: https://parentrecap.com"),
])
@pytest.mark.parametrize("hosted", [False, True])
def test_every_brief_links_a_whatsapp_recommendation_in_its_language(language, label, message, hosted):
    t = TEXT[language]

    for kind, (text, page) in every_kind(t, hosted).items():
        end = text.rsplit("\n\n", 1)[1].splitlines()
        [line] = [line for line in end if line.startswith(f"{label}: ")]
        url = line.removeprefix(f"{label}: ")
        assert url.startswith("https://wa.me/?text="), kind
        query = urlsplit(url).query
        assert parse_qs(query) == {"text": [message]}, kind
        assert " " not in url and "&" not in query, kind  # the whole message is one encoded value
        assert end.index(line) == (1 if hosted else 2), kind  # after the version line and the settings link
        assert (f"<p style='color:#888;font-size:12px'><a href=\"{html.escape(url)}\" style='color:#888;"
                f"text-decoration:underline'>{html.escape(label)}</a></p>") in page, kind


def test_the_recommendation_carries_nothing_from_the_households_brief():
    for language, t in TEXT.items():
        for hosted in (False, True):
            for kind, (text, page) in every_kind(t, hosted).items():
                assert recommend_link(t) in text and html.escape(recommend_link(t)) in page, (language, kind)
        message = parse_qs(urlsplit(recommend_link(t)).query)["text"][0]
        for detail in ("Mia", "Leo", "Opettaja", "Virtanen", "piano", PARENT):
            assert detail not in message, (language, detail)


def test_a_brief_in_an_unreviewed_language_links_the_english_readme():
    t = dataclasses.replace(TEXT["en"], language_name="Svenska", change_settings="Ändra inställningar")

    text, page = every_kind(t, hosted=False)["normal"]

    url = "https://github.com/kinlace/parent-recap/blob/stable/README.md#change-settings"
    assert f"Ändra inställningar: {url}" in text and f'href="{url}"' in page


def test_the_imessage_is_the_plain_text_brief_in_the_brief_language(golden):
    reply, sources = the_normal_night("zh")
    night = brief_of(written(reply, sources), TEXT["zh"])

    [imessage] = render.brief(night, [], ["+358401234567"])

    assert isinstance(imessage, render.IMessage) and imessage.to == ["+358401234567"]
    # The email's text, but for its last line, which says the address it came from is unread.
    golden("normal_night.zh.imessage.txt", imessage.text)


def test_the_new_calendar_events_are_listed_by_start_whatever_source_gave_them():
    t = TEXT["en"]
    created = [  # as the run collects them: the model's first, then each Source's in its own order
        {"title": "Parents' evening", "start": "2026-10-02T18:00:00", "kid": "Leo"},
        {"title": "Daycare closed", "start": "2026-10-06", "kid": "Mia"},
        {"title": "Forest trip", "start": "2026-09-30T08:45:00+03:00", "kid": "Mia"},
        {"title": "Training", "start": "2026-09-30T14:30:00+00:00", "kid": "Leo"},  # 17:30 in Helsinki
        {"title": "Photo day", "start": "2026-09-30", "kid": "Mia"},
    ]
    night = brief_of(Brief(), t, created=created, ics_events=[])

    [mail] = mails(render.brief(night, [render.Version(t, [PARENT], night.brief)], []))

    order = ["Photo day", "Forest trip", "Training", "Parents' evening", "Daycare closed"]
    for listed in (mail.text, mail.html, night.text()):
        at = [listed.index(html.escape(title) if listed is mail.html else title) for title in order]
        assert at == sorted(at), listed
