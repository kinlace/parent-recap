"""What reaches the AI: the one step every model-bound payload passes through (ADR 0013).

mask() takes any JSON-like payload, such as a list of messages or a prompt's whole payload, and
returns a copy in which phone numbers, email addresses (also the address in `Name <address>`) and
links are opaque placeholders such as ⟦P1⟧, ⟦E1⟧ and ⟦L1⟧, together with the Placeholders that
map them back. People the caller names with Placeholders.add_people become ⟦N1⟧ and so on, in
their Finnish case forms too (Maijalle as ⟦N1⟧:lle). INSTRUCTION tells the model to copy them
unchanged, and restore() puts the real values back in its reply: for a person, the exact text the
placeholder stood for where the model copied it. A placeholder the model changed
so far that it can't be read any more becomes the caller's general words for its kind ("a phone
number", "someone"), so the text around it stays.

Placeholders are kept in memory only, for one run. A run passes the same Placeholders to each of
its calls, so a value has the same placeholder in every payload. Values under the `keep` keys are
sent as they are: ids, which the model cites and the program checks (ADR 0001). Keys in `drop` are
left out wherever they are, for identifiers the model doesn't need.

The module knows nothing of the Brief or the Sources, so other tools can filter with it too."""
from __future__ import annotations

import logging
import re
from collections import Counter
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, TypeVar

log = logging.getLogger(__name__)

T = TypeVar("T")

# A placeholder's letter and the kind of value it stands for, the key of restore()'s words.
KINDS = {"P": "phone", "E": "email", "L": "link", "N": "person"}

# Fields that hold ids, which the model cites by value.
IDS = frozenset({"id", "external_id", "refs"})

# For the model's instructions, wherever a payload has placeholders.
INSTRUCTION = ("People's names (other than the kids'), phone numbers, email addresses and links are placeholders "
               "such as ⟦N1⟧, ⟦P1⟧, ⟦E1⟧ and ⟦L1⟧, and the program puts the real ones back. Copy each one you "
               "use exactly as written, with its brackets: never translate, reformat or renumber it, and never "
               "make one up. A placeholder is the same person or value wherever it stands. In Finnish, a case "
               "ending goes after a person's placeholder with a colon, as in ⟦N1⟧:lle")

# What a contact detail is written with: ASCII letters and digits, and the Latin letters of Finnish
# and other European names. Chinese and Japanese characters, full-width punctuation (，。：（）【】)
# and spaces end it, so one written into Chinese text with no space around it stops where the text starts.
_LATIN = "A-Za-z0-9À-ÖØ-öø-ÿ"
_EMAIL = rf"(?<![{_LATIN}._%+-])[{_LATIN}._%+-]+@[{_LATIN}-]+(?:\.[{_LATIN}-]+)+"
# A web link, as a calendar app would make it clickable. Without a scheme only common top-level
# domains count, so a missing space after a full stop or a file name is not a link. Citations
# checks links with the same pattern, so every link it knows is masked too.
TLDS = "com|net|org|info|biz|fi|se|eu|io|co|me|app|dev|xyz|site|online|link|page|top|click|ly|to|gl|ru|cn|uk|de|us"
LINK_CHARS = rf"[{_LATIN}._~:/?#@!$&*+,;=%-]"
LINK = (rf"(?:[a-z][a-z0-9+.-]*://|www\.){LINK_CHARS}+"
        rf"|(?<![{_LATIN}.-])(?:[{_LATIN}-]+\.)+(?:{TLDS})(?![{_LATIN}-])(?:/{LINK_CHARS}*)?")
# International (+358 40 123 4567, +44 (0)20 7946 0958), Finnish (040 123 4567, (09) 816 2000)
# and Chinese mobile (13800138000) numbers, never part of a longer number or Latin word.
_PHONE = (r"(?<![A-Za-z0-9+])(?:"
          r"\+[0-9]{1,3}[ -]?(?:\(0\)[ -]?)?\(?[0-9]{1,4}\)?(?:[ -]?[0-9]{2,4}){1,4}"
          r"|\(?0[0-9]{1,3}\)?[ -]?[0-9]{2,4}(?:[ -]?[0-9]{2,4}){1,3}"
          r"|1[3-9][0-9][ -]?[0-9]{4}[ -]?[0-9]{4}"
          r")(?![A-Za-z0-9])")
_CONTACT = re.compile(rf"(?P<E>{_EMAIL})|(?P<L>{LINK})|(?P<P>{_PHONE})", re.IGNORECASE)
_PHONE_DIGITS = range(7, 16)

# Finnish case endings and clitics a name may carry (Maijan, Maijalle, Maijaan, Maijallekin).
_CASES = ("n", "a", "ä", "ta", "tä", "na", "nä", "ksi", "lla", "llä", "lta", "ltä", "lle", "ssa", "ssä",
          "sta", "stä", "an", "än", "en", "in", "on", "un", "yn", "ön", "han", "hän", "hin", "hon", "hun")
_CLITICS = ("kin", "kaan", "kään")
_CASE = "(?:" + "|".join(sorted(_CASES, key=len, reverse=True)) + ")"
_CLITIC = "(?:" + "|".join(_CLITICS) + ")"
_VOWELS = "aeiouyäöåAEIOUYÄÖÅ"

# A placeholder as the model may give it back: another case, spaced out, with full-width digits, a
# letter that only looks like P, E, L or N (Cyrillic, Greek or full-width), and other brackets. The
# white square brackets ⟦⟧ 〚〛 are never in ordinary text, so either one of them is enough. Others,
# such as [P1], (P1) or 【P1】, need an opening and a closing bracket, and a bare P1 is never a
# placeholder. A Finnish case ending may follow after a colon (⟦N1⟧:lle).
_LETTERS = str.maketrans("pelnРрЕеΡρΕεΝＰＥＬＮｐｅｌｎ", "PELNPPEEPPEENPELNPELN")
_LETTER_SEEN = "[PELNpelnРрЕеΡρΕεΝＰＥＬＮｐｅｌｎ]"
_NUMBER_SEEN = r"[\s_-]*([0-9０-９]+)"
_ANY_CLOSE = "⟧〛】〕\\])）］"
_SEEN = re.compile(rf"(?:[⟦〚]\s*({_LETTER_SEEN}){_NUMBER_SEEN}(?:\s*[{_ANY_CLOSE}])?"
                   rf"|[【〔\\[(（［]\s*({_LETTER_SEEN}){_NUMBER_SEEN}\s*[{_ANY_CLOSE}]"
                   rf"|(?<![A-Za-z0-9])({_LETTER_SEEN}){_NUMBER_SEEN}\s*[⟧〛])"
                   rf"(?::(?=[a-zäöå])({_CASE}?{_CLITIC}?)(?![A-Za-zÀ-ÖØ-öø-ÿ]))?")
_ASCII_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")
# A letter and number the night's own text has, such as a football team's P2017. In other brackets
# than ⟦⟧ the model may be quoting it, so it isn't taken for a placeholder.
_AS_WRITTEN = re.compile(r"(?<![A-Za-z0-9])([PELN])\s*([0-9]+)(?![0-9])", re.IGNORECASE)

# ── People's names
#
# The caller gives the night's people as the fields that name them do: a sender's display name,
# say. A role or a label in brackets is not part of the name ("Opettaja Virtanen", "Anna (piano)",
# 王老师), an organisation beside a name is left out ("Maija Virtanen, Kilon koulu", 中文学校王老师),
# and an organisation alone is no person ("Kilo School", "Espoon kaupunki", info@). Never a
# dictionary of names: only the people given are masked.

# Words for what someone is rather than who: they stay in the text as they are.
_ROLES = frozenset("""
    opettaja ope luokanopettaja luokanvalvoja erityisopettaja rehtori apulaisrehtori terveydenhoitaja
    kouluterveydenhoitaja kuraattori psykologi koulupsykologi sihteeri koulusihteeri valmentaja
    ohjaaja huoltaja vanhempi vanhemmat
    teacher principal headteacher nurse coach trainer secretary parent parents rep representative
    mom mum mother dad father grandma grandpa granny
    the of and ja
""".split()) | frozenset(["äiti", "isä", "mummo", "mummi", "vaari", "pappa"])
# A family role after a name in the genitive: Eetun äiti is Eetu's mother.
_FAMILY = frozenset(["äiti", "isä", "mummo", "mummi", "vaari", "pappa", "huoltaja"])
# Words that make a display name an organisation or a service, alone or at the end of a Finnish
# compound (Kilonkoulu).
_ORGANISATIONS = frozenset("""
    school koulu koulun päiväkoti kaupunki city club seura ry oy oyj ab ltd inc gmbh fc team joukkue
    info noreply no-reply newsletter uutiskirje kirjasto library office kanslia toimisto support
    service palvelu admin system notifications forms google microsoft group ryhmä association
    yhdistys kerho institute instituutti academy akatemia science music musiikki sport sports
    urheilu arts taide center centre keskus foundation säätiö company events tapahtumat festival
    shop store kauppa studio theatre theater teatteri museum museo church seurakunta parish kunta
    municipality council kids junior juniors
""".split())
_COMPOUND_ORGANISATIONS = ("koulu", "koulun", "opisto", "seura", "kerho", "päiväkoti", "kaupunki", "kirjasto",
                           "lukio", "keskus", "akatemia", "säätiö", "yhdistys", "seurakunta")
# Mailboxes no one person reads: a sender writing from one is an organisation.
_SHARED_MAILBOXES = frozenset("""
    info noreply no-reply donotreply do-not-reply office newsletter news hello hei contact admin
    support team mail posti toimisto kanslia asiakaspalvelu palaute events tapahtumat uutiskirje
    notifications notification
""".split())
# Where a display name's parts meet: "Maija Virtanen, Kilon koulu", "Maija | FC Kilo", "X via Y".
_SEGMENTS = re.compile(r"\s*[,|/·•]\s*|\s+[-–—]\s+|\s+via\s+")
_CJK = "\u3400-\u4dbf\u4e00-\u9fff"  # Chinese characters, as Chinese text writes names
_CJK_ROLES = ("老师", "教练", "妈妈", "爸爸", "家长", "校长", "主任", "阿姨", "叔叔", "奶奶", "爷爷", "外婆", "外公",
              "妈", "爸")
_CJK_ORGANISATIONS = ("学校", "学院", "幼儿园", "俱乐部", "协会", "中心", "公司", "委员会")
_NAME_LETTERS = "A-Za-zÀ-ÖØ-öø-ÿ"
_NAME_WORD = re.compile(rf"[{_NAME_LETTERS}][{_NAME_LETTERS}'’.-]*|[{_CJK}]+")
_BRACKETED = re.compile(r"[(（\[【<][^)）\]】>]*[)）\]】>]")


@dataclass(frozen=True)
class _Name:
    """A person's name as one field gives it: its words, and for a Chinese surname alone the role
    written right after it (王 before 老师), which a match needs, so 王 is never masked on its own."""
    words: tuple[str, ...]
    before: str = ""


def _name(display: str) -> _Name | None:
    """The person in a display name, or None when it names no person."""
    mailbox = re.search(r"<([^<>@\s]+)@[^<>]*>", display)
    if mailbox and mailbox.group(1).casefold() in _SHARED_MAILBOXES:
        return None
    text = _BRACKETED.sub(" ", display)
    segments = [part for part in _SEGMENTS.split(text) if part.strip()]
    names = [_segment_name(part) for part in segments]
    if re.fullmatch(r"\s*\S+\s*,\s*\S+\s*", text) and all(n and not n.before for n in names):
        return _Name(tuple(w for n in names if n for w in n.words))  # Virtanen, Maija
    return next((n for n in names if n), None)


def _organisation(word: str) -> bool:
    folded = word.casefold()
    return folded in _ORGANISATIONS or folded.endswith(_COMPOUND_ORGANISATIONS)


def _genitive(word: str) -> bool:
    """Whether `word` may be a place name in the Finnish genitive (Espoon, Kilon), not a -nen name."""
    return word.endswith("n") and not word.endswith("nen")


def _segment_name(text: str) -> _Name | None:
    """The person in one part of a display name, without an organisation beside them. An
    organisation named the English way (Kilo School, Espoo Music Institute) is all of its part, and
    one named the Finnish way takes the place names in the genitive before it (Espoon kaupunki)."""
    found = _NAME_WORD.findall(text)
    while organisation := [i for i, w in enumerate(found) if not re.match(f"[{_CJK}]", w) and _organisation(w)]:
        i = organisation[-1]
        if found[i][:1].isupper():
            return None
        start = i
        while start > 0 and i - start < 2 and _genitive(found[start - 1]):
            start -= 1
        found = found[:start] + [w for w in found[i + 1:] if w[:1].isupper()]
    words: list[str] = []
    before = ""
    for word in found:
        if re.match(f"[{_CJK}]", word):
            cut = max((word.rfind(org) + len(org) for org in _CJK_ORGANISATIONS if org in word), default=0)
            word = word[cut:]  # 中文学校王老师
            role = next((r for r in _CJK_ROLES if word.endswith(r) and len(word) > len(r)), "")
            word = word[:len(word) - len(role)]
            if len(word) == 1 and role:
                before = role
            elif not 2 <= len(word) <= 4:
                continue
            words.append(word)
            continue
        word = re.sub(r"['’]s$|\.$", "", word)
        folded = word.casefold()
        if folded in _ROLES:
            if folded in _FAMILY and words and words[-1].endswith("n"):  # Eetun äiti, Virtasen isä
                last = words[-1]
                words[-1] = last[:-3] + "nen" if last.endswith("sen") else last[:-1]
            continue
        if len(word) >= 2:
            words.append(word)
    if not words or len(words) > 4:
        return None
    return _Name(tuple(words), before)


# The weak grade of the consonants before a word's last vowel (Pekka: Pekan, Ranta: Rannan, Satu:
# Sadun, Mäki: Mäen), the longer first. Matching may find a form no one writes, which is harmless.
_GRADES = (("kk", "k"), ("pp", "p"), ("tt", "t"), ("nt", "nn"), ("lt", "ll"), ("rt", "rr"), ("mp", "mm"),
           ("nk", "ng"), ("ht", "hd"), ("t", "d"), ("p", "v"), ("k", ""))


def _stems(word: str) -> list[tuple[str, str]]:
    """The other stems a Finnish case ending joins for `word`, each with the endings it takes:
    Virtanen as Virtasen and Virtasta, Pekka as Pekan, Niemi as Niemen, Lahti as Lahden, Laine as
    Laineen, Markus as Markuksen and Daniel as Danielin."""
    folded = word.casefold()
    if folded.endswith("nen"):
        return [(word[:-3] + "se", _CASE), (word[:-3] + "s", "(?:ta|tä)")]
    if folded[-1] in _VOWELS:
        body, vowel = word[:-1], word[-1]
        weak = next((body[:len(body) - len(s)] + w for s, w in _GRADES if body.casefold().endswith(s)), None)
        vowels = (vowel, "e") if folded[-1] == "i" else (vowel,)  # Niemi as Niemen, Lahti as Lahden
        stems = [stem + v for stem in (weak, body) for v in vowels if stem is not None and stem + v != word]
        if folded[-1] == "e":
            stems.append(word + "e")
        return [(stem, _CASE) for stem in dict.fromkeys(stems)]
    if folded[-1] == "s" and folded[-2] in _VOWELS:
        return [(word[:-1] + "kse", _CASE)]
    return [(word + "i", _CASE)]


def _form_pattern(form: _Name) -> str:
    """How one form of a name stands in text: its words with any spaces between them, a capital
    first letter as written (so the word toivo is not the name Toivo), never inside a longer Latin
    word, and with a Finnish case ending if any. Chinese text around it ends it as a space does."""
    *words, last = form.words
    latin = re.compile(f"[{_NAME_LETTERS}]")
    if latin.match(last) and len(last) >= 3:
        alternatives = [f"{re.escape(last)}{_CASE}?{_CLITIC}?",
                        *(f"{re.escape(stem)}{endings}{_CLITIC}?" for stem, endings in _stems(last))]
        last_pattern = "(?:" + "|".join(alternatives) + ")"
    else:
        last_pattern = re.escape(last)
    body = r"\s+".join([*map(re.escape, words), last_pattern])
    first = form.words[0][0]
    return ((f"(?<![{_LATIN}])" if latin.match(first) else "")
            + (f"(?={re.escape(first)})" if first.isupper() else "")
            + f"(?i:{body})"
            + (f"(?![{_LATIN}])" if latin.match(last[-1]) else "")
            + (f"(?={re.escape(form.before)})" if form.before else ""))


# The endings that take the weak grade of a kk, pp or tt before a name's last vowel (Pekan, Antille).
_WEAK = ("n", "lla", "llä", "lta", "ltä", "lle", "ssa", "ssä", "sta", "stä", "ksi")


def _inflected(name: str, ending: str) -> str:
    """`name` with a Finnish case `ending` the masked text never had it with: joined after a vowel
    (Maijalle), with the stem a -nen name (Virtaselle) or a kk, pp or tt before the last vowel
    (Pekalle) takes, and after a colon after any other consonant (Daniel:lle), where the right form
    would need the name's own stem."""
    if not ending or ending in _CLITICS:
        return name + ending
    folded = name.casefold()
    if folded.endswith("nen"):
        stem = name[:-3] + "s"
        return stem + (ending if ending.startswith("t") else "t" + ending if ending in ("a", "ä") else "e" + ending)
    if name[-1:] not in _VOWELS:
        return f"{name}:{ending}"
    case = next((ending[:-len(c)] for c in _CLITICS if ending.endswith(c)), ending)
    if len(folded) >= 3 and folded[-2] == folded[-3] and folded[-2] in "kpt" and case in _WEAK:
        return name[:-2] + name[-1] + ending
    return name + ending


@dataclass(frozen=True)
class _Seen:
    """One place a person's placeholder stands in the masked text: the Finnish case ending written
    after it, the exact text it replaced, and the ends of the words around it."""
    ending: str
    text: str
    before: str
    after: str


def _around(text: str, start: int, end: int) -> tuple[str, str]:
    """The last three letters of the word before `text[start:end]` and the first three of the word
    after it, for telling apart the texts one placeholder stood for (Ville Virtanen, Virtanen Maija)."""
    before = re.search(r"(\w+)\W*$", text[max(0, start - 40):start])
    after = re.match(r"\W*(\w+)", text[end:end + 40])
    return (before.group(1)[-3:].casefold() if before else "", after.group(1)[:3].casefold() if after else "")


def _likeliest(seen: list[_Seen], before: str, after: str) -> str:
    """The text among `seen` that stood where the same words stand around it, or else the one the
    night has most often, or else the first."""
    counts = Counter(s.text for s in seen)
    return max(seen, key=lambda s: ((bool(before) and s.before == before) + (bool(after) and s.after == after),
                                    counts[s.text])).text


class Placeholders:
    """One run's placeholders and the values they stand for, numbered per kind in the order the
    values are first met."""

    def __init__(self) -> None:
        self._tokens: dict[tuple[str, str], str] = {}
        self._values: dict[str, str] = {}   # token: value
        self._known: dict[str, str] = {}    # value: its first token
        self._known_pattern: re.Pattern[str] | None = None
        self._as_written: set[str] = set()   # P2017 and the like in the masked text
        self._counts = dict.fromkeys(KINDS, 0)
        self._names: list[_Name] = []        # the people given, in the order they were given
        self._keep: set[str] = set()         # words that are never a person's on their own
        self._people_pattern: re.Pattern[str] | None = None
        self._forms: list[tuple[str, list[str]]] = []  # by match group: its token, its last word's stems
        self._kept_pattern: re.Pattern[str] | None = None   # the kept names in their case forms (Leon)
        self._listed: dict[str, list[str]] = {}  # a person's token: the forms of their name listed
        self._seen: dict[str, list[_Seen]] = {}  # a person's token: where it stands in the masked text

    def add_people(self, names: Iterable[str], keep_names: Iterable[str] = ()) -> None:
        """Mask these people's names from now on, as display names give them ("Maija Virtanen",
        "Opettaja Virtanen", "Anna (piano)", 李明妈妈). A full name and each part of it are one
        person, in their Finnish case forms too, unless a part is also another person's: then
        that part has a placeholder of its own. `keep_names` stay as they are, alone, in a Finnish
        case form (Leon for the Kid Leo) or as part of a name, such as the Household's own: a
        person with only those names isn't masked."""
        self._keep |= {w.casefold() for term in keep_names for w in (term, *term.split()) if w.strip()}
        self._kept_pattern = re.compile("|".join(f"(?:{_form_pattern(_Name(tuple(term.split())))})"
                                                 for term in sorted(self._keep, key=len, reverse=True)),
                                        re.IGNORECASE) if self._keep else None
        for display in names:
            name = _name(display or "")
            if name and name not in self._names and not all(w.casefold() in self._keep for w in name.words):
                self._names.append(name)
        self._people_pattern = self._people()

    def _people(self) -> re.Pattern[str] | None:
        """One pattern for every form of every person given, the longest first, whose match groups
        line up with self._forms."""
        groups: list[list[_Name]] = []   # one per person, the longest name first
        for name in sorted(self._names, key=lambda n: -len(n.words)):
            words = {w.casefold() for w in name.words}
            owners = [g for g in groups if g[0].before == name.before
                      and words <= {w.casefold() for n in g for w in n.words}]
            if len(owners) == 1:
                owners[0].append(name)
            else:
                groups.append([name])
        groups.sort(key=lambda g: min(self._names.index(n) for n in g))
        tokens = [self._token("N", " ".join(g[0].words)) for g in groups]

        owners_of: dict[tuple[str, ...], set[int]] = {}
        written: dict[tuple[str, ...], _Name] = {}
        for i, group in enumerate(groups):
            for name in group:
                w = name.words
                forms = [w, *([w[::-1]] if len(w) == 2 else []), *([(w[0], w[-1])] if len(w) > 2 else []),
                         *((part,) for part in w if len(w) > 1 and part.casefold() not in self._keep)]
                if len(w) == 1 and len(w[0]) in (3, 4) and re.match(f"[{_CJK}]", w[0]) and not name.before:
                    given = w[0][-2:]  # 王小明 as 小明, 欧阳小明 as 小明
                    forms += [(given,)] if given not in self._keep else []
                for form in forms:
                    key = (*(p.casefold() for p in form), name.before)
                    owners_of.setdefault(key, set()).add(i)
                    written.setdefault(key, _Name(form, name.before))
        alternatives = []
        self._forms, self._listed = [], {}
        for key, form in sorted(written.items(), key=lambda kv: -len(" ".join(kv[1].words))):
            owners = owners_of[key]
            token = tokens[next(iter(owners))] if len(owners) == 1 else self._token("N", " ".join(form.words))
            last = form.words[-1]
            stems = [last, *(s for s, _ in _stems(last))] if re.match(f"[{_NAME_LETTERS}]", last) else [last]
            alternatives.append(f"(?P<f{len(self._forms)}>{_form_pattern(form)})")
            self._forms.append((token, sorted(stems, key=len, reverse=True)))
            self._listed.setdefault(token, []).append(" ".join(form.words))
        return re.compile("|".join(alternatives)) if alternatives else None

    def _mask_people(self, text: str) -> str:
        """`text` with each person given as their placeholder and Finnish case ending. A kept name
        in a case form (Leon for the Kid Leo) stays. Where each placeholder stands is recorded with
        the words around it in the masked text, as the model sees and copies them."""
        if not self._people_pattern:
            return text
        out: list[str] = []
        placed: list[tuple[str, str, str, int, int]] = []   # token, ending, text, where in the output
        end = length = 0
        for m in self._people_pattern.finditer(text):
            found = m.group()
            if self._kept_pattern and self._kept_pattern.fullmatch(found):
                continue
            token, stems = self._forms[int((m.lastgroup or "f0")[1:])]
            last = re.split(r"\s+", found)[-1]
            ending = next((last[len(s):] for s in stems if last.casefold().startswith(s.casefold())), "")
            placeholder = token + (f":{ending}" if ending else "")
            out += [text[end:m.start()], placeholder]
            length += m.start() - end
            placed.append((token, ending, found, length, length + len(placeholder)))
            length += len(placeholder)
            end = m.end()
        masked = "".join([*out, text[end:]])
        for token, ending, found, start, stop in placed:
            self._seen.setdefault(token, []).append(_Seen(ending, found, *_around(masked, start, stop)))
        return masked

    def _person_text(self, token: str, ending: str, before: str, after: str) -> str:
        """What a person's placeholder with this Finnish case `ending` stood for: the exact text,
        where the masked text had it so. Otherwise the name as the text wrote it without an ending,
        or the shortest form of it listed, with the ending."""
        seen = self._seen.get(token, [])
        same = [s for s in seen if s.ending == ending]
        if same:
            return _likeliest(same, before, after)
        bare = [s for s in seen if not s.ending]
        return _inflected(_likeliest(bare, before, after) if bare else min(self._listed[token], key=len), ending)

    def _token(self, kind: str, value: str) -> str:
        if (kind, value) not in self._tokens:
            self._counts[kind] += 1
            token = f"⟦{kind}{self._counts[kind]}⟧"
            self._tokens[kind, value] = token
            self._values[token] = value
            if kind != "N":  # a name is found by its own pattern, never inside a longer word
                self._known.setdefault(value, token)
                self._known_pattern = None
        return self._tokens[kind, value]

    def _known_values(self) -> re.Pattern[str]:
        """Every value already given a placeholder, the longest first so none splits a longer one."""
        if self._known_pattern is None:
            self._known_pattern = re.compile("|".join(map(re.escape, sorted(self._known, key=len, reverse=True))))
        return self._known_pattern

    def mask_text(self, text: str) -> str:
        """`text` with its contact details and the people given as placeholders. A contact detail
        this run has already seen is replaced as it is, wherever it stands, before the patterns look
        for new ones: a value put back into a reply may stand where the patterns wouldn't find it,
        such as right after a word. Names come last, so a name in an email address goes with it."""
        self._as_written.update(f"{k.upper()}{int(n)}" for k, n in _AS_WRITTEN.findall(text))
        if self._known:
            text = self._known_values().sub(lambda m: self._known[m.group()], text)

        def replace(m: re.Match[str]) -> str:
            kind = m.lastgroup or ""
            found = m.group()
            if kind == "L":  # a full stop or comma after a link ends the sentence, not the link
                value = found.rstrip(".,;:!?")
                return self._token(kind, value) + found[len(value):]
            if kind == "P" and sum(c.isdigit() for c in found) not in _PHONE_DIGITS:
                return found
            return self._token(kind, found)
        return self._mask_people(_CONTACT.sub(replace, text))

    def restore_text(self, text: str, words: Mapping[str, str]) -> str:
        """`text` with the real values in place of the placeholders the model wrote, or `words` for
        the kind of one it changed beyond repair. A person's placeholder gets the exact text it
        stood for with the Finnish case ending written after it (⟦N1⟧:lle as Maijalle), told apart
        by the words around it when it stood for more than one."""
        def replace(m: re.Match[str]) -> str:
            other_brackets = bool(m.group(3))
            letter, digits = next((m.group(i), m.group(i + 1)) for i in (1, 3, 5) if m.group(i))
            kind, number = letter.translate(_LETTERS), int(digits.translate(_ASCII_DIGITS))
            ending = m.group(7) or ""
            if other_brackets and f"{kind}{number}" in self._as_written:
                return m.group()
            token = f"⟦{kind}{number}⟧"
            if token not in self._values:
                log.warning("The model changed a placeholder so it can't be put back (%r); "
                            "the Brief says what kind of value it was", m.group())
                word = words[KINDS[kind]]
                return _inflected(word, ending) if kind == "N" else word + (f":{ending}" if ending else "")
            if kind == "N":
                return self._person_text(token, ending, *_around(m.string, m.start(), m.end()))
            return self._values[token] + (f":{ending}" if ending else "")
        return _SEEN.sub(replace, text)


def mask(payload: T, placeholders: Placeholders | None = None, *,
         keep: Collection[str] = IDS, drop: Collection[str] = ()) -> tuple[T, Placeholders]:
    """A copy of `payload` with its contact details as placeholders, and the Placeholders that
    hold them: `placeholders` when given, so a run's calls share one numbering."""
    placeholders = placeholders if placeholders is not None else Placeholders()
    return _walk(payload, placeholders.mask_text, keep, drop), placeholders


def restore(reply: T, placeholders: Placeholders, words: Mapping[str, str]) -> T:
    """A copy of the model's `reply` with the real values in place of their placeholders. One
    that can't be read any more becomes `words` for its kind: {"phone": ..., "email": ..., "link": ...}."""
    return _walk(reply, lambda text: placeholders.restore_text(text, words))


def _walk(value: Any, text: Callable[[str], str], keep: Collection[str] = (),
          drop: Collection[str] = ()) -> Any:
    """A copy of a JSON-like `value` with `text` applied to every string in it, except under the
    `keep` keys, which are copied as they are, and without the `drop` keys."""
    if isinstance(value, str):
        return text(value)
    if isinstance(value, Mapping):
        return {k: _walk(v, str) if k in keep else _walk(v, text, keep, drop)
                for k, v in value.items() if k not in drop}
    if isinstance(value, (list, tuple)):
        return [_walk(v, text, keep, drop) for v in value]
    return value
