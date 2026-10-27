"""The nightly `parent-recap run`'s AI filter, end to end (ADR 0013): contact details, Third
Parties' names and the Kids' own names reach the AI only as placeholders, and pilot feedback links
carry the text as the AI saw it. Assertions are only on what leaves the system: the email (text,
HTML, attachments), the model command line, and persisted state."""
from __future__ import annotations

import dataclasses
import json
import re
from datetime import timedelta
from typing import Callable
from urllib.parse import unquote

import pytest
import time_machine

from conftest import NOW, msg, myclub_event

from family_brief import feedback
from nights import (FEEDBACK, PARTNER_EN, archived_summary, chinese_reply, feedback_links,
                    model_call_for_golden, normal_night, payload_message_ids, placeholders_in, two_languages)


# ── Contact details reach the AI only as placeholders (ADR 0013)

TEACHER = "maija.opettaja@kilo.example.fi"
OFFICE = "office@kilo.example.fi"
RETKI_FORM = "https://forms.kilo.example.fi/retki?id=42"
SIGNUP = "www.kilo-fc.fi/signup"
KIT_LIST = "https://kilo-fc.example.fi/kit"
CONTACT_DETAILS = [TEACHER, OFFICE, RETKI_FORM, "040 123 4567", "+358 9 816 2000", SIGNUP, "050-765 4321",
                   KIT_LIST, "kilo.example.fi"]
GMAIL_LINK = "https://mail.google.com/mail/u/0/#search/rfc822msgid:retki-201@kilo.example.fi"
STUDENT_NUMBER = "7731905"


def contact_night(h) -> None:
    """A night whose every part has contact details: each Source's messages, the queued MyClub
    events and last night's Brief. The Gmail message has its link back and the Wilma one the
    Kid's student number, as the Sources give them."""
    h.config["summary_language"] = "en"
    h.sources = {
        "gmail": [msg("gmail", "1201", "2026-09-27T08:15:00+03:00",
                      f"Sign up for the retki at {RETKI_FORM} or call 040 123 4567. Questions to {OFFICE}.",
                      sender=f"Maija Opettaja <{TEACHER}>", subject="Retki", url=GMAIL_LINK)],
        "wilma": [msg("wilma", "message:301", "2026-09-27T12:00:00+03:00",
                      "If Leo is ill, call the school nurse on +358 9 816 2000.",
                      sender="Terveydenhoitaja", subject="Sairauspoissaolot", kid="Leo",
                      metadata={"student_number": STUDENT_NUMBER})],
        "whatsapp": [msg("whatsapp", "1:401", "2026-09-27T19:00:00+03:00",
                         f"Football sign-up is at {SIGNUP}, or text me on 050-765 4321",
                         sender="Anna", chat="3B parents", kid="Mia")],
        "myclub": [dataclasses.replace(myclub_event("mc-1", "FC Kilo P2017 vs HJK", "2026-10-03T07:00:00+00:00",
                                                    "2026-10-03T08:30:00+00:00"),
                                       description=f"Kit list: {KIT_LIST}")],
    }
    h.write_archive("2026-09-26", {"delivered": True, "summary": {"per_kid": [{"kid": "Mia", "notices": [
        {"text": f"The lost-property box is at the office, {OFFICE}", "refs": ["g-200"]}], "action_items": []}]}})


def test_the_brief_prompt_has_placeholders_in_place_of_contact_details(harness):
    contact_night(harness)

    assert harness.run() == 0

    prompt = harness.model_prompt(0)
    for value in CONTACT_DETAILS:
        assert value not in prompt
    payload = harness.model_payload(0)
    gmail, wilma, whatsapp = (next(m for m in payload["messages"] if m["external_id"] == i)
                              for i in ("1201", "message:301", "1:401"))
    # The display name's person and the address each have one placeholder everywhere (#191).
    assert re.fullmatch(rf"⟦N\d⟧ Opettaja <{gmail['metadata']['sender_email']}>", gmail["sender"])
    assert re.fullmatch(r"⟦E\d⟧", gmail["metadata"]["sender_email"])
    assert re.fullmatch(r"Sign up for the retki at ⟦L\d⟧ or call ⟦P\d⟧\. Questions to ⟦E\d⟧\.", gmail["body"])
    assert re.fullmatch(r"If Kid B is ill, call the school nurse on ⟦P\d⟧\.", wilma["body"])
    assert re.fullmatch(r"Football sign-up is at ⟦L\d⟧, or text me on ⟦P\d⟧", whatsapp["body"])
    assert re.fullmatch(r"Kit list: ⟦L\d⟧", payload["already_queued_for_calendar"][0]["description"])
    assert re.fullmatch(r"The lost-property box is at the office, ⟦E\d⟧",
                        payload["earlier_briefs"][0]["per_kid"][0]["notices"][0]["text"])
    # Identifiers the model doesn't need aren't sent, and the ids it cites are sent as they are.
    assert "url" not in gmail and "rfc822msgid" not in prompt and "mail.google.com" not in prompt
    assert "student_number" not in wilma["metadata"] and STUDENT_NUMBER not in prompt
    assert payload_message_ids(harness, 0) == ["1201", "1:401", "message:301"]
    assert payload["earlier_briefs"][0]["per_kid"][0]["notices"][0]["refs"] == ["g-200"]
    assert "placeholders such as ⟦N1⟧, ⟦P1⟧, ⟦E1⟧ and ⟦L1⟧" in harness.model_calls[0].system_prompt


def reply_with_placeholders(prompt: str) -> dict:
    """The contact night as a model writes it: with the placeholders it was given."""
    body = {m["external_id"]: m["body"] for m in json.loads(prompt[prompt.index("\n{") + 1:])["messages"]}
    form, phone, _office = placeholders_in(body["1201"])
    [nurse] = placeholders_in(body["message:301"])
    signup, _anna = placeholders_in(body["1:401"])
    return {
        "per_kid": [
            {"kid": "Mia", "notices": [], "action_items": [
                {"what": f"Sign up for the retki at {form} or call {phone}", "by": "2026-09-29", "who": "Either",
                 "refs": ["1201"]}]},
            {"kid": "Leo", "notices": [{"text": f"If Leo is ill, call the school nurse on {nurse}", "refs": ["message:301"]}],
             "action_items": []},
        ],
        "calendar_events": [
            {"kid": "Mia", "title": "Retki", "start": "2026-10-01T09:00:00", "description": f"Sign up: {form}",
             "refs": ["1201"]},
            # The retki's link pinned to an event whose message never had it.
            {"kid": "Mia", "title": "Football sign-up", "start": "2026-10-02T17:00:00",
             "description": f"Sign up at {signup} or {form}", "refs": ["1:401"]},
        ],
        "message_digest": f"**Mia**\n- Retki: sign up at {form}\n\n**Leo**\n- Ill? Call the nurse on {nurse}",
    }


def test_the_brief_calendar_and_archive_get_the_real_contact_details_back(harness):
    contact_night(harness)
    harness.config["imessage"] = {"enabled": True, "recipients": ["+358401111111"]}
    harness.model_reply = reply_with_placeholders

    assert harness.run() == 0

    [email] = harness.sent
    [(_, imessage)] = harness.imessages
    item = f"Sign up for the retki at {RETKI_FORM} or call 040 123 4567"
    for text in (email.text, email.html, imessage):
        assert item in text
        assert "+358 9 816 2000" in text
        assert not placeholders_in(text)
    assert f"Retki: sign up at {RETKI_FORM}" in email.text
    ics = email.attachment(".ics")[1].decode().replace("\r\n ", "")  # unfolded
    assert f"DESCRIPTION:Sign up: {RETKI_FORM}\\n" in ics
    # A link is kept in an event only if a message the event cites has it, checked on the real link.
    assert f"DESCRIPTION:Sign up at {SIGNUP} or \\n" in ics
    summary = archived_summary(harness)
    assert summary["per_kid"][0]["action_items"][0]["what"] == item
    assert summary["calendar_events"][0]["description"] == f"Sign up: {RETKI_FORM}"
    assert summary["calendar_events"][1]["description"] == f"Sign up at {SIGNUP} or "
    assert item in (harness.archive_dir / "2026-09-27.md").read_text()


def test_the_translation_prompt_has_the_same_placeholders_and_the_translation_the_values(harness):
    contact_night(harness)
    harness.config["ai_filter"] = {"mask_kids": False}  # the Kids' names reach the model
    harness.config["summary_language"] = "zh"
    harness.config["email"]["to"] = ["parent@example.com", PARTNER_EN]
    # The translation keeps every placeholder, as its instructions ask.
    harness.model_reply = [reply_with_placeholders, json.loads]

    assert harness.run() == 0

    prompt = harness.model_prompt(1)
    for value in CONTACT_DETAILS:
        assert value not in prompt
    written = reply_with_placeholders(harness.model_prompt(0))
    sent = json.loads(prompt)
    assert sent["per_kid"][0]["action_items"][0]["what"] == written["per_kid"][0]["action_items"][0]["what"]
    assert sent["message_digest"] == written["message_digest"]
    assert "placeholders such as ⟦N1⟧, ⟦P1⟧, ⟦E1⟧ and ⟦L1⟧" in harness.model_calls[1].system_prompt
    _zh, en = harness.sent
    assert f"Sign up for the retki at {RETKI_FORM} or call 040 123 4567" in en.text
    assert "Ill? Call the nurse on +358 9 816 2000" in en.html and not placeholders_in(en.html)
    ics = en.attachment(".ics")[1].decode().replace("\r\n ", "")
    assert f"DESCRIPTION:Sign up: {RETKI_FORM}\\n" in ics
    assert f"DESCRIPTION:Sign up at {SIGNUP} or \\n" in ics


LAOSHI = "laoshi@zhongwen.example.com"
ILMO = "https://kilo-fc.example.fi/ilmo"
CHINESE_DETAILS = ["13800138000", LAOSHI, ILMO, "040 765 4321", "040 123 4567", OFFICE,
                   "www.zhongwen.example.com/kebiao", "kilo-fc.example.fi", "zhongwen.example.com"]


def chinese_night(h) -> None:
    """Contact details inside Chinese text with no space around them, as Chinese parents write it."""
    h.config["summary_language"] = "zh"
    h.sources = {
        "gmail": [msg("gmail", "1601", "2026-09-27T10:00:00+03:00",
                      f"请联系040 123 4567或发邮件到{OFFICE}。课表见www.zhongwen.example.com/kebiao。",
                      sender=f"中文学校 <{LAOSHI}>", subject="中文课")],
        "whatsapp": [msg("whatsapp", "2:501", "2026-09-27T18:00:00+03:00", "周六中文课请联系13800138000报名",
                         sender="王老师", chat="Leo piano", kid="Leo"),
                     msg("whatsapp", "1:502", "2026-09-27T19:00:00+03:00",
                         f"请在{ILMO}报名，或致电040 765 4321。", sender="Anna", chat="3B parents", kid="Mia")],
    }


def test_contact_details_inside_chinese_text_become_placeholders_and_the_text_stays(harness):
    chinese_night(harness)

    assert harness.run() == 0

    prompt = harness.model_prompt(0)
    for value in CHINESE_DETAILS:
        assert value not in prompt
    body = {m["external_id"]: m["body"] for m in harness.model_payload(0)["messages"]}
    assert re.fullmatch(r"请联系⟦P\d⟧或发邮件到⟦E\d⟧。课表见⟦L\d⟧。", body["1601"])
    assert re.fullmatch(r"周六中文课请联系⟦P\d⟧报名", body["2:501"])
    assert re.fullmatch(r"请在⟦L\d⟧报名，或致电⟦P\d⟧。", body["1:502"])


def test_a_chinese_brief_gets_its_values_back_and_its_translation_prompt_has_none(harness):
    chinese_night(harness)
    harness.config["email"]["to"] = ["parent@example.com", PARTNER_EN]
    harness.model_reply = [chinese_reply, json.loads]  # the translation keeps every placeholder

    assert harness.run() == 0

    zh, en = harness.sent
    for email in (zh, en):
        assert "打13800138000给中文课报名，或致电040 123 4567" in email.text
        assert f"足球在{ILMO}报名，也可以WhatsApp040 765 4321联系Anna" in email.text
        assert not placeholders_in(email.html)
    assert archived_summary(harness)["per_kid"][0]["notices"][0]["text"] == \
        f"足球在{ILMO}报名，也可以WhatsApp040 765 4321联系Anna"
    prompt = harness.model_prompt(1)
    for value in CHINESE_DETAILS:
        assert value not in prompt
    ics = zh.attachment(".ics")[1].decode().replace("\r\n ", "")
    assert f"DESCRIPTION:报名链接：{ILMO}，周五截止\\n" in ics


FULL_WIDTH_DIGITS = str.maketrans("0123456789", "０１２３４５６７８９")
CYRILLIC = {"P": "Р", "E": "Е"}  # letters that look like P and E


def written_as(write) -> Callable[[str], dict]:
    """The contact night's reply, with 1201's placeholders in its Action Item as `write` gives them back."""
    def reply(prompt: str) -> dict:
        written = reply_with_placeholders(prompt)
        body = next(m["body"] for m in json.loads(prompt[prompt.index("\n{") + 1:])["messages"]
                    if m["external_id"] == "1201")
        form, phone, office = (write(token[1:-1]) for token in placeholders_in(body))
        written["per_kid"][0]["action_items"][0]["what"] = f"Sign up at {form}, call {phone} or write to {office}"
        return written
    return reply


@pytest.mark.parametrize("write", [
    pytest.param(lambda p: f"⟦ {p[0]} {p[1:]} ⟧", id="spaced"),
    pytest.param(lambda p: f"【{p[0].lower()}{p[1:].translate(FULL_WIDTH_DIGITS)}】", id="lower-case-full-width"),
    pytest.param(lambda p: f"[{p}]", id="square"),
    pytest.param(lambda p: f"({p})", id="round"),
    pytest.param(lambda p: f"（{p}）", id="full-width-round"),
    pytest.param(lambda p: f"〔{p}〕", id="tortoise-shell"),
    pytest.param(lambda p: f"⟦{CYRILLIC.get(p[0], p[0])}{p[1:]}⟧", id="cyrillic-letter"),
    pytest.param(lambda p: f"{p}⟧", id="no-opening-bracket"),
])
def test_a_placeholder_written_another_way_still_gets_its_value(harness, write):
    contact_night(harness)
    harness.model_reply = written_as(write)

    assert harness.run() == 0

    [email] = harness.sent
    assert f"Sign up at {RETKI_FORM}, call 040 123 4567 or write to {OFFICE} (Mia)" in email.text


@pytest.mark.parametrize("written, shown", [("[P9]", "a phone number"), ("〔L9〕", "a link"),
                                            ("（Е9）", "an email address"), ("⟦Р9⟧", "a phone number")])
def test_a_placeholder_written_another_way_with_no_value_is_shown_as_its_kind(harness, written, shown):
    contact_night(harness)
    harness.model_reply = written_as(lambda _p: written)

    assert harness.run() == 0

    [email] = harness.sent
    assert f"Sign up at {shown}, call {shown} or write to {shown} (Mia)" in email.text


def test_text_that_only_looks_like_a_placeholder_stays_as_written(harness):
    contact_night(harness)  # its MyClub event is FC Kilo P2017's match

    def reply(prompt: str) -> dict:
        written = reply_with_placeholders(prompt)
        written["per_kid"][0]["action_items"][0]["what"] = "Park at P1 for the FC Kilo (P2017) match"
        return written
    harness.model_reply = reply

    assert harness.run() == 0

    [email] = harness.sent
    assert "Park at P1 for the FC Kilo (P2017) match (Mia)" in email.text


@pytest.mark.parametrize("language, phone_number", [("en", "a phone number"), ("zh", "一个电话号码"),
                                                    ("fi", "puhelinnumero")])
def test_a_placeholder_the_model_changed_beyond_repair_is_shown_as_its_kind_and_the_item_stays(
        harness, language, phone_number):
    contact_night(harness)
    harness.config["summary_language"] = language

    def reply(prompt: str) -> dict:
        written = reply_with_placeholders(prompt)
        written["per_kid"][0]["action_items"][0]["what"] = "Call ⟦P9⟧ to sign up for the retki"
        return written
    harness.model_reply = reply

    assert harness.run() == 0

    [email] = harness.sent
    assert f"Call {phone_number} to sign up for the retki" in email.text
    assert "⟦" not in email.html
    assert archived_summary(harness)["per_kid"][0]["action_items"][0]["what"] == \
        f"Call {phone_number} to sign up for the retki"


def test_a_placeholder_a_translation_changed_is_shown_as_its_kind_in_that_language(harness):
    contact_night(harness)
    harness.config["summary_language"] = "zh"
    harness.config["email"]["to"] = ["parent@example.com", PARTNER_EN]

    def translation(prompt: str) -> dict:
        translated = json.loads(prompt)
        translated["per_kid"][0]["action_items"][0]["what"] = "Sign up for the retki at ⟦L 9⟧"
        return translated
    harness.model_reply = [reply_with_placeholders, translation]

    assert harness.run() == 0

    zh, en = harness.sent
    assert "Sign up for the retki at a link (Mia)" in en.text
    assert f"Sign up for the retki at {RETKI_FORM} or call 040 123 4567 (Mia)" in zh.text


# ── Third Parties' names reach the AI only as placeholders (ADR 0013, #191)

# Parts of tonight's Third Parties' names, in any case form: a coach, a teacher, a pupil whose
# mother writes in the class group, a parent, a pupil in a Chinese parent's WhatsApp name, and a
# pupil whose first name is also the Kid Leo's genitive.
THIRD_PARTIES = ["Juha", "Lahtin", "Lahtis", "Maija", "Virtan", "Virtas", "Eetu", "Toivo", "Mäkelä", "小明",
                 "Mäkin", "Mäkis"]


def names_night(h) -> None:
    """A Finnish Brief for a night whose messages name a coach (whose sender names his club too), a
    teacher, other pupils and other parents, in Finnish case forms and inside Chinese text with no
    spaces, and a message from the partner, a Recipient, under her own name. The partner reads
    English."""
    h.config["summary_language"] = "fi"
    h.config["email"]["to"] = ["parent@example.com", PARTNER_EN]
    h.sources = {
        "gmail": [msg("gmail", "1701", "2026-09-27T08:00:00+03:00",
                      "Hi! Juha here. Training moves to Tuesday. Questions to Juha or Lahtisen apuvalmentaja.",
                      sender='"Juha Lahtinen, Kilo FC" <juha.lahtinen@kilo-fc.example.fi>', subject="Training"),
                  msg("gmail", "1702", "2026-09-27T09:00:00+03:00", "Hanna picks Leo up on Monday.",
                      sender="Hanna Parent <partner@example.com>", subject="Monday"),
                  msg("gmail", "1703", "2026-09-27T09:30:00+03:00", "Science fair on Friday. Greetings, Kilo News",
                      sender="Kilo News <news@kilo.example.fi>", subject="Science fair")],
        "wilma": [msg("wilma", "message:701", "2026-09-27T12:00:00+03:00",
                      "Retki torstaina. Palauttakaa lupalappu Maijalle tai Virtaselle. Eetu ja Mia ovat samassa "
                      "ryhmässä.", sender="Virtanen Maija", subject="Retki", kid="Mia")],
        "whatsapp": [
            msg("whatsapp", "1:701", "2026-09-27T18:00:00+03:00", "Eetulle synttärit lauantaina! Mia ja Leo tervetuloa.",
                sender="Eetun äiti", chat="3B parents", kid="Mia"),
            msg("whatsapp", "1:702", "2026-09-27T18:30:00+03:00", "Kiitos Maijalle retkestä! Onni-koira tulee mukaan.",
                sender="Toivo Mäkelä", chat="3B parents", kid="Mia"),
            msg("whatsapp", "1:703", "2026-09-27T19:00:00+03:00", "小明和米娅周六一起去Juha教练的足球课",
                sender="王小明妈妈", chat="3B parents", kid="Mia"),
            msg("whatsapp", "2:704", "2026-09-27T19:30:00+03:00", "Mäkisen perhe tuo kakun. Leon reppu jäi kouluun.",
                sender="Leon Mäkinen", chat="Leo piano", kid="Leo"),
        ],
    }


def people_in(text: str) -> list[str]:
    return re.findall(r"⟦N\d+⟧", text)


def names_reply(prompt: str) -> dict:
    """The names night as a model writes it in Finnish: with the placeholders it was given, and a
    Finnish case ending after a colon."""
    sender = {m["external_id"]: m["sender"] for m in json.loads(prompt[prompt.index("\n{") + 1:])["messages"]}
    [coach], [teacher], [eetu], [toivo], [xiaoming] = (people_in(sender[i]) for i in
                                                       ("1701", "message:701", "1:701", "1:702", "1:703"))
    return {
        "per_kid": [
            {"kid": "Mia",
             "notices": [{"text": f"{eetu}:n synttärit lauantaina, {xiaoming} ja Mia menevät {coach}:n treeneihin",
                          "refs": ["1:701", "1:703"]}],
             "action_items": [{"what": f"Palauta retken lupalappu {teacher}:lle", "by": "2026-09-30",
                               "who": "Kumpi tahansa", "refs": ["message:701"]}]},
            {"kid": "Leo", "notices": [{"text": "Hanna hakee Leon maanantaina", "refs": ["1702"]}],
             "action_items": []},
        ],
        "calendar_events": [{"kid": "Mia", "title": f"{eetu}:n synttärit", "start": "2026-10-03",
                             "description": f"{toivo} kiitti {teacher}:a", "refs": ["1:701", "1:702"]}],
        "message_digest": f"**Mia**\n- Lupalappu {teacher}:lle\n- {eetu}:n synttärit lauantaina",
    }


def test_third_parties_names_reach_the_brief_prompt_only_as_placeholders(harness):
    names_night(harness)
    harness.config["ai_filter"] = {"mask_kids": False}  # the Kids' names reach the model
    harness.model_reply = [names_reply, json.loads]

    assert harness.run() == 0

    prompt = harness.model_prompt(0)
    for name in THIRD_PARTIES:
        assert name not in prompt
    messages = {m["external_id"]: m for m in harness.model_payload(0)["messages"]}
    # One placeholder per person: a name, its parts and their case forms, also inside Chinese text.
    coach, teacher, eetu, toivo, xiaoming, leon = (people_in(messages[i]["sender"])[0] for i in
                                                   ("1701", "message:701", "1:701", "1:702", "1:703", "2:704"))
    assert len({coach, teacher, eetu, toivo, xiaoming, leon}) == 6
    # The club in the coach's sender stays, as an organisation and no one's name.
    assert re.fullmatch(rf'"{coach}, Kilo FC" <⟦E\d⟧>', messages["1701"]["sender"])
    assert messages["1701"]["body"] == \
        f"Hi! {coach} here. Training moves to Tuesday. Questions to {coach} or {coach}:n apuvalmentaja."
    assert messages["message:701"]["body"] == f"Retki torstaina. Palauttakaa lupalappu {teacher}:lle tai {teacher}:lle. " \
                                        f"{eetu} ja Mia ovat samassa ryhmässä."
    assert messages["1:701"]["sender"] == f"{eetu}:n äiti"
    assert messages["1:701"]["body"] == f"{eetu}:lle synttärit lauantaina! Mia ja Leo tervetuloa."
    # A newsletter's sender is no person, so its words stay.
    assert messages["1703"]["body"] == "Science fair on Friday. Greetings, Kilo News"
    # Only tonight's people: Onni is no one's name tonight.
    assert messages["1:702"]["body"] == f"Kiitos {teacher}:lle retkestä! Onni-koira tulee mukaan."
    # A Chinese name's given name alone is the same person.
    assert messages["1:703"] == {**messages["1:703"], "sender": f"{xiaoming}妈妈",
                                  "body": f"{xiaoming}和米娅周六一起去{coach}教练的足球课"}
    # Leon is a pupil's name, and the Kid Leo's genitive: Leo's backpack stays.
    assert messages["2:704"]["body"] == f"{leon}:n perhe tuo kakun. Leon reppu jäi kouluun."
    # The Household's own names still reach the model: the Kids, their aliases and the partner.
    assert messages["1702"]["sender"] == "Hanna Parent <⟦E2⟧>" and "Hanna picks Leo up" in messages["1702"]["body"]
    assert [k["name"] for k in harness.model_payload(0)["kid_profiles"]] == ["Mia", "Leo"]
    assert "米娅" in prompt and "小狮" in prompt
    assert "⟦N1⟧" in harness.model_calls[0].system_prompt


def test_the_brief_calendar_and_archive_get_the_names_back_and_the_translation_prompt_has_none(harness):
    names_night(harness)
    harness.config["ai_filter"] = {"mask_kids": False}  # the Kids' names reach the model
    harness.model_reply = [names_reply, json.loads]  # the translation keeps every placeholder

    assert harness.run() == 0

    fi, en = harness.sent
    # Each name comes back as the messages wrote it where the model copied that placeholder and
    # ending, and is made from the name the messages wrote where they never had that form.
    for email in (fi, en):
        assert "Palauta retken lupalappu Maijalle" in email.text
        assert "Eetun synttärit lauantaina" in email.text
        assert not people_in(email.html)
        ics = email.attachment(".ics")[1].decode().replace("\r\n ", "")
        assert "SUMMARY:Eetun synttärit" in ics and "DESCRIPTION:Toivo Mäkelä kiitti Virtanen Maijaa" in ics
    summary = archived_summary(harness)
    assert summary["per_kid"][0]["action_items"][0]["what"] == "Palauta retken lupalappu Maijalle"
    assert summary["per_kid"][0]["notices"][0]["text"] == \
        "Eetun synttärit lauantaina, 王小明 ja Mia menevät Lahtisen treeneihin"
    assert "Lupalappu Maijalle" in (harness.archive_dir / "2026-09-27.md").read_text()
    translation = harness.model_prompt(1)
    for name in THIRD_PARTIES:
        assert name not in translation
    assert "Hanna hakee Leon maanantaina" in translation


def test_an_earlier_briefs_names_reach_the_model_only_as_placeholders(harness):
    """Last night a teacher who doesn't write tonight sent a message, and its Brief names her."""
    names_night(harness)
    harness.write_archive("2026-09-26", {
        "delivered": True,
        "messages": [msg("wilma", "w-600", "2026-09-26T12:00:00+03:00", "Vanhempainilta tiistaina.",
                         sender="Niemi Sanna", subject="Vanhempainilta", kid="Leo").to_dict()],
        "summary": {"per_kid": [{"kid": "Leo", "notices": [], "action_items": [
            {"what": "Vastaa Sanna Niemelle vanhempainillasta (Niemen luokka)", "by": "2026-09-29",
             "refs": ["w-600"]}]}]}})
    harness.model_reply = [names_reply, json.loads]

    assert harness.run() == 0

    prompt = harness.model_prompt(0)
    assert "Sanna" not in prompt and "Niem" not in prompt
    [earlier] = harness.model_payload(0)["earlier_briefs"]
    assert re.fullmatch(r"Vastaa (⟦N\d⟧):lle vanhempainillasta \(\1:n luokka\)",
                        earlier["per_kid"][0]["action_items"][0]["what"])
    assert set(earlier) == {"date", "per_kid"}  # what the program knows of that night stays here


# ── The Kids' own names as Kid A and Kid B, behind a switch that is on by default (#268, #300)

def kids_masked_reply(prompt: str) -> dict:
    """The names night as a model writes it with the Kids as the placeholders it was given."""
    written = json.dumps(names_reply(prompt), ensure_ascii=False)
    return json.loads(written.replace("Mia", "Kid A").replace("Leon", "Kid B:n").replace("Leo", "Kid B"))


def test_with_the_kids_masked_no_kids_name_reaches_the_model_and_the_brief_has_them(harness):
    names_night(harness)
    harness.config["ai_filter"] = {"mask_kids": True}
    harness.model_reply = [kids_masked_reply, json.loads]

    assert harness.run() == 0

    for i in (0, 1):  # the Brief's call and the translation's
        prompt = harness.model_prompt(i)
        for name in [*KIDS, *THIRD_PARTIES]:
            assert name not in prompt
    payload = harness.model_payload(0)
    assert [(k["name"], k["aliases"]) for k in payload["kid_profiles"]] == [("Kid A", []), ("Kid B", [])]
    assert [(c["name"], c["kid"]) for c in payload["whatsapp_chat_map"]] == \
        [("3B parents", "Kid A"), ("Kid B piano", "Kid B")]
    messages = {m["external_id"]: m for m in payload["messages"]}
    [xiaoming] = people_in(messages["1:703"]["sender"])
    assert messages["1:701"]["body"].endswith("Kid A ja Kid B tervetuloa.")
    assert messages["1:703"]["body"].startswith(f"{xiaoming}和Kid A周六")
    assert messages["2:704"]["body"].endswith("Kid B:n reppu jäi kouluun.")  # Leo's, in the genitive
    assert "Kid A" in harness.model_calls[0].system_prompt
    fi, en = harness.sent
    assert "Palauta retken lupalappu Maijalle (Mia)" in fi.text and "**Mia**" in fi.text
    for email in (fi, en):
        assert "Kid A" not in email.html and "Kid B" not in email.html
    summary = archived_summary(harness)
    assert [k["kid"] for k in summary["per_kid"]] == ["Mia", "Leo"]
    assert summary["per_kid"][1]["notices"][0]["text"] == "Hanna hakee Leon maanantaina"


def test_with_the_kids_masked_a_kids_name_whose_stem_changes_goes_as_their_placeholder(harness):
    harness.config["summary_language"] = "fi"
    harness.config["email"]["to"] = ["parent@example.com"]
    harness.config["ai_filter"] = {"mask_kids": True}
    harness.config["kids"] = [{"name": "Elias Virta", "everyday_name": "Elias", "grade": 3},
                              {"name": "Pekka", "grade": 1}]
    harness.sources = {"whatsapp": [
        msg("whatsapp", "1:801", "2026-09-27T18:00:00+03:00",
            "Eliaksen ja Pekan reput jäivät kouluun. Eliakselle ja Pekalle uudet kumisaappaat.",
            sender="Opettaja", chat="3B parents")]}
    harness.model_reply = {
        "per_kid": [{"kid": "Kid B", "notices": [{"text": "Kid B:n reppu jäi kouluun", "refs": ["1:801"]}],
                     "action_items": [{"what": "Hae Kid B:lle reppu", "by": "2026-09-28", "refs": ["1:801"]}]}],
        "calendar_events": [],
        "message_digest": "**Kid B**\n- Kid B:n reppu jäi kouluun",
    }

    assert harness.run() == 0

    [message] = harness.model_payload(0)["messages"]
    assert message["body"] == "Kid A:n ja Kid B:n reput jäivät kouluun. Kid A:lle ja Kid B:lle uudet kumisaappaat."
    [email] = harness.sent
    assert "Hae Pekalle reppu (Pekka)" in email.text and "Pekan reppu jäi kouluun" in email.text


def test_without_the_switch_in_the_config_the_kids_go_as_kid_a_and_kid_b(harness):
    names_night(harness)
    harness.model_reply = [kids_masked_reply, json.loads]

    assert harness.run() == 0

    assert [k["name"] for k in harness.model_payload(0)["kid_profiles"]] == ["Kid A", "Kid B"]
    for name in KIDS:
        assert name not in harness.model_prompt(0)
    assert "**Mia**" in harness.sent[0].text


def test_the_kids_names_reach_the_model_when_the_switch_is_off(harness):
    names_night(harness)
    harness.config["ai_filter"] = {"mask_kids": False}
    harness.model_reply = [names_reply, json.loads]

    assert harness.run() == 0

    assert [k["name"] for k in harness.model_payload(0)["kid_profiles"]] == ["Mia", "Leo"]
    assert "Kid A" not in harness.model_prompt(0) and "Kid A" not in harness.model_calls[0].system_prompt


@pytest.mark.parametrize("language, written, shown", [
    ("en", "Return the slip to ⟦N9⟧", "Return the slip to someone"),
    ("zh", "把同意书交给⟦N9⟧", "把同意书交给某人"),
    ("fi", "Palauta lupalappu ⟦N9⟧:lle", "Palauta lupalappu henkilölle"),
])
def test_a_person_placeholder_the_model_changed_beyond_repair_is_said_in_each_language(harness, language, written,
                                                                                       shown):
    names_night(harness)
    harness.config["summary_language"] = language
    harness.config["email"]["to"] = ["parent@example.com"]

    def reply(prompt: str) -> dict:
        brief = names_reply(prompt)
        brief["per_kid"][0]["action_items"][0]["what"] = written
        return brief
    harness.model_reply = reply

    assert harness.run() == 0

    [email] = harness.sent
    assert f"{shown} (Mia)" in email.text
    assert "⟦" not in email.html


# normal_night.*.model.txt and two_languages.translate.model.txt are the prompts from before the
# AI filter, left as they were.
@pytest.mark.parametrize("language", ["en", "zh", "fi"])
def test_with_the_ai_filter_off_the_brief_prompt_is_as_before_the_filter(harness, golden, language):
    normal_night(harness, language)
    harness.config["ai_filter"] = {"enabled": False}

    assert harness.run() == 0

    [call] = harness.model_calls
    golden(f"normal_night.{language}.model.txt", model_call_for_golden(call))


def test_with_the_ai_filter_off_the_translation_prompt_is_as_before_the_filter(harness, golden):
    two_languages(harness)
    harness.config["ai_filter"] = {"enabled": False}

    assert harness.run() == 0

    _summarize, translate = harness.model_calls
    golden("two_languages.translate.model.txt", model_call_for_golden(translate))


def test_with_the_ai_filter_off_the_model_gets_the_messages_as_they_are(harness):
    contact_night(harness)
    harness.config["ai_filter"] = {"enabled": False}

    assert harness.run() == 0

    prompt = harness.model_prompt(0)
    for value in [*CONTACT_DETAILS, GMAIL_LINK, STUDENT_NUMBER]:
        assert value in prompt
    assert "⟦" not in prompt and "⟦" not in harness.model_calls[0].system_prompt


# ── Pilot feedback links carry the text as the AI saw it (ADR 0013, #195)

# The Household's own names that a feedback link never carries: the Kids' names and aliases.
KIDS = ["Mia", "Leo", "米娅", "小狮"]


def item_links(html: str) -> list[dict[str, str]]:
    """The pre-filled answers of each Action Item's ❌ link in the Brief."""
    return [answers for _, answers, _ in feedback_links(html) if answers["verdict"] == feedback.WRONG]


def test_a_feedback_link_has_an_item_as_the_ai_saw_it_without_the_teachers_or_kids_name(harness):
    names_night(harness)
    harness.config["feedback"] = FEEDBACK

    def reply(prompt: str) -> dict:
        brief = names_reply(prompt)
        [teacher] = people_in(brief["per_kid"][0]["action_items"][0]["what"])
        brief["per_kid"][0]["action_items"][0]["what"] = f"Palauta Mian retken lupalappu {teacher}:lle"
        return brief
    harness.model_reply = [reply, json.loads]

    assert harness.run() == 0

    fi, en = harness.sent
    assert "Palauta Mian retken lupalappu Maijalle" in fi.text  # the Brief itself has the names
    [teacher] = people_in(next(m["sender"] for m in harness.model_payload(0)["messages"] if m["external_id"] == "message:701"))
    for email in (fi, en):
        [item] = item_links(email.html)
        assert (item["item_text"], item["kid"]) == (f"Palauta Kid A:n retken lupalappu {teacher}:lle", "Kid A")
        for _, answers, _ in feedback_links(email.html):
            for name in [*THIRD_PARTIES, *KIDS]:
                assert name not in json.dumps(answers, ensure_ascii=False)


def test_a_feedback_link_masks_people_and_contact_details_inside_chinese_text_with_no_spaces(harness):
    chinese_night(harness)
    harness.config["feedback"] = FEEDBACK

    def reply(prompt: str) -> dict:
        brief = chinese_reply(prompt)
        wa_501 = next(m for m in json.loads(prompt[prompt.index("\n{") + 1:])["messages"] if m["external_id"] == "2:501")
        [wang], [mobile] = people_in(wa_501["sender"]), placeholders_in(wa_501["body"])
        brief["per_kid"][1]["action_items"][0]["what"] = f"周六前告诉{wang}老师小狮不去中文课，或打{mobile}"
        return brief
    harness.model_reply = reply

    assert harness.run() == 0

    [email] = harness.sent
    assert "周六前告诉王老师小狮不去中文课，或打13800138000" in email.text
    messages = {m["external_id"]: m for m in harness.model_payload(0)["messages"]}
    [wang], [mobile] = people_in(messages["2:501"]["sender"]), placeholders_in(messages["2:501"]["body"])
    [anna], [form, anna_phone] = people_in(messages["1:502"]["sender"]), placeholders_in(messages["1:502"]["body"])
    digest, item = (answers for _, answers, _ in feedback_links(email.html) if answers["verdict"] != feedback.SAVED)
    assert (item["item_text"], item["kid"]) == (f"周六前告诉{wang}老师Kid B不去中文课，或打{mobile}", "Kid B")
    # Anna, whom the model wrote out itself, goes as the placeholder the AI saw for her too.
    assert digest["item_text"] == \
        f"**Kid B**\n- 中文课报名打{mobile}\n\n**Kid A**\n- 足球在{form}报名，也可以WhatsApp{anna_phone}联系{anna}"
    for _, answers, _ in feedback_links(email.html):
        for value in [*CHINESE_DETAILS, "Anna", *KIDS]:
            assert value not in json.dumps(answers, ensure_ascii=False)


def test_with_the_ai_filter_off_feedback_links_still_carry_no_ones_name(harness):
    # The links go to the team, not the AI, so they are masked all the same.
    names_night(harness)
    harness.config["ai_filter"] = {"enabled": False}
    harness.config["email"]["to"] = ["parent@example.com"]
    harness.config["feedback"] = FEEDBACK
    harness.model_reply = {
        "per_kid": [{"kid": "Mia", "notices": [], "action_items": [
            {"what": "Palauta Mian retken lupalappu Maijalle", "by": "2026-09-30", "refs": ["message:701"]}]}],
        "calendar_events": [],
        "message_digest": "**Mia**\n- Lupalappu Maijalle tai Virtaselle\n- Eetun synttärit lauantaina",
    }

    assert harness.run() == 0

    [email] = harness.sent
    assert "Palauta Mian retken lupalappu Maijalle" in email.text and "Maija" in harness.model_prompt(0)
    digest, item = (answers for _, answers, _ in feedback_links(email.html) if answers["verdict"] != feedback.SAVED)
    assert re.fullmatch(r"Palauta Kid A:n retken lupalappu (⟦N\d+⟧):lle", item["item_text"]) and item["kid"] == "Kid A"
    assert re.fullmatch(r"\*\*Kid A\*\*\n- Lupalappu (⟦N\d+⟧):lle tai \1:lle\n- ⟦N\d+⟧:n synttärit lauantaina",
                        digest["item_text"])


def households_in(email) -> set[str]:
    return {answers["household"] for _, answers, _ in feedback_links(email.html)}


def test_the_form_gets_a_households_pseudonym_the_same_every_evening_and_never_its_label(harness):
    normal_night(harness)
    harness.config["feedback"] = FEEDBACK  # the Household label is 王家
    assert harness.run() == 0
    # The next evening has no new messages, but last night's to-dos are due soon, so the model writes again.
    with time_machine.travel(NOW + timedelta(days=1), tick=False):
        assert harness.run() == 0

    first, second = harness.sent
    [pseudonym] = households_in(first)
    assert households_in(second) == {pseudonym}
    assert re.fullmatch(r"Household [0-9a-f]{6}", pseudonym)
    for email in (first, second):
        assert "王家" not in unquote(email.html)


def test_another_household_gets_another_pseudonym(harness):
    normal_night(harness)
    harness.config["feedback"] = FEEDBACK
    assert harness.run() == 0
    harness.config["feedback"] = {**FEEDBACK, "household_label": "Virtanen family"}
    with time_machine.travel(NOW + timedelta(days=1), tick=False):
        assert harness.run() == 0

    first, second = harness.sent
    [one], [other] = households_in(first), households_in(second)
    assert one != other and re.fullmatch(r"Household [0-9a-f]{6}", other)
    assert "Virtanen" not in unquote(second.html)
