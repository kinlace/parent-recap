"""The AI filter on its own, as another tool could call it: a list of messages in, the masked
messages and their placeholders out, and a separate step that puts the real values back in a reply.
The evening run's use of it is in test_nightly_run.py."""
from __future__ import annotations

import copy
import json

from family_brief import ai_filter

MESSAGES = [
    {"external_id": "g-1", "sender": "Maija Opettaja <maija.opettaja@kilo.example.fi>",
     "body": "Sign up at https://forms.kilo.example.fi/retki?id=42 or call 040 123 4567. "
             "Questions to maija.opettaja@kilo.example.fi.",
     "url": "https://mail.google.com/mail/u/0/#search/rfc822msgid:abc@kilo.example.fi"},
    {"external_id": "wa-1", "sender": "Anna", "body": "Text me on +358 50 765 4321, or see www.kilo-fc.fi.",
     "metadata": {"student_number": "7731905"}},
]
WORDS = {"phone": "a phone number", "email": "an email address", "link": "a link"}


def test_a_list_of_messages_gets_placeholders_that_a_reply_turns_back_into_the_values():
    before = copy.deepcopy(MESSAGES)

    masked, placeholders = ai_filter.mask(MESSAGES, drop=("url", "student_number"))

    sent = json.dumps(masked, ensure_ascii=False)
    for value in ("maija.opettaja@kilo.example.fi", "forms.kilo.example.fi", "040 123 4567",
                  "+358 50 765 4321", "kilo-fc.fi", "mail.google.com", "7731905"):
        assert value not in sent
    assert masked == [
        {"external_id": "g-1", "sender": "Maija Opettaja <⟦E1⟧>",
         "body": "Sign up at ⟦L1⟧ or call ⟦P1⟧. Questions to ⟦E1⟧."},
        {"external_id": "wa-1", "sender": "Anna", "body": "Text me on ⟦P2⟧, or see ⟦L2⟧.", "metadata": {}},
    ]
    assert MESSAGES == before

    reply = {"what": "Call ⟦P1⟧ or sign up at ⟦L1⟧", "refs": ["g-1"]}
    assert ai_filter.restore(reply, placeholders, WORDS) == {
        "what": "Call 040 123 4567 or sign up at https://forms.kilo.example.fi/retki?id=42", "refs": ["g-1"]}


# ── People's names (#191)

PERSON_WORDS = {**WORDS, "person": "someone"}


def people(*names: str, keep: tuple[str, ...] = ()) -> ai_filter.Placeholders:
    """Placeholders that know tonight's people, as the fields that name them give them."""
    placeholders = ai_filter.Placeholders()
    placeholders.add_people(names, keep_names=keep)
    return placeholders


def masked_text(placeholders: ai_filter.Placeholders, text: str) -> str:
    return ai_filter.mask({"body": text}, placeholders)[0]["body"]


def test_a_full_name_its_parts_and_their_finnish_case_forms_are_one_person():
    placeholders = people("Maija Virtanen")

    assert masked_text(placeholders, "Maija Virtanen kirjoitti. Kerro Maijalle, että Maijan lupalappu "
                                     "puuttuu. Virtasen tunnilla Maijaa ei näkynyt.") == \
        "⟦N1⟧ kirjoitti. Kerro ⟦N1⟧:lle, että ⟦N1⟧:n lupalappu puuttuu. ⟦N1⟧:n tunnilla ⟦N1⟧:a ei näkynyt."


def test_a_name_whose_finnish_stem_changes_is_found_in_its_case_forms():
    placeholders = people("Pekka Niemi", "Daniel Laine", "Markus Lahti", "Satu Mäki")

    assert masked_text(placeholders, "Pekan ja Niemen, Danielille ja Laineelle, Markuksen ja Lahden, "
                                     "Sadun ja Mäen. Niemestä ja Pekkaa.") == \
        "⟦N1⟧:n ja ⟦N1⟧:n, ⟦N2⟧:lle ja ⟦N2⟧:lle, ⟦N3⟧:n ja ⟦N3⟧:n, ⟦N4⟧:n ja ⟦N4⟧:n. ⟦N1⟧:stä ja ⟦N1⟧:a."


def test_a_reply_that_copies_the_placeholders_gets_back_exactly_the_text_they_stood_for():
    # Ville Virtanen, Maria and the spring party Kevät juhla are no one on tonight's list, but a
    # listed name's part or case form matches them.
    placeholders = people("Maija Virtanen", "Mari Korhonen", "Kevät Lehto", "Pekka Niemi", "Markus Lahti",
                          "Satu Mäki", "Daniel Laine", "Anna Ranta")
    text = ("Maija Virtanen kirjoitti, että Ville Virtanen ja Virtasta. Maria ja Marille. Kevät juhla on "
            "Lehdon talossa. Niemen ja Lahden, Laineen ja Mäen, Rannan ja Sadun, Markuksen ja Pekan.")

    masked = masked_text(placeholders, text)

    for name in ("Virta", "Mari", "Kevät", "Lehdo", "Nieme", "Lahde", "Laine", "Mäen", "Ranna", "Sadun",
                 "Markuk", "Pekan"):
        assert name not in masked
    assert ai_filter.restore({"text": masked}, placeholders, PERSON_WORDS) == {"text": text}


def test_a_form_the_text_never_had_is_made_from_the_name_as_the_text_wrote_it():
    placeholders = people("Maija Virtanen", "Pekka Korpela", "Daniel")
    masked_text(placeholders, "Maija tulee. Virtasen kanssa. Pekalle kiitos. Danielin reppu.")
    reply = {"text": "⟦N1⟧:lle ja ⟦N1⟧:kin. ⟦N2⟧ tulee, ⟦N2⟧:n reppu. ⟦N3⟧ ja ⟦N3⟧:lle.",
             "zh": "请回复⟦N2⟧"}

    # From the name as the text wrote it without an ending (Maija), or else the shortest name
    # listed (Pekka, Daniel), never a full name the text didn't have. An ending joins a vowel, a
    # kk, pp or tt before the last vowel takes its weak stem, and the colon stays after any other
    # consonant, where the right form would need the name's own stem.
    assert ai_filter.restore(reply, placeholders, PERSON_WORDS) == {
        "text": "Maijalle ja Maijakin. Pekka tulee, Pekan reppu. Daniel ja Daniel:lle.",
        "zh": "请回复Pekka"}
    # Only a Finnish case ending joins the name: other text after a colon stays as written.
    assert ai_filter.restore({"text": "Ask ⟦N1⟧:she knows. ⟦N1⟧: 040"}, placeholders, PERSON_WORDS) == \
        {"text": "Ask Maija:she knows. Maija: 040"}


def test_names_inside_chinese_text_with_no_spaces_are_masked_and_come_back():
    # A Latin name, a WhatsApp name with the pupil's name before 妈妈, and a surname before 老师.
    placeholders = people("Maija Virtanen", "李明妈妈", "王老师")
    text = "请联系Maija老师，李明今天没来。王老师说周五考试，Virtanen老师也同意。王子的故事不考。"

    masked = masked_text(placeholders, text)

    assert masked == "请联系⟦N1⟧老师，⟦N2⟧今天没来。⟦N3⟧老师说周五考试，⟦N1⟧老师也同意。王子的故事不考。"
    assert masked_text(placeholders, "李明妈妈：明天带雨衣") == "⟦N2⟧妈妈：明天带雨衣"
    reply = {"text": "⟦N3⟧老师说⟦N2⟧周五考试，问⟦N1⟧"}
    assert ai_filter.restore(reply, placeholders, PERSON_WORDS) == {"text": "王老师说李明周五考试，问Maija"}
    assert ai_filter.restore({"text": masked}, placeholders, PERSON_WORDS) == {"text": text}


def test_a_three_character_chinese_name_is_also_masked_by_its_given_name():
    placeholders = people("王小明妈妈", "李伟")
    text = "小明今天没来，王小明的作业在李伟那里。伟大的老师和小李都在。"

    masked = masked_text(placeholders, text)

    assert masked == "⟦N1⟧今天没来，⟦N1⟧的作业在⟦N2⟧那里。伟大的老师和小李都在。"
    assert ai_filter.restore({"text": masked}, placeholders, PERSON_WORDS) == {"text": text}


def test_only_the_nights_people_are_masked_so_a_word_that_is_a_name_elsewhere_stays():
    text = "Onni ja Toivo tulevat. Toivon, että sää on hyvä, ja onnea matkaan! toivo on suuri."

    assert masked_text(people("Maija Virtanen"), text) == text
    # Toivo is a parent in tonight's WhatsApp group: the name goes, in its case forms too (a
    # capital Toivon starting a sentence may be the verb, and goes as well), and the lower-case
    # word for hope stays.
    placeholders = people("Toivo Mäkelä")
    masked = masked_text(placeholders, text)
    assert masked == "Onni ja ⟦N1⟧ tulevat. ⟦N1⟧:n, että sää on hyvä, ja onnea matkaan! toivo on suuri."
    # Copied back, each one is the word the text had.
    assert ai_filter.restore({"text": masked}, placeholders, PERSON_WORDS) == {"text": text}


def test_the_households_own_names_stay_and_a_part_two_people_share_is_a_person_of_its_own():
    # Mia is the Household's Kid, and a teacher and a parent are both called Maija.
    placeholders = people("Mia Virtanen", "Mia", "Maija Korhonen", "Maija Laine", "Kilo School",
                          keep=("Mia", "米娅", "Kilo School", "3B"))
    text = "Mia Virtanen kertoi, että Mia ja Mian luokka 3B (Kilo School) lähtevät. Maija Korhonen ja " \
           "Maija Laine tulevat, Maijalle kiitos. 米娅很开心。Virtasen tunti."

    assert masked_text(placeholders, text) == \
        "⟦N1⟧ kertoi, että Mia ja Mian luokka 3B (Kilo School) lähtevät. ⟦N2⟧ ja ⟦N3⟧ tulevat, " \
        "⟦N4⟧:lle kiitos. 米娅很开心。⟦N1⟧:n tunti."
    reply = {"text": "⟦N4⟧:lle ja ⟦N1⟧:lle kiitos"}
    assert ai_filter.restore(reply, placeholders, PERSON_WORDS) == {"text": "Maijalle ja Mia Virtaselle kiitos"}


def test_a_kids_name_in_a_finnish_case_form_stays_when_a_third_partys_name_has_that_form():
    # The Kid is Leo, and a pupil in the class is Leon Mäkinen.
    placeholders = people("Leon Mäkinen", "Pekka", keep=("Leo", "Pekka Virtanen", "Pekka"))
    text = "Leon synttärit ovat lauantaina, Leolle kiitos. Leon Mäkinen ja Mäkisen äiti tulevat. Pekan reppu."

    assert masked_text(placeholders, text) == \
        "Leon synttärit ovat lauantaina, Leolle kiitos. ⟦N1⟧ ja ⟦N1⟧:n äiti tulevat. Pekan reppu."


def test_a_role_or_label_stays_beside_the_placeholder_and_an_organisation_is_no_person():
    senders = ["Opettaja Virtanen", "Terveydenhoitaja", "Anna (piano)", "Eetun äiti", "Parent rep",
               "Espoon kaupunki", "Kilonkoulu", "中文学校", "Info <info@kilo.example.fi>"]
    placeholders = people(*senders)

    assert [masked_text(placeholders, s) for s in senders] == [
        "Opettaja ⟦N1⟧", "Terveydenhoitaja", "⟦N2⟧ (piano)", "⟦N3⟧:n äiti", "Parent rep",
        "Espoon kaupunki", "Kilonkoulu", "中文学校", "Info <⟦E1⟧>"]
    assert masked_text(placeholders, "Eetu ja Virtanen") == "⟦N3⟧ ja ⟦N1⟧"


def test_a_sender_with_an_organisation_keeps_its_person_and_an_organisation_alone_is_no_one():
    senders = ["Maija Virtanen, Kilon koulu", "Juha Lahtinen Espoon kaupunki", "中文学校王老师", "Korpela, Anna",
               "Espoo Music Institute <info@emi.example.fi>", "Kide Science <hello@kide.example.com>", "Kilo FC",
               "Kilon koulu tiedottaa"]
    placeholders = people(*senders)

    assert [masked_text(placeholders, s) for s in senders] == [
        "⟦N1⟧, Kilon koulu", "⟦N2⟧ Espoon kaupunki", "中文学校⟦N3⟧老师", "⟦N4⟧, ⟦N4⟧",
        "Espoo Music Institute <⟦E1⟧>", "Kide Science <⟦E2⟧>", "Kilo FC", "Kilon koulu tiedottaa"]
    text = "Espoossa on Music lesson ja Science fair. Kilo pelaa. Anna Korpela ja Juhalle."
    assert masked_text(placeholders, text) == \
        "Espoossa on Music lesson ja Science fair. Kilo pelaa. ⟦N4⟧ ja ⟦N2⟧:lle."


def test_a_person_placeholder_written_another_way_gets_the_name_and_one_beyond_repair_a_word():
    placeholders = people("Maija Virtanen", "Anna Korpela")
    masked_text(placeholders, "Huone N1 on auki")  # the night's own text has an N1
    reply = {"text": "Vastaa [N2]:lle, ⟦ n 1 ⟧:lle ja ⟦Ν2⟧:n äidille, ei ⟦N9⟧:lle. Huone (N1) on auki.",
             "zh": "请回复【Ｎ２】和⟦N7⟧"}

    assert ai_filter.restore(reply, placeholders, {**PERSON_WORDS, "person": "henkilö"}) == {
        "text": "Vastaa Annalle, Maijalle ja Annan äidille, ei henkilölle. Huone (N1) on auki.",
        "zh": "请回复Anna和henkilö"}
    assert placeholders.lost == 2  # ⟦N9⟧ and ⟦N7⟧, which the eval counts against the model
