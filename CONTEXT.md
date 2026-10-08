# Parent Recap

A tool that reads a household's school, club and parent-group messages each evening and sends the parents one summary of what needs attention. Terms are canonical in English; the Chinese (zh) and Finnish (fi) names in parentheses are the ones to use in a Brief in that language or when talking to a user in it.

Families know the product as Kinlace Parent Recap: the plugin is `parent-recap`, and the email subject and the top of each Brief say "Parent Recap". The command is `parent-recap`, and `family-brief` still works. The Python package `family_brief`, the Keychain service `family-brief`, the Google Calendar properties, the `.ics` UID suffix and PRODID, the `~/.family` folder and the terms below keep their names (ADR 0010).

## Output

**Brief** (zh 日报, fi kooste):
The daily message sent to a Household, containing the Digest, Notices, Action Items and new calendar events. There is one Brief per night; each Recipient receives it in their own language, with the same content.
_Avoid_: report, newsletter, daily digest, 家庭简报

**Digest** (zh 摘要, fi yhteenveto):
The short prose summary at the top of a Brief, grouped by Kid.
_Avoid_: summary, message digest, 总结

**Notice** (zh 注意事项, fi tiedoksi):
Something a parent should know, with no action required.
_Avoid_: alert, info, 通知

**Action Item** (zh 待办, fi hoidettava asia):
Something a parent must do, with an absolute due date and an optional assignee. An event or schedule change is not an Action Item by itself, since getting the Kid there is implied; only something beyond showing up is (bring, pay, sign, reply, or arrange something unusual).
_Avoid_: task, todo, 家长要做的事

**Weekend Picks** (zh 周末活动推荐, fi viikonlopun vinkit):
The separate Friday message recommending local family events for the weekend; not part of the Brief.
_Avoid_: weekend events, weekend brief

**Held-back Message** (zh 未交给 AI 的消息, fi tekoälyltä piilotettu viesti):
A message that looked sensitive, such as health, special support or bullying, and was not sent to the AI. The Brief lists it by Source, sender and subject so the Recipient reads it where it came from (ADR 0013).
_Avoid_: filtered message, hidden message, 敏感消息

## Inputs

**Source** (zh 信息源, fi lähde):
A place messages or events are read from for a Household, such as Gmail, Wilma, WhatsApp or MyClub.
_Avoid_: collector, channel, feed, 采集器, 来源

**AI filter** (zh AI 过滤, fi tekoälysuodatin):
The step on the Mac that every AI call of the evening Brief goes through: phone numbers, email addresses and links become placeholders, which the program turns back into the real values before the Brief goes out (ADR 0013). It is on by default, and a parent can turn it off with `manage`.
_Avoid_: anonymizer, redaction, masking (in user-facing text), 脱敏

## People

**Household** (zh 家庭, fi perhe):
The unit that receives a Brief: its Kids, its Recipients and their configured Sources.
_Avoid_: family, user, account

**Recipient** (zh 收件人, fi vastaanottaja):
A person who receives the Brief or Weekend Picks for a Household. Each Recipient has their own language, used for everything Parent Recap sends or says to them.
_Avoid_: reader, subscriber, user, 用户

**Kid** (zh 孩子, fi lapsi):
A child in the Household whom messages are attributed to. A Brief calls each Kid by one everyday name everywhere, even where a Source uses their full name.
_Avoid_: child, student

**Third Party** (zh 第三方, fi ulkopuolinen henkilö):
Anyone named in a Household's messages who is not one of its Kids or Recipients, such as teachers, other pupils and other parents. The AI sees Third Parties only as placeholders (ADR 0013).
_Avoid_: others, outsiders, 别人
