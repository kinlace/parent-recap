"""`parent-recap ai-filter-report` reads the Household's own archive and counts what the AI filter
catches, as the evening run would filter each night, with counts only (#197, ADR 0013). With
--held-back it lists the Held-back Messages too, only in the parent's own terminal (#220)."""
from __future__ import annotations

import re
import socket
import sys

import pytest

from conftest import Harness, msg

# Three evenings in the archive, as the evening run writes them: every message it read, the held
# back ones too. Each Brief has a Notice, so the next evenings mask its people as well.
FINNISH = [
    msg("wilma", "w-1", "2026-09-21T08:00:00+03:00",
        "Hei! Luokan retki on perjantaina. Kysykää lisää opettaja Korhoselta tai soittakaa 040 123 4567. "
        "Ilmoittautuminen: https://forms.kilo.example.fi/retki. Virtasen tunnilla Mia oli ahkera.",
        sender="Opettaja Virtanen", subject="Retki perjantaina", metadata={"student_number": "7731905"}),
    msg("gmail", "g-1", "2026-09-21T09:00:00+03:00",
        "Tervetuloa vanhempainiltaan torstaina. Eetun äiti tuo kahvia. "
        "Kysymykset: maija.makinen@kilo.example.fi",
        sender="Maija Mäkinen <maija.makinen@kilo.example.fi>", subject="Vanhempainilta torstaina",
        url="https://mail.google.com/mail/u/0/#search/rfc822msgid:abc@kilo.example.fi"),
    msg("gmail", "g-2", "2026-09-21T10:00:00+03:00", "Mian diagnoosi on liitteenä.",
        sender="Terveydenhoitaja Nieminen <nieminen@kilo.example.fi>", subject="Lausunto"),
    msg("whatsapp", "wa-1", "2026-09-21T18:00:00+03:00", "Anna Laine tässä, tuon pullaa perjantaina.",
        sender="Anna", chat="3B parents", kid="Mia", metadata={"from_me": False}),
    msg("whatsapp", "wa-2", "2026-09-21T18:05:00+03:00", "Kiitos!",
        sender="Parent", chat="3B parents", kid="Mia", metadata={"from_me": True}),
    msg("wilma", "w-3", "2026-09-21T11:00:00+03:00", "Koulupsykologi haluaa tavata ensi viikolla.",
        sender="Rehtori Saarinen", subject="Tapaaminen"),
]
ENGLISH = [
    msg("gmail", "g-3", "2026-09-22T08:00:00+03:00",
        "Training moves to Thursday. Ask coach Brown or Mrs Taylor if you have questions. "
        "Call +358 50 765 4321 or see www.kilo-fc.fi for the schedule. Mike",
        sender="Coach Mike Brown <mike.brown@kilofc.example.com>", subject="Training moved"),
    msg("gmail", "g-4", "2026-09-22T09:00:00+03:00", "We need to talk about what happened at break today.",
        sender="Principal Jane Smith <jane.smith@kilo.example.fi>", subject="Bullying incident at break"),
    msg("whatsapp", "wa-3", "2026-09-22T18:00:00+03:00",
        "Thanks Sarah! Tom's mum and Anna's dad will drive the kids on Saturday.",
        sender="Sarah", chat="Leo piano", kid="Leo", metadata={"from_me": False}),
    msg("gmail", "g-6", "2026-09-22T10:00:00+03:00", "Leo's speech therapy starts next week.",
        sender="Kilo School <info@kilo.example.fi>", subject="Next week"),
]
CHINESE = [
    msg("whatsapp", "wa-4", "2026-09-23T08:00:00+03:00", "明天钢琴课改到下午三点，请联系李老师，电话13800138000。王丽",
        sender="王丽", chat="Leo piano", kid="Leo", metadata={"from_me": False}),
    msg("whatsapp", "wa-5", "2026-09-23T09:00:00+03:00", "张老师说周五开家长会，小红妈妈也来。小狮妈妈请带水。",
        sender="小明妈妈", chat="3B parents", kid="Mia", metadata={"from_me": False}),
    msg("gmail", "g-5", "2026-09-23T10:00:00+03:00", "请下周来学校谈孩子的个别化教育计划。",
        sender="Kilo School <info@kilo.example.fi>", subject="会议"),
    msg("whatsapp", "wa-6", "2026-09-23T11:00:00+03:00", "明天警察来学校讲交通安全。",
        sender="Sarah", chat="3B parents", kid="Mia", metadata={"from_me": False}),
]
NIGHTS = {"2026-09-21": FINNISH, "2026-09-22": ENGLISH, "2026-09-23": CHINESE}


def archive(harness: Harness, nights: dict = NIGHTS, delivered: bool = True) -> None:
    for day, messages in nights.items():
        harness.write_archive(day, {
            "generated_at": f"{day}T21:00:00", "messages": [m.to_dict() for m in messages],
            "summary": {"per_kid": [{"kid": "Mia", "notices": [{"text": "A Notice", "refs": []}],
                                     "action_items": []}]},
            "calendar_created": [], "delivered": delivered})


def report(harness: Harness, capsys: pytest.CaptureFixture[str], *args: str) -> str:
    assert harness.cli("ai-filter-report", *args) == 0
    return capsys.readouterr().out


def held_back_list(harness: Harness, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
                   terminal: bool) -> str:
    """The report with --held-back, its output a terminal or, as when an AI assistant runs it, not."""
    monkeypatch.setattr(sys.stdout, "isatty", lambda: terminal)
    return report(harness, capsys, "--held-back")


def counts(out: str) -> dict[str, int]:
    """Each `label: number` line of the report."""
    return {m.group(1): int(m.group(2)) for m in re.finditer(r"^\s*([^:\n]+): (\d+)", out, re.MULTILINE)}


def test_a_household_with_no_archive_yet_is_told_when_there_will_be_one(harness, capsys):
    out = report(harness, capsys)

    assert "No archived evenings in ~/ParentRecap yet" in out
    assert "one each evening" in out


def test_an_archive_folder_with_no_evenings_in_it_gets_the_same_answer(harness, capsys):
    (harness.archive_dir / "logs").mkdir(parents=True)
    (harness.archive_dir / "2026-09-21.md").write_text("# Parent Recap")

    assert "No archived evenings in ~/ParentRecap yet" in report(harness, capsys)


def test_an_evening_whose_brief_wasnt_delivered_and_a_file_that_cant_be_read_are_skipped(harness, capsys):
    archive(harness, {"2026-09-21": FINNISH})
    archive(harness, {"2026-09-22": ENGLISH}, delivered=False)  # its messages came again the next evening
    harness.write_archive("2026-09-23", "{not json")

    found = counts(report(harness, capsys))

    assert (found["Evenings read"], found["Messages read"]) == (1, 6)
    assert found["Evenings skipped because their Brief wasn't delivered"] == 1
    assert found["Archive files that couldn't be read"] == 1


def test_it_counts_the_evenings_the_messages_and_those_held_back_by_category(harness, capsys):
    archive(harness)

    found = counts(report(harness, capsys))

    assert found["Evenings read"] == 3
    assert found["Messages read"] == 14
    assert found["Held back from the AI"] == 6
    assert {c: found[c] for c in ("health", "support", "bullying", "welfare", "staff")} == \
        {"health": 2, "support": 1, "bullying": 1, "welfare": 1, "staff": 1}


def test_it_counts_the_names_and_contact_details_that_become_placeholders_each_evening(harness, capsys):
    archive(harness)

    out = report(harness, capsys)

    # Senders: Virtanen, Maija Mäkinen, Anna, Mike Brown, Sarah, 王丽 and 小明. Text: Virtasen, Anna,
    # Brown, Mike, Sarah, 王丽, and Anna's dad on the English evening, whose list has the earlier
    # Brief's people too. Not the held-back messages' names, nor the parent's own post.
    assert "Names: 14 (7 in senders, 7 in subjects and text)" in out
    found = counts(out)
    # The Gmail message link and the Wilma student number never reach the AI at all.
    assert found["Phone numbers"] == 3
    assert found["Email addresses"] == 3
    assert found["Links"] == 2


def test_it_estimates_the_names_it_missed_beside_a_role_a_title_or_a_names_placeholder(harness, capsys):
    archive(harness)

    found = counts(report(harness, capsys))

    # Finnish: opettaja Korhoselta, Eetun äiti and the Laine after Anna's placeholder. English: Mrs
    # Taylor and Tom's mum. Chinese: 李老师, 张老师 and 小红妈妈. Not coach Brown or 王丽 (masked), not
    # the Kid Leo's alias in 小狮妈妈, not 周五开家长会 (on Friday, a parents' evening), and not a
    # word that starts a sentence.
    assert found["Likely missed names"] == 8


# Every name, contact detail and link in the archive, as the messages write them.
PRIVATE = ["Virtanen", "Virtasen", "Korhoselta", "Maija", "Mäkinen", "Eetu", "Nieminen", "Anna", "Laine",
           "Saarinen", "Mike", "Brown", "Taylor", "Jane", "Smith", "Tom", "Sarah", "王丽", "李", "张", "小明",
           "小红", "Mia", "Leo", "小狮", "040 123 4567", "+358 50 765 4321", "13800138000", "7731905",
           "forms.kilo.example.fi", "kilo-fc.fi", "mail.google.com", "@"]


@pytest.mark.parametrize("args", [(), ("--held-back",)], ids=["counts", "held-back-captured"])
def test_the_report_has_no_message_text_names_or_links_and_makes_no_network_call(harness, capsys,
                                                                                     monkeypatch, args):
    archive(harness)

    def no_network(*_a, **_k):
        raise AssertionError("the report reached for the network")
    for name in ("socket", "create_connection", "getaddrinfo"):
        monkeypatch.setattr(socket, name, no_network)

    out = report(harness, capsys, *args)  # captured, so not a terminal, as when an AI assistant runs it

    assert harness.commands == []  # no model call, and no other program either
    for value in PRIVATE:
        assert value not in out
    for m in [*FINNISH, *ENGLISH, *CHINESE]:
        for text in (m.subject, m.body, m.chat_name):
            assert not text or text not in out


def test_with_the_ai_filter_off_it_counts_what_the_filter_would_do(harness, capsys):
    harness.config["ai_filter"] = {"enabled": False}
    archive(harness)

    out = report(harness, capsys)

    assert "The AI filter is off" in out
    assert "Names: 14 (7 in senders, 7 in subjects and text)" in out
    found = counts(out)
    assert (found["Held back from the AI"], found["Links"], found["Likely missed names"]) == (6, 2, 8)


# What --held-back lists for the three evenings, by date: each line has the date, the Source, the
# category, the word that matched as the message writes it, the sender, the subject (a WhatsApp
# chat's name) and the text around the word.
HELD_BACK = [
    '  2026-09-21  Gmail     health    [diagnoosi]  Terveydenhoitaja Nieminen · Lausunto · '
    '"Mian diagnoosi on liitteenä."',
    '  2026-09-21  Wilma     staff     [Koulupsykologi]  Rehtori Saarinen · Tapaaminen · '
    '"Koulupsykologi haluaa tavata ensi viikolla."',
    '  2026-09-22  Gmail     bullying  [Bullying]  Principal Jane Smith · Bullying incident at break · '
    '"Bullying incident at break"',
    '  2026-09-22  Gmail     health    [therapy]  Kilo School · Next week · '
    '"Leo\'s speech therapy starts next week."',
    '  2026-09-23  Gmail     support   [个别化教育]  Kilo School · 会议 · "请下周来学校谈孩子的个别化教育计划。"',
    '  2026-09-23  WhatsApp  welfare   [警察]  Sarah · 3B parents · "明天警察来学校讲交通安全。"',
]


def listed(out: str) -> list[str]:
    """The report's lines listing a Held-back Message, in their order."""
    return [line for line in out.splitlines() if re.match(r"  \d{4}-\d{2}-\d{2}  ", line)]


def test_in_a_terminal_held_back_lists_every_held_back_message_after_the_counts(harness, capsys, monkeypatch):
    archive(harness)

    out = held_back_list(harness, capsys, monkeypatch, terminal=True)

    assert listed(out) == HELD_BACK
    assert out.index("Likely missed names") < out.index(HELD_BACK[0])
    assert "The 6 messages held back from the AI, by date." in out
    assert "then each held-back message with its own text, for your eyes only" in out
    assert "Counts only" not in out
    # The counts are the same as without the list.
    assert counts(out)["Held back from the AI"] == 6
    assert "Names: 14 (7 in senders, 7 in subjects and text)" in out


# A notice to the whole class about bullying, long and over several lines, read again after an
# evening whose Brief wasn't delivered, so it comes before that evening's own message. And one held
# back for its sender's title alone, with no subject.
NOTICE = ("Hyvät huoltajat,\n\nviikolla 40 koulussamme on teemaviikko.\n"
          "Maanantaina puhumme luokissa\tkiusaamisesta \x1b[1mja siitä, miten jokainen voi auttaa.\n"
          "Tiistaina on retki Nuuksioon, eväät mukaan.\n\nTerveisin rehtori")
LATE = [
    msg("gmail", "g-7", "2026-09-24T08:00:00+03:00", "Voisimmeko tavata ensi viikolla?",
        sender="Kuraattori Maija Laine <maija.laine@kilo.example.fi>"),
    msg("wilma", "w-4", "2026-09-20T12:00:00+03:00", NOTICE, sender="Rehtori Saarinen",
        subject="Viikkotiedote 40"),
]


def test_a_long_message_shows_about_40_characters_on_each_side_of_the_word_on_one_line(harness, capsys,
                                                                                        monkeypatch):
    archive(harness, {"2026-09-24": LATE})

    out = held_back_list(harness, capsys, monkeypatch, terminal=True)

    # By the messages' own dates, with no word cut in two at either end, the newlines and the tab
    # made spaces, and the terminal's escape code gone, all of it.
    assert listed(out) == [
        '  2026-09-20  Wilma     bullying  [kiusaamisesta]  Rehtori Saarinen · Viikkotiedote 40 · '
        '"…Maanantaina puhumme luokissa kiusaamisesta ja siitä, miten jokainen voi…"',
        '  2026-09-24  Gmail     staff     [Kuraattori]  Kuraattori Maija Laine · (no subject) · '
        '"Kuraattori Maija Laine"',
    ]
    assert "\x1b" not in out


def test_with_its_output_piped_or_captured_held_back_prints_the_counts_and_says_to_run_it_in_terminal(
        harness, capsys, monkeypatch):
    archive(harness)
    counts_only = report(harness, capsys)

    out = held_back_list(harness, capsys, monkeypatch, terminal=False)

    assert listed(out) == []
    assert out.startswith(counts_only)
    assert out[len(counts_only):] == (
        "\nThe list of held-back messages isn't printed here: it holds the messages' own text, so it prints "
        "only to a terminal, not when the output is piped or captured, as when an AI assistant runs the "
        "report. Run this same command in Terminal yourself to see it.\n")


def test_with_the_ai_filter_off_held_back_lists_what_the_filter_would_hold_back(harness, capsys, monkeypatch):
    harness.config["ai_filter"] = {"enabled": False}
    archive(harness)

    out = held_back_list(harness, capsys, monkeypatch, terminal=True)

    assert "The 6 messages the AI filter would hold back, by date." in out
    assert listed(out) == HELD_BACK


def test_held_back_with_nothing_held_back_says_so(harness, capsys, monkeypatch):
    archive(harness, {"2026-09-21": [m for m in FINNISH if m.external_id not in ("g-2", "w-3")]})

    out = held_back_list(harness, capsys, monkeypatch, terminal=True)

    assert listed(out) == []
    assert "No messages to list: none was held back." in out
