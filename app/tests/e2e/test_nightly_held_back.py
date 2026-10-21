"""The nightly `parent-recap run`'s held-back messages, end to end (ADR 0013): sensitive messages
are held back from the AI and listed in the Brief. Assertions are only on what leaves the system:
the email (text, HTML, attachments), the model command line, and persisted state."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from html import escape

import pytest

from conftest import html_for_golden, msg

from nights import ENGLISH_REPLY, NORMAL_NIGHT_IDS, PARTNER_EN, normal_night, payload_message_ids


# ── Sensitive messages are held back from the AI and listed in the Brief (ADR 0013)

# A message with a word from each category in each language: (id, Source, sender, subject, body).
SENSITIVE = [
    ("message:901", "wilma", "Opettaja Virtanen", "Lääkärin lausunto", "Mialla todettiin epilepsia."),
    ("message:902", "wilma", "Erityisopettaja Laine", "Leon koulunkäynti",
     "Leolle on tehty pedagoginen selvitys, ja aloitamme tehostetun tuen."),
    ("1901", "gmail", "Rehtori <rehtori@kilo.example.fi>", "Välituntitilanne",
     "Leoa kiusattiin taas välitunnilla."),
    ("message:903", "wilma", "Kuraattori", "Tapaaminen ensi viikolla", "Olemme tehneet lastensuojeluilmoituksen."),
    ("1902", "gmail", "Skolan <skolan@kilo.example.fi>", "Möte om Mia", "Skolpsykologen vill träffa er om Mia."),
    ("1903", "gmail", "Läraren <larare@kilo.example.fi>", "Från nästa vecka", "Leo får särskilt stöd från nästa vecka."),
    ("1:901", "whatsapp", "Eva", None, "Mia har blivit mobbad på rasterna."),
    ("1:902", "whatsapp", "Eva", None, "Vi har gjort en barnskyddsanmälan."),
    ("1904", "gmail", "Nurse <nurse@kilo.example.fi>", "After the visit", "The doctor diagnosed Leo with epilepsy."),
    ("1905", "gmail", "Teacher <teacher@kilo.example.fi>", "Signature needed",
     "Mia's individual learning plan is ready to sign."),
    ("1:903", "whatsapp", "Sam", None, "Two older boys keep bullying Leo on the bus."),
    ("1:904", "whatsapp", "Sam", None, "The police came to school about Mia today."),
    ("1:905", "whatsapp", "王老师", None, "米娅确诊了多动症，可能会犯困"),
    ("1:906", "whatsapp", "王老师", None, "老师建议给小狮申请特殊教育支持"),
    ("1:907", "whatsapp", "李妈妈", None, "米娅在学校被欺负了"),
    ("1:908", "whatsapp", "李妈妈", None, "社工下周要来家访"),
]


def sensitive_night(h) -> None:
    """The normal night with a sensitive message from each category in each language added."""
    normal_night(h)
    for ext_id, source, sender, subject, body in SENSITIVE:
        h.sources[source].append(msg(source, ext_id, "2026-09-27T16:00:00+03:00", body, sender=sender,
                                     subject=subject, chat="3B parents" if source == "whatsapp" else None,
                                     kid=None if source == "gmail" else "Mia"))


def test_a_sensitive_message_in_any_language_reaches_no_model_call(harness):
    sensitive_night(harness)
    harness.config["email"]["to"] = ["parent@example.com", PARTNER_EN]
    harness.model_reply = [harness.model_reply, copy.deepcopy(ENGLISH_REPLY)]

    assert harness.run() == 0

    summarize_call, translate_call = harness.model_calls
    for call in (summarize_call, translate_call):
        sent = call.stdin + json.dumps(call.argv, ensure_ascii=False)
        for ext_id, _source, _sender, subject, body in SENSITIVE:
            assert ext_id not in sent and body not in sent
            assert subject is None or subject not in sent
    assert payload_message_ids(harness, 0) == NORMAL_NIGHT_IDS
    zh, en = harness.sent  # each Recipient's Brief lists them all, in their own language
    for email, heading in ((zh, "🔒 未交给 AI 的消息"), (en, "🔒 Held-back messages")):
        listed = email.text.split(f"\n{heading}\n", 1)[1].split("\n\n", 1)[0].splitlines()[1:]
        assert len(listed) == len(SENSITIVE)
        for _id, source, sender, subject, _body in SENSITIVE:
            name = {"gmail": "Gmail", "wilma": "Wilma", "whatsapp": "WhatsApp"}[source]
            about = f"3B parents · {sender}" if source == "whatsapp" else f"{sender.split(' <')[0]} · {subject}"
            assert any(line.startswith(f"• {name} · {about} · ") for line in listed), (name, about)


WILMA_TENANT = "https://espoo.inschool.fi"
# What the Wilma Source puts in its ids once the family has signed in to WILMA_TENANT, so that
# two Wilmas' ids stay apart: the start of the address's SHA-256.
WILMA_TAG = hashlib.sha256(WILMA_TENANT.encode()).hexdigest()[:8]
SELVITYS_LINK = "https://mail.google.com/mail/u/0/#search/rfc822msgid:selvitys-12@kilo.example.fi"


def signed_in_to_wilma(h) -> None:
    """The wilma CLI's saved profile for the Wilma the family signed in to, as setup writes it."""
    config = h.home / ".config" / "wilmai" / "config.json"
    config.parent.mkdir(parents=True)
    profile_id = f"{WILMA_TENANT}|parent"
    config.write_text(json.dumps({"lastProfileId": profile_id, "profiles": [
        {"id": profile_id, "tenantUrl": WILMA_TENANT, "tenantName": "Espoo", "username": "parent",
         "students": []}]}))


def held_back_night(h, language: str = "zh") -> None:
    """The normal night with a sensitive message from Gmail, Wilma and WhatsApp, which the
    Sources give with what each has to find it again: Gmail its link, Wilma its ids."""
    normal_night(h, language)
    h.sources["gmail"].append(msg(
        "gmail", "1103", "2026-09-27T11:00:00+03:00",
        "Leon pedagoginen selvitys on valmis. Käydäänkö se läpi tiistaina?",
        sender="Koulupsykologi Laine <psykologi@kilo.example.fi>", subject="Leon koulunkäynti",
        url=SELVITYS_LINK))
    h.sources["wilma"].append(msg(
        "wilma", "message:812", "2026-09-27T13:00:00+03:00", "Miaa on kiusattu välitunneilla. Soitattehan minulle.",
        sender="Opettaja Virtanen", subject="Välituntitilanne", kid="Mia", metadata={"student_number": "7731905"}))
    h.sources["whatsapp"].append(msg(
        "whatsapp", "1:3", "2026-09-27T20:10:00+03:00", "米娅最近在学校被欺负了，老师说要找心理老师谈谈",
        sender="李妈妈", chat="3B parents", kid="Mia"))


HELD_BACK_BODIES = ["pedagoginen selvitys on valmis", "kiusattu", "被欺负了"]
# Each language's heading, and what it says for a WhatsApp message, which has no link.
HELD_BACK = {"en": ("🔒 Held-back messages", "read it in WhatsApp"),
             "zh": ("🔒 未交给 AI 的消息", "请在 WhatsApp 里查看"),
             "fi": ("🔒 Viestit, joita tekoäly ei nähnyt", "lue viesti lähteessä WhatsApp")}


@pytest.mark.parametrize("language", ["en", "zh", "fi"])
def test_the_brief_lists_each_held_back_message_by_source_sender_and_subject_with_its_link(
        harness, golden, language):
    held_back_night(harness, language)
    signed_in_to_wilma(harness)

    assert harness.run() == 0

    [email] = harness.sent
    heading, in_whatsapp = HELD_BACK[language]
    assert f"\n{heading}\n" in email.text
    assert f"• Gmail · Koulupsykologi Laine · Leon koulunkäynti · {SELVITYS_LINK}" in email.text
    assert f"• Wilma · Opettaja Virtanen · Välituntitilanne · {WILMA_TENANT}/!7731905/messages/812" in email.text
    assert f"• WhatsApp · 3B parents · 李妈妈 · {in_whatsapp}" in email.text
    assert f'<a href="{escape(SELVITYS_LINK)}">' in email.html
    assert f'<a href="{WILMA_TENANT}/!7731905/messages/812">' in email.html
    for body in HELD_BACK_BODIES:  # listed, never quoted, and never sent to the model
        assert body not in email.text and body not in email.html and body not in harness.model_prompt()
    golden(f"held_back.{language}.txt", email.text)
    golden(f"held_back.{language}.html", html_for_golden(email.html))


def test_a_night_with_only_held_back_messages_still_sends_a_brief_that_lists_them(harness):
    harness.config["summary_language"] = "en"
    signed_in_to_wilma(harness)
    harness.sources = {"wilma": [msg(
        "wilma", f"message:{WILMA_TAG}:812", "2026-09-27T13:00:00+03:00", "Miaa on kiusattu välitunneilla.",
        sender="Opettaja Virtanen", subject="Välituntitilanne", kid="Mia", metadata={"student_number": "7731905"})]}

    assert harness.run() == 0

    assert harness.model_calls == []
    [email] = harness.sent
    assert email.text == (
        "👨‍👩‍👧‍👦 Parent Recap Sun 27 Sep\n\n"
        "🔒 Held-back messages\n"
        "These messages looked sensitive, such as health, support, bullying or child welfare, so they were "
        "not sent to Claude. Read them where they came from.\n"
        f"• Wilma · Opettaja Virtanen · Välituntitilanne · {WILMA_TENANT}/!7731905/messages/812\n\n"
        "📥 Read tonight: Gmail 0 messages · MyClub 0 events · Wilma 1 message · WhatsApp 0 messages")
    assert f"message:{WILMA_TAG}:812" in harness.state()["seen_message_ids"]["wilma"]  # not listed again tomorrow
    assert "Written by" not in email.html  # no model wrote any of it


def test_held_back_messages_are_kept_in_the_archive_like_the_others(harness):
    held_back_night(harness)

    assert harness.run() == 0

    raw = json.loads((harness.archive_dir / "2026-09-27.raw.json").read_text())
    archived = {m["external_id"]: m["body"] for m in raw["messages"]}
    assert sorted(archived) == sorted([*NORMAL_NIGHT_IDS, "1103", "message:812", "1:3"])
    assert "Miaa on kiusattu välitunneilla" in archived["message:812"]
    assert raw["summary"]["_held_back"] == ["1103", "message:812", "1:3"]  # what the AI never saw
    assert "Välituntitilanne" in (harness.archive_dir / "2026-09-27.md").read_text()


def test_without_the_model_the_raw_list_leaves_held_back_messages_to_their_own_list(harness):
    held_back_night(harness)
    harness.config["email"]["to"] = ["parent@example.com", PARTNER_EN]
    harness.model_error = "Error: something went wrong"

    assert harness.run() == 0

    zh, en = harness.sent
    for email, heading in ((zh, "🔒 未交给 AI 的消息"), (en, "🔒 Held-back messages")):
        assert heading in email.text and "Retki Nuuksioon" in email.text  # the fallback's raw list
        for body in HELD_BACK_BODIES:
            assert body not in email.text and body not in email.html
        assert email.text.count("Välituntitilanne") == 1


def test_with_the_ai_filter_off_sensitive_messages_go_to_the_model_as_before(harness):
    held_back_night(harness)
    harness.config["ai_filter"] = {"enabled": False}

    assert harness.run() == 0

    prompt = harness.model_prompt()
    for body in HELD_BACK_BODIES:
        assert body in prompt
    assert payload_message_ids(harness) == sorted([*NORMAL_NIGHT_IDS, "1103", "message:812", "1:3"])
    [email] = harness.sent
    assert "🔒" not in email.text and "🔒" not in email.html


def test_summarizing_an_archived_night_by_hand_holds_back_its_sensitive_messages(harness):
    held_back_night(harness)
    assert harness.run() == 0
    raw = harness.archive_dir / "2026-09-27.raw.json"

    assert harness.cli("summarize", "--input", str(raw)) == 0

    prompt = harness.model_prompt()
    for body in HELD_BACK_BODIES:
        assert body not in prompt
    assert payload_message_ids(harness) == NORMAL_NIGHT_IDS


def test_a_held_back_message_without_a_link_says_where_to_read_it_and_cannot_inject_html(harness):
    harness.config["summary_language"] = "en"  # and no Wilma signed in to on this Mac
    harness.sources = {
        "wilma": [msg("wilma", "message:813", "2026-09-27T13:00:00+03:00", "Leon HOJKS päivitetään.",
                      sender="Opettaja Virtanen", subject="<b>HOJKS</b>", kid="Leo",
                      metadata={"student_number": "7731906"})]}

    assert harness.run() == 0

    [email] = harness.sent
    assert "• Wilma · Opettaja Virtanen · <b>HOJKS</b> · read it in Wilma" in email.text
    assert "<li>Wilma · Opettaja Virtanen · &lt;b&gt;HOJKS&lt;/b&gt; · read it in Wilma</li>" in email.html


def test_the_setup_pages_preview_lists_held_back_messages_too(harness, capsys):
    held_back_night(harness, "en")

    assert harness.run("--preview") == 0

    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
    made = next(line for line in lines if line["preview"] == "made")
    assert "🔒 Held-back messages" in made["html"] and "🔒 Held-back messages" in made["text"]
    for body in HELD_BACK_BODIES:
        assert body not in harness.model_prompt()
    assert harness.sent == []


def test_the_setup_pages_preview_leaves_one_line_in_the_installs_log_without_message_content(harness):
    held_back_night(harness, "en")
    harness.sources["whatsapp"] = RuntimeError("wacli: store locked by psykologi@kilo.example.fi")

    assert harness.run("--preview", "--lookback-hours", "72") == 0

    log = (harness.archive_dir / "logs" / "run-stderr.log").read_text(encoding="utf-8")
    [line] = log.splitlines()
    assert re.fullmatch(
        r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d INFO family_brief \| Setup preview: "
        r"read gmail 3, myclub 1, wilma 2, whatsapp failed; 2 held back; "
        r"Digest 161 chars, 3 Notices, 3 Action Items, 1 new events", line), line
    for m in [*harness.sources["gmail"], *harness.sources["wilma"]]:
        for content in (m.body, m.sender, m.subject, m.chat_name):
            assert not content or content not in line
    for content in ("Mia", "Leo", "psykologi", "kilo.example.fi", "Nuuksio", "Retki", "Parent Recap ·"):
        assert content not in line
    if os.name != "nt":  # made by the preview, before any evening job: still owner-only
        assert (harness.archive_dir / "logs").stat().st_mode & 0o077 == 0
    assert harness.sent == []


def test_the_setup_pages_preview_logs_that_the_model_failed_without_its_error(harness):
    normal_night(harness, "en")
    harness.model_error = "Error: quota exceeded for teacher.3b@kilo.example.fi"

    assert harness.run("--preview") == 0

    [line] = (harness.archive_dir / "logs" / "run-stderr.log").read_text(encoding="utf-8").splitlines()
    assert line.endswith("; the model failed, so it lists the messages"), line
    assert "quota" not in line and "kilo.example.fi" not in line


def test_a_newsletter_listing_its_staff_and_a_camp_notice_about_medication_still_reach_the_model(harness):
    normal_night(harness)
    newsletter = ("Viikkotiedote 40\nTorstaina retki Nuuksioon.\n\nYhteystiedot:\n"
                  "Kuraattori Maija Laine, 040 123 4567\nKoulupsykologi Pekka Virtanen, pekka.virtanen@kilo.example.fi")
    harness.sources["wilma"].append(msg(
        "wilma", "news:41", "2026-09-27T09:00:00+03:00", newsletter, sender="Rehtori", subject="Viikkotiedote 40",
        metadata={"student_number": "7731905"}))
    harness.sources["gmail"].append(msg(
        "gmail", "1104", "2026-09-27T09:30:00+03:00",
        "Leirikoulu: ilmoitattehan opettajalle lapsen lääkityksestä ja allergioista perjantaihin mennessä.",
        sender="teacher.3b@kilo.example.fi", subject="Leirikoulu"))

    assert harness.run() == 0

    assert payload_message_ids(harness) == sorted([*NORMAL_NIGHT_IDS, "1104", "news:41"])
    [email] = harness.sent
    assert "🔒" not in email.text and "🔒" not in email.html


# Made-up notification emails from the town's Wilma, read through Gmail: one with only
# announcements, and one that also copies a message to the Household.
WILMA_ANNOUNCEMENTS = ("Uudet tiedotteet (2):\nPoliisi muistuttaa liikenteestä\n"
                       "Poliisi valvoo ensi viikolla koulun ympäristön liikennettä.\n\n"
                       "Kiusaamisen vastainen viikko\nViikolla 41 puhumme kaikissa luokissa kiusaamisesta.")
WILMA_MESSAGE_COPY = ("Uudet viestit (1):\nOpettaja Virtanen: Välituntitilanne\n"
                      "Miaa on kiusattu välitunneilla. Soitattehan minulle.\n\n" + WILMA_ANNOUNCEMENTS)


def test_announcements_and_mass_email_reach_the_model_and_a_message_to_the_household_does_not(harness):
    normal_night(harness, "en")
    signed_in_to_wilma(harness)
    harness.sources["wilma"] += [
        msg("wilma", f"news:{WILMA_TAG}:41", "2026-09-27T09:00:00+03:00",
            "Poliisi muistuttaa: koulun takana oleva aidattu alue on suljettu.", sender="Rehtori Saarinen",
            subject="Poliisin tiedote", metadata={"student_number": "7731905"}),
        msg("wilma", f"message:{WILMA_TAG}:812", "2026-09-27T13:00:00+03:00", "Miaa on kiusattu välitunneilla.",
            sender="Opettaja Virtanen", subject="Välituntitilanne", kid="Mia",
            metadata={"student_number": "7731905", "addressee_count": 2}),
        # The wilma CLI names its 34 addressees: every guardian of the class.
        msg("wilma", f"message:{WILMA_TAG}:813", "2026-09-27T15:00:00+03:00",
            "Huoltajakysely on auki 9.10. asti. Kiusaamiseen puututaan aina.", sender="Opettaja Virtanen",
            subject="Huoltajakysely", kid="Mia", metadata={"student_number": "7731905", "addressee_count": 34})]
    harness.sources["gmail"] += [
        msg("gmail", "1103", "2026-09-27T09:30:00+03:00",
            "Iltapäivätoiminnan haku päättyy perjantaina 2.10. Erityisen tuen oppilaat hakevat samalla lomakkeella.",
            sender="Kilon kaupunki <info@kilo.example.fi>", subject="Iltapäivätoiminnan haku",
            metadata={"mailing_list": True}),
        msg("gmail", "1104", "2026-09-27T10:00:00+03:00", WILMA_ANNOUNCEMENTS,
            sender="Wilma <noreply@kilo.example.fi>", subject="Viesti Wilmasta"),
        msg("gmail", "1105", "2026-09-27T14:00:00+03:00", WILMA_MESSAGE_COPY,
            sender="Wilma <noreply@kilo.example.fi>", subject="Viesti Wilmasta")]

    assert harness.run() == 0

    # Signed in to, the Wilma's tag is in the normal night's Wilma id too.
    normal_ids = [f"message:{WILMA_TAG}:55" if i == "message:55" else i for i in NORMAL_NIGHT_IDS]
    assert payload_message_ids(harness) == sorted([*normal_ids, "1103", "1104", f"message:{WILMA_TAG}:813",
                                                   f"news:{WILMA_TAG}:41"])
    prompt = harness.model_prompt()
    for text in ("aidattu alue", "Erityisen tuen oppilaat", "Kiusaamisen vastainen viikko",
                 "Kiusaamiseen puututaan"):
        assert text in prompt
    assert "Miaa on kiusattu" not in prompt
    [email] = harness.sent
    listed = email.text.split("\n🔒 Held-back messages\n", 1)[1].split("\n\n", 1)[0].splitlines()[1:]
    assert listed == ["• Gmail · Wilma · Viesti Wilmasta · "
                      "https://mail.google.com/mail/u/0/#search/rfc822msgid:1105@mail.gmail.com",
                      f"• Wilma · Opettaja Virtanen · Välituntitilanne · {WILMA_TENANT}/!7731905/messages/812"]
