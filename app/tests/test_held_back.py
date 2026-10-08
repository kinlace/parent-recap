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
    ("Leolla diagnosoitiin lukihäiriö.", "health"),
    ("Koulupsykologi haluaa tavata teidät Mian asioissa.", "staff"),
    ("Tapasimme psykologia Leon asioissa.", "staff"),
    ("Kuraattorin tapaaminen on torstaina klo 10.", "staff"),
    ("Mian tukipalaveri on keskiviikkona.", "support"),
    ("Mialle aloitetaan tehostettu tuki.", "support"),
    ("Leo siirtyy erityisen tuen piiriin.", "support"),
    ("Mia tarvitsee tehostettua tukea lukemiseen.", "support"),
    ("Leon HOJKS päivitetään.", "support"),
    ("Mian oppimissuunnitelma on valmis allekirjoitettavaksi.", "support"),
    ("Pedagogisen selvityksen palaveri on tiistaina.", "support"),
    ("Mia har blivit mobbad på rasterna.", "bullying"),
    ("Leo får särskilt stöd från nästa vecka.", "support"),
    ("Skolkuratorn vill träffa er.", "staff"),
    ("Mia har fått diagnos i somras.", "health"),
    ("Leo har diagnostiserats med dyslexi.", "health"),
    ("Vi har gjort en orosanmälan.", "welfare"),
    ("Leo was bullied at recess again.", "bullying"),
    ("The doctor diagnosed Mia last week.", "health"),
    ("The school counsellor would like to meet you about Mia.", "staff"),
    ("The school psychologist would like to meet you, so please call her.", "staff"),
    ("Mia's individual learning plan is ready to sign.", "support"),
    ("米娅在学校被欺负了", "bullying"),
    ("老师说小狮最近被霸凌", "bullying"),
    ("学校心理老师想和你们谈谈米娅的情况", "staff"),
    ("米娅确诊多动症", "health"),
    ("小狮被诊断为阅读障碍", "health"),
    ("Leo的ADHD诊断出来了", "health"),
    ("建议小狮接受特殊教育支持", "support"),
    ("社工下周来家访", "staff"),
    # A word about what happened counts even on a line with a phone number.
    ("Mia kertoi, että häntä kiusataan. Soitathan 040 123 4567?", "bullying"),
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
    # A camp or trip notice that asks every family about medication.
    "Leirikoulu: ilmoitattehan opettajalle lapsen lääkityksestä ja allergioista.",
    "Lägerskola: meddela läraren om ditt barn behöver medicinering.",
    "Camp: please tell us if your child needs medication during the trip.",
    "露营时请为孩子准备常用药",
    # A test or a school subject.
    "Ensi viikolla on matematiikan diagnostinen koe.",
    "Diagnostiset kokeet pidetään syyskuussa.",
    "Diagnostiska prov i matematik nästa vecka.",
    "Maths diagnostic test on Monday.",
    "下周有数学诊断测试",
    "Psykologian koe on perjantaina.",
    "Huomenna psykologian tunti on luokassa B12.",
    "Psychology class moves to room B12.",
    "The psychology exam is on Friday, and the psychology lesson after it is cancelled.",
    # Course choices with the guidance counsellor.
    "Opinto-ohjaaja kertoo kurssivalinnoista torstaina.",
    "Studievägledaren berättar om kursval på torsdag.",
    "The guidance counsellor talks about course choices on Thursday.",
    "Our guidance counselor visits grade 9 on Monday.",
]

# A weekly newsletter whose footer lists the staff with their contact details.
NEWSLETTERS = [
    "Viikkotiedote 40\nTällä viikolla retki Nuuksioon.\n\nYhteystiedot:\n"
    "Rehtori Matti Meikäläinen, 040 111 2222\n"
    "Kuraattori Maija Laine, 040 123 4567, maija.laine@kilo.example.fi\n"
    "Koulupsykologi Pekka Virtanen, pekka.virtanen@kilo.example.fi\n"
    "Terveydenhoitaja Anna Koski, p. 09 816 2000",
    "Veckobrev 40\nUtflykt på torsdag.\n\nKurator Eva Berg, 040 765 4321\nSkolpsykolog Ola Ek, ola.ek@kilo.example.fi",
    "Weekly news\nThe retki is on Thursday.\n\nSchool counsellor: Anna Smith, anna.smith@kilo.example.fi\n"
    "School psychologist: Tom Brown, +358 40 222 3333\nSocial worker: www.kilo.example.fi/staff",
    "本周通知\n周四远足。\n\n心理老师：王老师 13800138000\n社工：李老师 li@kilo.example.fi",
]


@pytest.mark.parametrize("text, category", HELD_BACK)
def test_a_message_about_one_persons_sensitive_situation_is_held_back(text, category):
    assert held_back.sensitive(text) == category


@pytest.mark.parametrize("text", NOT_HELD_BACK)
def test_a_routine_notice_or_an_everyday_word_is_not_held_back(text):
    assert held_back.sensitive(text) is None


@pytest.mark.parametrize("text", NEWSLETTERS)
def test_staff_listed_with_their_contact_details_are_not_a_sensitive_message(text):
    assert held_back.sensitive(text) is None
    # The same newsletter about one pupil's situation is still held back.
    assert held_back.sensitive(text + "\n\nLeoa on kiusattu välitunneilla.") == "bullying"


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
