"""The nights and helpers the nightly-run tests (`test_nightly_*.py`) share: the normal night and
the model's reply to it in each language, the night in two languages, the payload's message ids, the
archived summary and the feedback links."""
from __future__ import annotations

import copy
import json
import re
from html import unescape
from urllib.parse import parse_qs, urlsplit

from conftest import msg, myclub_event


def normal_night(h, language: str = "zh") -> None:
    h.config["summary_language"] = language
    h.sources = {
        "gmail": [
            msg("gmail", "g-101", "2026-09-27T08:15:00+03:00",
                "Hei! 3B goes on a retki to Nuuksio on Thursday 1.10. Please sign the permission "
                "slip in Wilma by Tuesday. Eväät and vaatetus for rain.",
                sender="teacher.3b@kilo.example.fi", subject="Retki Nuuksioon to 1.10.", kid="Mia"),
            msg("gmail", "g-102", "2026-09-27T10:40:00+03:00",
                "Class photos for 1A are on Wednesday morning.",
                sender="office@kilo.example.fi", subject="Valokuvaus ke 30.9."),
        ],
        "myclub": [myclub_event("mc-1", "FC Kilo P2017 vs HJK", "2026-10-03T07:00:00+00:00",
                                "2026-10-03T08:30:00+00:00")],
        "wilma": [
            msg("wilma", "w-55", "2026-09-27T12:00:00+03:00",
                "Matematiikan koe maanantaina 5.10. Kertotaulut 2-5.",
                sender="Opettaja Virtanen", subject="Koe 5.10.", kid="Mia"),
        ],
        "whatsapp": [
            msg("whatsapp", "wa-1", "2026-09-27T18:02:00+03:00",
                "Piano lesson moves to Wednesday 17:30 this week", sender="Anna (piano)",
                chat="Leo piano", kid="Leo"),
            msg("whatsapp", "wa-2", "2026-09-27T19:45:00+03:00",
                "Remember reissuvihko signatures 🙏", sender="Parent rep", chat="3B parents", kid="Mia"),
        ],
    }
    h.model_reply = {
        "per_kid": [
            {"kid": "Mia",
             "notices": [{"text": "周四 10/1 班级去 Nuuksio 远足（retki），带 eväät 和雨衣", "refs": ["g-101"]},
                         {"text": "周一 10/5 数学考试，范围乘法表 2–5", "refs": ["w-55"]}],
             "action_items": [
                 {"what": "在 Wilma 签远足同意书", "by": "2026-09-29", "who": "任一", "refs": ["g-101"]},
                 {"what": "签 reissuvihko", "by": "2026-09-28", "who": "妈妈", "refs": ["wa-2"]}]},
            {"kid": "Leo",
             "notices": [{"text": "周三 9/30 上午拍班级照", "refs": ["g-102"]}],
             "action_items": [{"what": "给 Leo 准备拍照穿的衣服 <整洁>", "by": "2026-09-30", "who": "爸爸",
                               "refs": ["g-102"]}]},
        ],
        "calendar_events": [
            {"kid": "Mia", "title": "3B 远足 Nuuksio", "start": "2026-10-01T09:00:00",
             "end": "2026-10-01T14:00:00", "location": "Nuuksio",
             "description": "班级远足，带午餐和雨衣（gmail）", "refs": ["g-101"]},
            {"kid": "Leo", "title": "钢琴课（改期）", "start": "2026-09-30T17:30:00+03:00",
             "location": "Music school", "description": "本周改到周三", "refs": ["wa-1"]},
        ],
        "message_digest": "**Mia**\n- 周四远足，周二前在 Wilma 签同意书\n- 下周一数学考试\n\n"
                          "**Leo**\n- 周三拍班级照\n- 钢琴课本周改到周三 17:30",
    }
    if language != "zh":
        h.model_reply = copy.deepcopy({"en": ENGLISH_REPLY, "fi": FINNISH_REPLY}[language])  # tests edit it


# The same night as the model writes it for an English Brief.
ENGLISH_REPLY = {
    "per_kid": [
        {"kid": "Mia",
         "notices": [{"text": "Thu 10/1 class retki to Nuuksio; pack eväät and rain gear", "refs": ["g-101"]},
                     {"text": "Mon 10/5 maths test on times tables 2–5", "refs": ["w-55"]}],
         "action_items": [
             {"what": "Sign the retki permission slip in Wilma", "by": "2026-09-29", "who": "Either",
              "refs": ["g-101"]},
             {"what": "Sign the reissuvihko", "by": "2026-09-28", "who": "Mom", "refs": ["wa-2"]}]},
        {"kid": "Leo",
         "notices": [{"text": "Wed 9/30 class photos in the morning", "refs": ["g-102"]}],
         "action_items": [{"what": "Get Leo's photo-day clothes ready <neat>", "by": "2026-09-30", "who": "Dad",
                           "refs": ["g-102"]}]},
    ],
    "calendar_events": [
        {"kid": "Mia", "title": "3B retki to Nuuksio", "start": "2026-10-01T09:00:00",
         "end": "2026-10-01T14:00:00", "location": "Nuuksio",
         "description": "Class retki; pack lunch and rain gear (gmail)", "refs": ["g-101"]},
        {"kid": "Leo", "title": "Piano lesson (moved)", "start": "2026-09-30T17:30:00+03:00",
         "location": "Music school", "description": "Moved to Wednesday this week", "refs": ["wa-1"]},
    ],
    "message_digest": "**Mia**\n- Thursday retki; sign the slip in Wilma by Tuesday\n- Maths test next Monday\n\n"
                      "**Leo**\n- Class photos Wednesday\n- Piano moves to Wednesday 17:30 this week",
}


# The same night as the model writes it for a Finnish Brief.
FINNISH_REPLY = {
    "per_kid": [
        {"kid": "Mia",
         "notices": [{"text": "to 1.10. luokan retki Nuuksioon; mukaan eväät ja sadevarusteet", "refs": ["g-101"]},
                     {"text": "ma 5.10. matematiikan koe, kertotaulut 2–5", "refs": ["w-55"]}],
         "action_items": [
             {"what": "Allekirjoita retken lupalappu Wilmassa", "by": "2026-09-29", "who": "Kumpi tahansa",
              "refs": ["g-101"]},
             {"what": "Allekirjoita reissuvihko", "by": "2026-09-28", "who": "Äiti", "refs": ["wa-2"]}]},
        {"kid": "Leo",
         "notices": [{"text": "ke 30.9. aamupäivällä luokkakuvaus", "refs": ["g-102"]}],
         "action_items": [{"what": "Varaa Leolle kuvauspäiväksi siistit vaatteet <siistit>", "by": "2026-09-30",
                           "who": "Isä", "refs": ["g-102"]}]},
    ],
    "calendar_events": [
        {"kid": "Mia", "title": "3B:n retki Nuuksioon", "start": "2026-10-01T09:00:00",
         "end": "2026-10-01T14:00:00", "location": "Nuuksio",
         "description": "Luokan retki; mukaan eväät ja sadevarusteet (gmail)", "refs": ["g-101"]},
        {"kid": "Leo", "title": "Pianotunti (siirretty)", "start": "2026-09-30T17:30:00+03:00",
         "location": "Musiikkiopisto", "description": "Siirtyy tällä viikolla keskiviikkoon", "refs": ["wa-1"]},
    ],
    "message_digest": "**Mia**\n- Torstaina retki; lupalappu Wilmassa tiistaihin mennessä\n"
                      "- Ensi maanantaina matematiikan koe\n\n"
                      "**Leo**\n- Keskiviikkona luokkakuvaus\n- Piano siirtyy tällä viikolla keskiviikkoon klo 17.30",
}


def model_call_for_golden(argv: list[str], stdin: str | None) -> str:
    shown = ["<system prompt>" if i and argv[i - 1] == "--system-prompt" else a for i, a in enumerate(argv)]
    out = "argv: " + json.dumps(shown, ensure_ascii=False) + "\n"
    if "--system-prompt" in argv:
        out += "\n--- system prompt ---\n" + argv[argv.index("--system-prompt") + 1] + "\n"
    return out + "\n--- stdin ---\n" + str(stdin) + "\n"


def payload_message_ids(h, i: int = -1) -> list[str]:
    return sorted(m["external_id"] for m in h.model_payload(i)["messages"])


NORMAL_NIGHT_IDS = ["g-101", "g-102", "w-55", "wa-1", "wa-2"]


def archived_summary(h, day: str = "2026-09-27") -> dict:
    return json.loads((h.archive_dir / f"{day}.raw.json").read_text())["summary"]


FORM = "https://docs.google.com/forms/d/e/FORM_ID/viewform"
FEEDBACK = {"enabled": True, "prefill_base_url": FORM, "household_label": "王家",
            "fields": {"verdict": "entry.1", "item_text": "entry.2", "source": "entry.3",
                       "backend": "entry.4", "date": "entry.5", "household": "entry.6", "kid": "entry.7"}}
FIELDS = {v: k for k, v in FEEDBACK["fields"].items()}


def feedback_links(html: str) -> list[tuple[str, dict[str, str], str]]:
    """(link label, pre-filled answers by field name, full URL) for every Form link in the Brief."""
    out = []
    for href, label in re.findall(r'<a href="([^"]+)"[^>]*>([^<]+)</a>', html):
        url = unescape(href)
        if not url.startswith(FORM + "?"):
            continue
        query = parse_qs(urlsplit(url).query, keep_blank_values=True)
        assert query.pop("usp") == ["pp_url"]
        out.append((label, {FIELDS[k]: v for k, [v] in query.items()}, url))
    return out


PARTNER_EN = {"address": "partner@example.com", "language": "en"}


def two_languages(h) -> None:
    """The normal night for a Household where the first Recipient reads Chinese and the partner English."""
    normal_night(h)
    h.config["email"]["to"] = ["parent@example.com", PARTNER_EN]
    # The model writes the Brief in Chinese, then translates it: the same entries in English.
    h.model_reply = [h.model_reply, copy.deepcopy(ENGLISH_REPLY)]


def placeholders_in(text: str) -> list[str]:
    return re.findall(r"⟦[PEL]\d+⟧", text)


def chinese_reply(prompt: str) -> dict:
    """The Chinese night as a model writes it in Chinese: placeholders with no space around them."""
    body = {m["external_id"]: m["body"] for m in json.loads(prompt[prompt.index("\n{") + 1:])["messages"]}
    phone, _office, timetable = placeholders_in(body["g-601"])
    [mobile] = placeholders_in(body["wa-501"])
    form, anna = placeholders_in(body["wa-502"])
    return {
        "per_kid": [
            {"kid": "Mia", "notices": [{"text": f"足球在{form}报名，也可以WhatsApp{anna}联系Anna", "refs": ["wa-502"]}],
             "action_items": []},
            {"kid": "Leo", "notices": [{"text": f"课表见{timetable}", "refs": ["g-601"]}],
             "action_items": [{"what": f"打{mobile}给中文课报名，或致电{phone}", "by": "2026-10-02", "who": "任一",
                               "refs": ["wa-501", "g-601"]}]},
        ],
        "calendar_events": [{"kid": "Mia", "title": "足球报名截止", "start": "2026-10-02",
                             "description": f"报名链接：{form}，周五截止", "refs": ["wa-502"]}],
        "message_digest": f"**Leo**\n- 中文课报名打{mobile}\n\n**Mia**\n- 足球在{form}报名，也可以WhatsApp{anna}联系Anna",
    }
