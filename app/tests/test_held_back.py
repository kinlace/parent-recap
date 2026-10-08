"""Which messages look sensitive, checked on their own, as another tool could: a text in, its
category or None out, and a night's messages split into those for the AI and those held back.
The evening run's use of it (nothing held back reaches a prompt, and the Brief lists it) is in
test_nightly_run.py. The words are in src/family_brief/sensitive_words.yaml."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
import yaml

from family_brief import held_back
from family_brief.collectors.base import Message

# A message about one person's sensitive situation, written as parents and schools write it:
# Finnish with its case endings and compounds, Chinese with no spaces between words.
HELD_BACK = [
    ("Miaa kiusattiin taas välitunnilla.", "bullying"),
    ("Leoa on kiusattu jo pitkään.", "bullying"),
    ("Isompi poika kiusasi Leoa bussissa.", "bullying"),
    ("Yksi oppilas kiusaa Miaa joka päivä.", "bullying"),
    ("Selvitämme Mian kokemaa kiusaamista.", "bullying"),
    ("Koulukiusaaminen on vakava asia, ja puhumme Leon tilanteesta.", "bullying"),
    ("Mia on kokenut häirintää bussissa.", "bullying"),
    ("Välitunnilla Leoon kohdistui väkivaltaa.", "bullying"),
    ("Leosta on tehty lastensuojeluilmoitus.", "welfare"),
    ("Poliisi kävi koululla Mian asiassa.", "welfare"),
    ("Mialla todettiin ADHD.", "health"),
    ("Leon diagnoosi tuli eilen.", "health"),
    ("Leon lääkitystä muutetaan ensi viikolla.", "health"),
    ("Koulupsykologi haluaa tavata teidät Mian asioissa.", "health"),
    ("Kuraattorin tapaaminen on torstaina klo 10.", "health"),
    ("Mialle aloitetaan tehostettu tuki.", "support"),
    ("Leo siirtyy erityisen tuen piiriin.", "support"),
    ("Mia tarvitsee tehostettua tukea lukemiseen.", "support"),
    ("Leon HOJKS päivitetään.", "support"),
    ("Mian oppimissuunnitelma on valmis allekirjoitettavaksi.", "support"),
    ("Pedagogisen selvityksen palaveri on tiistaina.", "support"),
    ("Mia har blivit mobbad på rasterna.", "bullying"),
    ("Leo får särskilt stöd från nästa vecka.", "support"),
    ("Skolkuratorn vill träffa er.", "health"),
    ("Vi har gjort en orosanmälan.", "welfare"),
    ("Leo was bullied at recess again.", "bullying"),
    ("The school counsellor would like to meet you about Mia.", "health"),
    ("Mia's individual learning plan is ready to sign.", "support"),
    ("米娅在学校被欺负了", "bullying"),
    ("老师说小狮最近被霸凌", "bullying"),
    ("学校心理老师想和你们谈谈米娅的情况", "health"),
    ("米娅确诊多动症需要服药", "health"),
    ("Leo的ADHD诊断出来了", "health"),
    ("建议小狮接受特殊教育支持", "support"),
    ("社工下周来家访", "welfare"),
]

# Routine notices to a whole class, absence notes and everyday words that only look alike.
NOT_HELD_BACK = [
    "Luokalla on todettu täitä. Tarkistakaa lasten hiukset tänä iltana.",
    "Head lice have been found in class 3B. Please check your child's hair tonight.",
    "班上发现了头虱，请家长今晚检查孩子的头发",
    "Luokalla on vesirokkoa. Seuratkaa oireita.",
    "There is chickenpox in class 1A.",
    "班上有同学出水痘，请家长留意",
    "Mia on tänään kipeänä kotona.",
    "Leo is ill today and stays home.",
    "米娅今天发烧，请假一天",
    "Terveydenhoitaja: 1. luokan terveystarkastukset ovat ensi viikolla. Aikataulu liitteenä.",
    "The school nurse's check-ups for grade 1 are next week.",
    "Leo loukkasi nilkkansa treeneissä ja käy fysioterapiassa, joten hän pitää taukoa.",
    "Leo twisted his ankle at training and is seeing a physiotherapist this week.",
    "小狮训练时扭伤了脚，这周休息",
    "Koululla vierailee ensi viikolla terapiakoira.",
    "Tämä on vähän kiusallista, mutta retki siirtyy.",
    "Kiitos erityisesti tuesta talkoissa!",
    "Pukekaa lapsille erityisen tukevat kengät.",
    "The syllabus for the maths test is on page 12.",
    "Our flautist plays at the spring concert.",
    "A Polish family joins class 3B.",
    "It's a depressing rainy day, so bring a raincoat.",
    "学校明天将测试火灾报警器",
    "Vi vill särskilt stödja läsningen i klassen.",
]


@pytest.mark.parametrize("text, category", HELD_BACK)
def test_a_message_about_one_persons_sensitive_situation_is_held_back(text, category):
    assert held_back.sensitive(text) == category


@pytest.mark.parametrize("text", NOT_HELD_BACK)
def test_a_routine_notice_or_an_everyday_word_is_not_held_back(text):
    assert held_back.sensitive(text) is None


def message(ext_id: str, body: str, subject: str | None = None, sender: str | None = None) -> Message:
    return Message(source="gmail", external_id=ext_id, timestamp=datetime(2026, 9, 27, tzinfo=timezone.utc),
                   sender=sender, subject=subject, body=body)


def test_a_nights_messages_are_split_into_those_for_the_ai_and_those_held_back():
    night = [message("1", "Retki torstaina."),
             message("2", "Soitattehan minulle.", subject="Leon kiusaaminen"),
             message("3", "Class photos on Wednesday."),
             message("4", "Voisimmeko tavata?", sender="Koulupsykologi Laine <psykologi@kilo.example.fi>"),
             message("5", "米娅被欺负了")]

    for_the_ai, held = held_back.hold_back(night)

    assert [m.external_id for m in for_the_ai] == ["1", "3"]
    assert [m.external_id for m in held] == ["2", "4", "5"]


def test_the_word_lists_are_in_one_file_with_every_category_in_every_language():
    text = held_back.WORDS.read_text(encoding="utf-8")
    assert "the Finnish list waits for a native speaker's review" in text
    lists = yaml.safe_load(text)
    assert set(lists) == {"fi", "sv", "en", "zh"}
    for language, categories in lists.items():
        assert set(categories) == {*held_back.CATEGORIES, "not"}, language
        assert all(categories[c] for c in held_back.CATEGORIES), language
