"""`parent-recap ai-filter-report`: what the AI filter catches on the Household's own archive.

It reads every evening in the archive and runs the evening run's own filter code on that evening's
messages: the Held-back Messages by category, and in the rest the names, phone numbers, email
addresses and links that become placeholders, with that evening's own list of names (its senders
and those of the earlier Briefs' evenings). It also estimates the names the filter likely missed.
It prints counts only, never message text, a name or a link, and it makes no network call and
starts no program. The counts decide whether a local name-recognition model is worth adding, and
whether the Kids' own names can be masked too (ADR 0013). It works with the filter switched off
as well: it then says what the filter would do.

With --held-back it also lists each Held-back Message with the word that matched and the text
around it, so the parent can see which words hold back routine notices. That is the messages' own
text, so it prints only to a terminal: piped or captured, as when an AI assistant runs it, the
report prints the counts alone and says to run it in Terminal."""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from . import ai_filter, held_back, summarize
from .actions import archive
from .collectors.base import Message
from .config import Config

_EVENING = re.compile(r"\d{4}-\d{2}-\d{2}\.raw\.json")  # one evening's messages, as archive.write names it
_PLACEHOLDER = re.compile(r"⟦([PELN])[0-9]+⟧")

# ── Likely misses: a name the filter didn't mask where a name usually stands, in the masked text.
# A capitalised word after a role or a title (opettaja Korhoselta, Mrs Taylor) or after a person's
# placeholder (⟦N1⟧ Laine), before a family role in the Finnish genitive or the English possessive
# (Eetun äiti, Tom's mum), and a Chinese surname or a 小 nickname before a title or a family role
# (李老师, 小红妈妈). An estimate: a name standing alone isn't found, and some words aren't names.
_UPPER, _LOWER = "A-ZÀ-ÖØ-Þ", "a-zß-öø-ÿ"
_CAPITALISED = rf"[{_UPPER}][{_LOWER}'’-]*[{_LOWER}]"
_ROLE = (r"(?i:opettaj|valmentaj|ohjaaj|rehtor|kuraattor|terveydenhoitaj|psykolog)[a-zäöå]*"
         r"|(?i:ope|teachers?|coach|principal|headteacher|nurse|trainer|lärare|rektor|tränare)"
         r"|Mr|Mrs|Ms|Dr|Herra|Rouva")
_FAMILY_FI = r"(?i:äiti|äidi|isä|mummo|mummi|vaari|pappa|huoltaja|vanhem)[a-zäöå]*"
_FAMILY_EN = r"(?i:mum|mom|mother|dad|father|parents?|grandma|grandpa|granny)\b"
_SURNAMES = ("王李张刘陈杨黄赵吴周徐孙马朱胡郭何林罗高郑梁谢宋唐许韩冯邓曹彭曾肖田董袁潘蒋蔡余杜叶程苏魏"
             "吕丁任沈姚卢姜崔钟谭陆汪范金石廖贾夏韦付方白邹孟熊秦邱江尹薛闫段雷侯龙史陶黎贺顾毛郝龚邵万钱严覃武戴莫孔汤")
_CJK = "\u3400-\u4dbf\u4e00-\u9fff"  # Chinese characters, as ai_filter reads names
_MISSED = re.compile(
    rf"(?<![\w’'])(?:{_ROLE})\.?\s+(?P<a>{_CAPITALISED})"
    rf"|⟦N[0-9]+⟧(?::[a-zäöå]+)?\s+(?P<b>{_CAPITALISED})"
    rf"|(?<![\w’'])(?P<c>[{_UPPER}][{_LOWER}]*n)\s+{_FAMILY_FI}"
    rf"|(?<![\w’'])(?P<d>{_CAPITALISED})['’]s\s+{_FAMILY_EN}"
    rf"|(?P<e>[{_SURNAMES}][{_CJK}]{{0,2}}?)(?=老师|教练|校长|主任|先生|女士|阿姨|叔叔)"
    rf"|(?P<f>[{_SURNAMES}小][{_CJK}]{{1,2}}?)(?=妈妈|爸爸|奶奶|爷爷|外婆|外公)")


def _kept(word: str, keep: list[str]) -> bool:
    """Whether `word` is one of the Household's own names in `keep`, also in a case form (Mian for
    Mia) or, in Chinese, with other characters around it."""
    word = word.casefold()
    return any(k in word if re.match(f"[{_CJK}]", k) else word == k or (len(k) >= 3 and word.startswith(k))
               for k in keep)


def _likely_misses(text: str, keep: list[str]) -> int:
    """How many names `text`, as the AI would get it, seems to have unmasked. The Household's own
    names in `keep` (the Kids' names and aliases, their school and class, the Sources') don't count."""
    return sum(not _kept(next(g for g in m.groups() if g), keep) for m in _MISSED.finditer(text))


def register(sub) -> None:
    p = sub.add_parser("ai-filter-report", help="Count what the AI filter catches in the archive of past "
                       "evenings (counts only, no message text unless --held-back in Terminal, nothing "
                       "leaves the Mac)")
    p.add_argument("--held-back", action="store_true",
                   help="Also list each held-back message with the word that matched and the text around "
                   "it. Only in Terminal: piped or captured, the report prints the counts alone")
    p.set_defaults(func=cmd_ai_filter_report)


@dataclass
class Report:
    """The counts over the evenings read so far."""
    evenings: int = 0
    messages: int = 0
    held_back: Counter[str] = field(default_factory=Counter)
    to_the_ai: int = 0
    masked: Counter[str] = field(default_factory=Counter)  # by kind, as ai_filter.KINDS names them
    names_in_senders: int = 0
    likely_missed: int = 0
    undelivered: int = 0
    unreadable: int = 0
    held: list[tuple[Message, held_back.Match]] = field(default_factory=list)  # for --held-back

    def add(self, cfg: Config, day: str, messages: list[dict[str, Any]]) -> None:
        """Count one evening's `messages`, as the archive keeps them, the way that evening's run
        filters them: its Held-back Messages, then the rest masked with the people of its senders
        and of the earlier Briefs' evenings, without what the model never gets."""
        self.evenings += 1
        self.messages += len(messages)
        to_the_ai = []
        for m in messages:
            message = Message.from_dict(m)
            found = held_back.match_message(message)
            if found:
                self.held_back[found.category] += 1
                self.held.append((message, found))
            else:
                to_the_ai.append(m)
        self.to_the_ai += len(to_the_ai)
        placeholders = ai_filter.Placeholders()
        summarize.add_the_nights_people(cfg, placeholders, to_the_ai, archive.recent_points(cfg, day))
        masked, _ = ai_filter.mask(to_the_ai, placeholders, drop=summarize.NOT_FOR_THE_MODEL)
        keep = [w.casefold() for term in summarize.household_names(cfg) for w in term.split()]
        for m in masked:
            placed = _PLACEHOLDER.findall(json.dumps(m, ensure_ascii=False))
            self.masked.update(ai_filter.KINDS[kind] for kind in placed)
            self.names_in_senders += _PLACEHOLDER.findall(m.get("sender") or "").count("N")
            self.likely_missed += sum(_likely_misses(m.get(key) or "", keep)
                                      for key in ("subject", "body", "chat_name"))


def _show(p: Path) -> str:
    home = Path.home()
    return f"~/{p.relative_to(home)}" if p.is_relative_to(home) else str(p)


def cmd_ai_filter_report(args: argparse.Namespace) -> int:
    cfg = Config.load(args.config)
    folder = cfg.archive.resolved_dir()
    files = sorted(p for p in folder.glob("*.raw.json") if _EVENING.fullmatch(p.name)) \
        if folder.is_dir() else []
    if not files:
        print(f"No archived evenings in {_show(folder)} yet. The evening Brief adds one each evening "
              "it runs, so try again after a few evenings.")
        return 0
    report = Report()
    for path in files:
        try:
            data = json.loads(path.read_text())
            messages = data.get("messages") or []
        except (OSError, ValueError, AttributeError):
            report.unreadable += 1
            continue
        if data.get("delivered") is False:  # its messages were read again the next evening
            report.undelivered += 1
            continue
        report.add(cfg, path.name[:10], messages)
    listing = args.held_back and sys.stdout.isatty()
    _print(report, folder, files[0].name[:10], files[-1].name[:10], cfg.ai_filter.enabled, listing)
    if listing:
        _print_held_back(report, cfg.ai_filter.enabled, ZoneInfo(cfg.timezone))
    elif args.held_back:
        print("\nThe list of held-back messages isn't printed here: it holds the messages' own text, so it "
              "prints only to a terminal, not when the output is piped or captured, as when an AI assistant "
              "runs the report. Run this same command in Terminal yourself to see it.")
    return 0


def _print(report: Report, folder: Path, first: str, last: str, on: bool, listing: bool) -> None:
    names = report.masked["person"]
    print(f"AI filter report for {_show(folder)}, {first} to {last}")
    print("Each archived evening goes through the AI filter as the evening Brief would send it today. "
          + ("The counts come first, then each held-back message with its own text, for your eyes only. "
             if listing else "Counts only: no message text, names or links. ") + "Nothing leaves this Mac.")
    if not on:
        print("The AI filter is off in this Household's config, so the AI gets the messages as they are. "
              "These counts show what it would do.")
    print(f"\nEvenings read: {report.evenings}")
    if report.undelivered:
        print(f"Evenings skipped because their Brief wasn't delivered: {report.undelivered} "
              "(their messages were read again the next evening)")
    if report.unreadable:
        print(f"Archive files that couldn't be read: {report.unreadable}")
    print(f"Messages read: {report.messages}")
    print(f"\nHeld back from the AI: {sum(report.held_back.values())}")
    for category in held_back.CATEGORIES:
        print(f"  {category}: {report.held_back[category]}")
    print(f"\nIn the {report.to_the_ai} messages that go to the AI, these become placeholders:")
    print(f"  Names: {names} ({report.names_in_senders} in senders, "
          f"{names - report.names_in_senders} in subjects and text)")
    print(f"  Phone numbers: {report.masked['phone']}")
    print(f"  Email addresses: {report.masked['email']}")
    print(f"  Links: {report.masked['link']}")
    print(f"\nLikely missed names: {report.likely_missed}")
    print("These are capitalised words and Chinese names next to a role or a title (opettaja, teacher, "
          "老师, -n äiti, 妈妈) or right after a name's placeholder, which weren't on that evening's list "
          "of names. It's an estimate: a name standing alone isn't counted, and a word next to a role "
          "isn't always a name.")


# ── --held-back: one line per Held-back Message, in the parent's own terminal only.
_AROUND = 40  # characters of the text shown on each side of the word that matched
_SOURCES = {"gmail": "Gmail", "wilma": "Wilma", "whatsapp": "WhatsApp", "myclub": "MyClub"}
# Runs of characters, each as the pattern of a run starting at a position and of one ending there.
# A match's word is the run of letters and digits around it, and an excerpt doesn't cut in two a run
# without spaces, a word with its punctuation. Neither takes in Chinese characters: Chinese has no
# spaces between words, and a Chinese entry is a word of its own.
_LETTERS = re.compile(rf"[^\W{_CJK}]*"), re.compile(rf"[^\W{_CJK}]*$")
_UNSPACED = re.compile(rf"[^\s{_CJK}]*"), re.compile(rf"[^\s{_CJK}]*$")


def _run_end(run: tuple[re.Pattern[str], re.Pattern[str]], text: str, at: int) -> int:
    """Where the `run` of characters starting at `at` ends."""
    return run[0].match(text, at).end()


def _run_start(run: tuple[re.Pattern[str], re.Pattern[str]], text: str, at: int, since: int = 0) -> int:
    """Where the `run` of characters ending at `at` starts, but not before `since`."""
    return at - len(run[1].search(text, since, at)[0])


def _one_line(text: str) -> str:
    """`text` on one line: newlines, tabs and other control characters, such as a terminal's escape
    codes, become spaces, and runs of spaces one."""
    return " ".join("".join(" " if unicodedata.category(c) == "Cc" else c for c in text).split())


def _excerpt(text: str, start: int, end: int) -> str:
    """The text around text[start:end] on one line: about _AROUND characters on each side, with no
    word cut in two, and … where the text goes on."""
    a, b = max(0, start - _AROUND), min(len(text), end + _AROUND)
    if a and _run_end(_UNSPACED, text, a - 1) > a:  # a word goes on on both sides of a: start after it
        a = _run_end(_UNSPACED, text, a)
    if b < len(text) and _run_end(_UNSPACED, text, b - 1) > b:  # and of b: end before it
        b = _run_start(_UNSPACED, text, b, since=end)
    return "…" * (a > 0) + _one_line(text[a:b]) + "…" * (b < len(text))


def _held_back_line(m: Message, found: held_back.Match, zone: ZoneInfo) -> str:
    """A Held-back Message as --held-back lists it: its date, Source and category, the word that
    matched as the message writes it, its sender and subject (a WhatsApp chat's name), and the text
    around the word."""
    text = held_back.fields(m)[found.field]
    start, end = _run_start(_LETTERS, text, found.start), _run_end(_LETTERS, text, found.end)
    excerpt = _excerpt(text, start, end)
    sender = _one_line(held_back.fields(m)["sender"]) or _one_line(m.sender or "")
    subject = _one_line((m.chat_name if m.source == "whatsapp" else m.subject) or "") or "(no subject)"
    day = m.timestamp.astimezone(zone).date().isoformat()
    return (f"  {day}  {_SOURCES.get(m.source, m.source):<8}  {found.category:<8}  "
            f"[{_one_line(text[start:end])}]  {sender or '(no sender)'} · {subject} · \"{excerpt}\"")


def _print_held_back(report: Report, on: bool, zone: ZoneInfo) -> None:
    """Every Held-back Message of the report, by date. Only for a terminal: they hold message text."""
    if not report.held:
        print("\nNo messages to list: none was held back.")
        return
    held = sorted(report.held, key=lambda h: h[0].timestamp.astimezone(timezone.utc))
    what = "held back from the AI" if on else "the AI filter would hold back"
    print(f"\nThe {len(held)} messages {what}, by date. Each line has the date, the Source, the "
          "category, [the word that matched], the sender · the subject or WhatsApp chat · \"the text around "
          "the word\":")
    for m, found in held:
        print(_held_back_line(m, found, zone))
