"""Every piece of text the program itself writes into a Brief or Weekend Picks, reviewed for `en`,
`zh` and `fi`.

The model writes the rest in the same language (see summarize.system_prompt and translate), so a
Brief is never half one language and half the other. Any other language gets these tables
translated once (see languages.py). Terms follow CONTEXT.md: English canonical, with its Chinese
and Finnish names in a `zh` or `fi` Brief."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Annotated

from pydantic import BeforeValidator, StringConstraints

# A language by its two- or three-letter code: en, zh, or any other such as sv. Written in any case
# in the config; a region (en-GB) is left out, so en and zh always find their reviewed tables.
Language = Annotated[str, BeforeValidator(lambda v: v.strip().lower() if isinstance(v, str) else v),
                     StringConstraints(pattern=r"^[a-z]{2,3}$")]


# The product name in the subject and at the top of every Brief. The brand stays English in every
# language.
PRODUCT_NAME = "Parent Recap"


@dataclass(frozen=True)
class Plural:
    """A count in words: `one` for a single one and `other` for several, with {n} for the number."""
    one: str
    other: str

    def __call__(self, n: int) -> str:
        return (self.one if n == 1 else self.other).format(n=n)


@dataclass(frozen=True)
class BriefText:
    # ── What the prompt asks the model for
    language_name: str
    quotes: str                         # what to quote with inside JSON strings instead of "
    weekdays: tuple[str, ...]           # Monday first, for the prompt's date_reference
    household: str                      # `kid` of an entry about the whole Household
    who: tuple[str, str, str]           # mom, dad, either
    re_reminder: str                    # prefix of a re-reminded Action Item

    # ── Coverage line
    read_tonight: str                   # {counts}
    messages: Plural
    events: Plural
    not_read: str                       # {source} {reason}
    partly_read: str                    # {source} {reason}
    may_be_incomplete: str              # {missed}
    missed_sep: str
    permission_denied: str
    whatsapp_permission_denied: str     # WhatsApp's, whose likely cause is a changed Python
    login_failed: str
    message_unreadable: str             # a Source skipped a Message it couldn't read

    # ── The short Brief of a night when no Source could be read
    nothing_read: str
    source_problem: str                 # {source} {reason} {fix}
    fix_login: str                      # {assistant} {source}
    fix_permission: str                 # {assistant} {source}
    fix_other: str                      # {assistant}

    # ── Rule-based fallback when the model fails
    fallback_header: str
    unsorted: str
    fallback_count: str                 # {count}, as `messages` gives it
    fallback_more: str                  # {n}
    # At the top of a Brief made from a model reply that was cut off
    reply_incomplete: str               # {assistant}
    message_time: str                   # when a message came: {day} {month} {hour} {minute}

    # ── At the top of the original, sent to a Recipient whose translation failed
    translation_failed: str

    # ── Held-back Messages, which the program lists itself, without the AI (ADR 0013)
    held_back: str                      # the heading
    held_back_note: str                 # {assistant}
    held_back_open: str                 # the link to read one at its Source
    held_back_read_in: str              # {source}, for one without a link, such as WhatsApp's
    no_subject: str

    # ── Sections and footer
    action_items: str
    due: str                            # {date}, after an Action Item
    # How the Brief writes a date (the header, a due date) and a date with a time (an event's start)
    months: tuple[str, ...]             # short month names, January first, for {month_name}
    date: str                           # {weekday} {day} and {month_name}, or {month} for its number
    date_time: str                      # what `date` has, and {hour} {minute}
    new_events: str
    new_events_line: str                # the plain-text heading
    written_by: str                     # {assistant}
    untitled: str

    # ── In place of a placeholder the model changed so it can't be put back (ADR 0013)
    phone_number: str
    email_address: str
    link: str

    # ── Calendar notes
    ics_hint: str
    ics_hint_mixed: str
    reauth: str                         # {assistant}
    calendar_not_authorized: str
    calendar_expired: str
    calendar_write_failed: str          # {error}
    ics_fallback: str

    # ── Archive Markdown, which stays in this language like the rest of the Brief (ADR 0004)
    archive_digest: str
    archive_notices: str
    archive_action_items: str
    archive_new_events: str
    archive_messages: str

    # ── Feedback link labels (the Form's own choices are English for every language, see feedback.py)
    feedback_saved: str
    feedback_wrong: str
    feedback_digest_wrong: str

    def placeholder_words(self) -> dict[str, str]:
        """What this language says in place of a placeholder the model changed beyond repair, by kind."""
        return {"phone": self.phone_number, "email": self.email_address, "link": self.link}

    def on(self, day: date) -> str:
        """`day` the way this language writes a date."""
        return self.date.format(**self._date_fields(day))

    def at(self, moment: datetime) -> str:
        """`moment` the way this language writes a date and time, in the timezone it is given in."""
        return self.date_time.format(**self._date_fields(moment), hour=moment.hour, minute=moment.minute)

    def _date_fields(self, day: date) -> dict:
        return {"weekday": self.weekdays[day.weekday()], "day": day.day, "month": day.month,
                "month_name": self.months[day.month - 1]}


EN = BriefText(
    language_name="English",
    quotes="‘single quotes’",
    weekdays=("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"),
    household="Household",
    who=("Mom", "Dad", "Either"),
    re_reminder="(Reminder) ",
    read_tonight="📥 Read tonight: {counts}",
    messages=Plural("{n} message", "{n} messages"),
    events=Plural("{n} event", "{n} events"),
    not_read="{source} not read ({reason})",
    partly_read="{source} partly read ({reason})",
    may_be_incomplete="⚠️ {missed}. Tonight's Brief may be incomplete.",
    missed_sep=", ",
    permission_denied="macOS permission denied",
    whatsapp_permission_denied="macOS permission denied, maybe because Parent Recap's Python changed",
    login_failed="login failed",
    message_unreadable="a message couldn't be read",
    nothing_read="⚠️ No Source could be read tonight, so there is no Digest. Their messages will be "
                 "in the first Brief after they can be read again.",
    source_problem="• {source}: {reason}. {fix}",
    fix_login="Tell {assistant} “re-authorize {source}” to fix it.",
    fix_permission="Tell {assistant} “Parent Recap can't read {source}” to fix it.",
    fix_other="If the Mac was offline, this fixes itself. If it happens again, tell {assistant} "
              "“check Parent Recap”.",
    fallback_header="⚠️ Tonight's Digest could not be written, so here are the raw messages "
                    "(all of them are in the archive in ~/ParentRecap):",
    unsorted="Unsorted",
    fallback_count=" ({count})",
    fallback_more="  …{n} more in the archive",
    reply_incomplete="⚠️ {assistant}'s reply tonight was cut off, so this Brief may be missing some items. "
                     "All of tonight's messages are in the archive in ~/ParentRecap.",
    message_time="{month:02}-{day:02} {hour:02}:{minute:02}",
    translation_failed="⚠️ Tonight's Brief couldn't be translated, so here it is as it was written.",
    held_back="🔒 Held-back messages",
    held_back_note="These messages looked sensitive, such as health, support, bullying or child welfare, "
                   "so they were not sent to {assistant}. Read them where they came from.",
    held_back_open="open it",
    held_back_read_in="read it in {source}",
    no_subject="(no subject)",
    action_items="✅ Action Items",
    due="by {date}",
    months=("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
    date="{weekday} {day} {month_name}",
    date_time="{weekday} {day} {month_name} {hour:02}:{minute:02}",
    new_events="📅 New calendar events",
    new_events_line="📅 New calendar events:",
    written_by="Written by {assistant}",
    untitled="(untitled)",
    phone_number="a phone number",
    email_address="an email address",
    link="a link",
    ics_hint="They are in the attached .ics. Open it to add them to your calendar.",
    ics_hint_mixed="Linked events are in Google Calendar; the rest are in the attached .ics. "
                   "Open it to add them to your calendar.",
    reauth="Tell {assistant} “re-authorize Google Calendar” to fix it. ",
    calendar_not_authorized="⚠️ Google Calendar is not authorized yet, so new events were not added to it. ",
    calendar_expired="⚠️ Google Calendar access has expired, so new events were not added to it. ",
    calendar_write_failed="⚠️ Adding to Google Calendar failed: {error}. ",
    ics_fallback="The new events are in the .ics attached to this email; open it to add them to your calendar.",
    archive_digest="## Digest",
    archive_notices="**Notices:**",
    archive_action_items="**Action Items:**",
    archive_new_events="## New calendar events",
    archive_messages="## Messages",
    feedback_saved="⭐ Glad this was here",
    feedback_wrong="❌ This is wrong",
    feedback_digest_wrong="❌ The Digest has a mistake",
)

ZH = BriefText(
    language_name="Simplified Chinese",
    quotes="《》 or 「」",
    weekdays=("周一", "周二", "周三", "周四", "周五", "周六", "周日"),
    household="全家",
    who=("妈妈", "爸爸", "任一"),
    re_reminder="（再提醒）",
    read_tonight="📥 今晚读取：{counts}",
    messages=Plural("{n} 条", "{n} 条"),
    events=Plural("{n} 个日程", "{n} 个日程"),
    not_read="{source} 没读到（{reason}）",
    partly_read="{source} 部分没读到（{reason}）",
    may_be_incomplete="⚠️ {missed}，今天的日报可能缺这一块。",
    missed_sep="，",
    permission_denied="macOS 权限被拒绝",
    whatsapp_permission_denied="macOS 权限被拒绝，可能是 Parent Recap 的 Python 换了",
    login_failed="登录失败",
    message_unreadable="有一条消息读不出来",
    nothing_read="⚠️ 今晚所有信息源都没读到，所以没有摘要。等它们恢复后，这些消息会出现在下一份日报里。",
    source_problem="• {source}：{reason}。{fix}",
    fix_login="跟 {assistant} 说「重新授权 {source}」即可修复。",
    fix_permission="跟 {assistant} 说「Parent Recap 读不了 {source}」即可修复。",
    fix_other="如果当时 Mac 没联网，会自己恢复。如果再次出现，跟 {assistant} 说「检查 Parent Recap」。",
    fallback_header="⚠️ 今日 LLM 总结失败，以下是原始消息清单（详见 ~/ParentRecap 归档）：",
    unsorted="未分类",
    fallback_count="（{count}）",
    fallback_more="  …另外 {n} 条，见归档",
    reply_incomplete="⚠️ 今晚 {assistant} 的回复不完整，这份日报可能漏了几条。今晚的原始消息都在 ~/ParentRecap 归档里。",
    message_time="{month:02}-{day:02} {hour:02}:{minute:02}",
    translation_failed="⚠️ 今晚的日报没能翻译成功，下面是原文。",
    held_back="🔒 未交给 AI 的消息",
    held_back_note="这些消息看起来涉及健康、特殊支持、霸凌或儿童保护等敏感内容，所以没有交给 {assistant}。请到原处查看。",
    held_back_open="打开原文",
    held_back_read_in="请在 {source} 里查看",
    no_subject="（无主题）",
    action_items="✅ 待办",
    due="by {date}",
    months=("1月", "2月", "3月", "4月", "5月", "6月", "7月", "8月", "9月", "10月", "11月", "12月"),
    date="{month_name}{day}日 {weekday}",
    date_time="{month_name}{day}日 {weekday} {hour:02}:{minute:02}",
    new_events="📅 新日历事件",
    new_events_line="📅 新加日历事件:",
    written_by="由 {assistant} 生成",
    untitled="(无标题)",
    phone_number="一个电话号码",
    email_address="一个邮箱地址",
    link="一个链接",
    ics_hint="已打包在邮件附件的 .ics 里，点开附件即可一键加入你的日历。",
    ics_hint_mixed="带链接的已写入 Google 日历，其余已打包在邮件附件的 .ics 里，点开附件即可一键加入你的日历。",
    reauth="跟 {assistant} 说「重新授权 Google Calendar」即可修复。",
    calendar_not_authorized="⚠️ Google Calendar 还没有授权，新事件没有写入日历。",
    calendar_expired="⚠️ Google Calendar 授权已过期，新事件没有写入日历。",
    calendar_write_failed="⚠️ Google Calendar 写入失败：{error}。",
    ics_fallback="这些新事件已打包在邮件附件的 .ics 里，点开附件即可加入日历。",
    archive_digest="## 摘要",
    archive_notices="**注意事项：**",
    archive_action_items="**待办：**",
    archive_new_events="## 新日历事件",
    archive_messages="## 原始消息",
    feedback_saved="⭐ 幸好有这条",
    feedback_wrong="❌ 这条错了",
    feedback_digest_wrong="❌ 摘要里有错",
)

FI = BriefText(
    language_name="Finnish",
    quotes="”näin”",
    weekdays=("ma", "ti", "ke", "to", "pe", "la", "su"),
    household="Koko perhe",
    who=("Äiti", "Isä", "Kumpi tahansa"),
    re_reminder="(Muistutus) ",
    read_tonight="📥 Luettu tänä iltana: {counts}",
    messages=Plural("{n} viesti", "{n} viestiä"),
    events=Plural("{n} tapahtuma", "{n} tapahtumaa"),
    not_read="{source} jäi lukematta ({reason})",
    partly_read="{source} luettiin vain osittain ({reason})",
    may_be_incomplete="⚠️ {missed}. Tämäniltaisesta koosteesta voi puuttua jotain.",
    missed_sep=", ",
    permission_denied="macOS ei antanut käyttöoikeutta",
    whatsapp_permission_denied="macOS ei antanut käyttöoikeutta, ehkä koska Parent Recapin Python vaihtui",
    login_failed="kirjautuminen epäonnistui",
    fallback_header="⚠️ Tämäniltaista yhteenvetoa ei saatu kirjoitettua, joten tässä ovat viestit sellaisenaan "
                    "(kaikki ovat tallessa arkistossa ~/ParentRecap):",
    unsorted="Muut viestit",
    fallback_count=" ({count})",
    fallback_more="  …arkistossa vielä {n} lisää",
    # Added after the first review and not yet checked by a Finnish speaker: reply_incomplete,
    # message_unreadable, nothing_read, source_problem, fix_login, fix_permission, fix_other,
    # whatsapp_permission_denied, phone_number, email_address, link, held_back, held_back_note,
    # held_back_open, held_back_read_in, no_subject.
    reply_incomplete="⚠️ {assistant}-avustajan vastaus katkesi tänä iltana, joten koosteesta voi puuttua "
                     "joitakin kohtia. Kaikki tämäniltaiset viestit ovat arkistossa kansiossa ~/ParentRecap.",
    message_unreadable="yhtä viestiä ei voitu lukea",
    nothing_read="⚠️ Tänä iltana yhtäkään lähdettä ei voitu lukea, joten yhteenvetoa ei ole. Niiden viestit "
                 "tulevat ensimmäiseen koosteeseen sen jälkeen, kun ne voidaan taas lukea.",
    source_problem="• {source}: {reason}. {fix}",
    fix_login="Voit korjata tämän kirjoittamalla {assistant}-avustajalle ”valtuuta {source} uudelleen”.",
    fix_permission="Voit korjata tämän kirjoittamalla {assistant}-avustajalle "
                   "”Parent Recap ei pysty lukemaan lähdettä {source}”.",
    fix_other="Jos Mac oli offline-tilassa, tämä korjaantuu itsestään. Jos sama toistuu, kirjoita "
              "{assistant}-avustajalle ”tarkista Parent Recap”.",
    message_time="{day}.{month}. klo {hour}.{minute:02}",
    translation_failed="⚠️ Tämäniltaista koostetta ei saatu käännettyä, joten tässä se on alkuperäisellä kielellä.",
    held_back="🔒 Tekoälyltä piilotetut viestit",
    held_back_note="Nämä viestit vaikuttivat arkaluonteisilta (esimerkiksi terveys, tuki, kiusaaminen tai "
                   "lastensuojelu), joten niitä ei lähetetty {assistant}-avustajalle. Lue ne alkuperäisessä lähteessä.",
    held_back_open="avaa viesti",
    held_back_read_in="lue viesti lähteessä {source}",
    no_subject="(ei aihetta)",
    action_items="✅ Hoidettavat",
    due="viimeistään {date}",
    months=("tammi", "helmi", "maalis", "huhti", "touko", "kesä", "heinä", "elo", "syys", "loka", "marras",
            "joulu"),
    date="{weekday} {day}.{month}.",
    date_time="{weekday} {day}.{month}. klo {hour}.{minute:02}",
    new_events="📅 Uudet kalenteritapahtumat",
    new_events_line="📅 Uudet kalenteritapahtumat:",
    written_by="Kirjoittanut: {assistant}",
    untitled="(ei otsikkoa)",
    phone_number="puhelinnumero",
    email_address="sähköpostiosoite",
    link="linkki",
    ics_hint="Ne ovat liitteenä olevassa .ics-tiedostossa. Avaa se, niin saat ne kalenteriisi.",
    ics_hint_mixed="Osa tapahtumista on lisätty Google Kalenteriin, loput ovat liitteenä olevassa .ics-tiedostossa. "
                   "Avaa se, niin saat ne kalenteriisi.",
    reauth="Voit korjata tämän kirjoittamalla {assistant}-avustajalle ”valtuuta Google Kalenteri uudelleen”. ",
    calendar_not_authorized="⚠️ Google Kalenteria ei ole vielä yhdistetty, joten uusia tapahtumia ei lisätty siihen. ",
    calendar_expired="⚠️ Google Kalenterin käyttöoikeus on vanhentunut, joten uusia tapahtumia ei lisätty siihen. ",
    calendar_write_failed="⚠️ Tapahtumien lisääminen Google Kalenteriin epäonnistui: {error}. ",
    ics_fallback="Uudet tapahtumat ovat tämän sähköpostin liitteenä olevassa .ics-tiedostossa; avaa se, "
                 "niin saat ne kalenteriisi.",
    archive_digest="## Yhteenveto",
    archive_notices="**Tiedoksi:**",
    archive_action_items="**Hoidettavat:**",
    archive_new_events="## Uudet kalenteritapahtumat",
    archive_messages="## Viestit",
    feedback_saved="⭐ Hyvä, että tämä oli mukana",
    feedback_wrong="❌ Tämä on väärin",
    feedback_digest_wrong="❌ Yhteenvedossa on virhe",
)

TEXT: dict[Language, BriefText] = {"en": EN, "zh": ZH, "fi": FI}  # the reviewed languages


@dataclass(frozen=True)
class WeekendText:
    """Every piece of text the program itself writes into Weekend Picks."""
    subject: str                        # {weekend}
    heading: str                        # {weekend}
    intro: str
    intro_html: str
    free: str
    price_on_page: str
    place_unknown: str
    locality: str                       # {locality}
    ranking_failed: str
    calendar_note: str
    translation_failed: str             # at the top of the original, sent to a Recipient whose translation failed
    when: str                           # an event's start: {weekday} {day} {month} {hour} {minute}


WEEKEND_TEXT: dict[Language, WeekendText] = {  # the reviewed languages
    "en": WeekendText(
        subject="Weekend Picks · {weekend}",
        heading="🎪 Weekend Picks ({weekend})",
        intro="Every pick is in your calendar as a tentative event. **Delete the ones you don't want**: "
              "next week's picks learn from what you kept and what you deleted.",
        intro_html="Every pick is in your calendar as a <b>tentative</b> event. <b>Delete the ones you "
                   "don't want</b>: next week's picks learn from what you kept and what you deleted.",
        free="Free",
        price_on_page="Price on the event page",
        place_unknown="Place unknown",
        locality=" ({locality})",
        ranking_failed="(Ranking failed, so these are the first few in order)",
        calendar_note="[From Parent Recap Weekend Picks. Delete it if you don't want it; "
                      "next week's picks learn from what you delete.]",
        translation_failed="⚠️ This week's Weekend Picks couldn't be translated, so here they are as they were written.",
        when="{weekday} {month:02}/{day:02} {hour:02}:{minute:02}",
    ),
    "zh": WeekendText(
        subject="周末活动推荐 · {weekend}",
        heading="🎪 周末活动推荐（{weekend}）",
        intro="所有条目已作为 tentative 事件加入日历。**不感兴趣的直接删掉**——下周会根据你保留了哪些、删了哪些来学习偏好。",
        intro_html="所有条目已作为 <b>tentative</b> 事件加入日历。<b>不感兴趣的直接删掉</b>——下周会根据你保留了哪些、删了哪些来学习偏好。",
        free="免费",
        price_on_page="价格见页",
        place_unknown="地点未知",
        locality="（{locality}）",
        ranking_failed="（LLM 排序失败，按顺序展示前几条）",
        calendar_note="[本条来自 Parent Recap 周末活动推荐 — 不想要请直接删除，删除信号会用于下周学习偏好]",
        translation_failed="⚠️ 本周的周末活动推荐没能翻译成功，下面是原文。",
        when="{weekday} {month:02}/{day:02} {hour:02}:{minute:02}",
    ),
    "fi": WeekendText(
        subject="Viikonlopun vinkit · {weekend}",
        heading="🎪 Viikonlopun vinkit ({weekend})",
        intro="Jokainen vinkki on kalenterissasi alustavana tapahtumana. **Poista ne, jotka eivät kiinnosta**: "
              "ensi viikon vinkit valitaan sen perusteella, mitkä säilytit ja mitkä poistit.",
        intro_html="Jokainen vinkki on kalenterissasi <b>alustavana</b> tapahtumana. <b>Poista ne, jotka eivät "
                   "kiinnosta</b>: ensi viikon vinkit valitaan sen perusteella, mitkä säilytit ja mitkä poistit.",
        free="Maksuton",
        price_on_page="Hinta tapahtuman sivulla",
        place_unknown="Paikka ei tiedossa",
        locality=" ({locality})",
        ranking_failed="(Vinkkien lajittelu epäonnistui, joten tässä ovat ensimmäiset vinkit alkuperäisessä järjestyksessä)",
        calendar_note="[Parent Recapin viikonlopun vinkki. Poista tapahtuma, jos se ei kiinnosta – "
                      "poistosi huomioidaan ensi viikon vinkeissä.]",
        translation_failed="⚠️ Tämän viikon vinkkejä ei saatu käännettyä, joten tässä ne ovat alkuperäisellä kielellä.",
        when="{weekday} {day}.{month}. klo {hour}.{minute:02}",
    ),
}
