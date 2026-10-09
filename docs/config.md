# Configuration

For a full example see `app/config.example.yaml`. The config file lives at `~/.family/config.yaml`; after writing it, run `chmod 600 ~/.family/config.yaml`. Changes need no restart; they take effect on the next run.

## City presets

| City                                 | Wilma address          | Starting Gmail allowlist | Weekend Picks regions   |
| ------------------------------------ | ---------------------- | ------------------------ | ----------------------- |
| Espoo                                | espoo.inschool.fi      | `espoo.fi`               | Espoo, Helsinki, Vantaa |
| Helsinki (city schools)              | helsinki.inschool.fi   | `hel.fi`                 | Helsinki, Espoo, Vantaa |
| Helsinki (private and state schools) | yvkoulut.inschool.fi   | The school's own domain  | Helsinki, Espoo, Vantaa |
| Vantaa                               | vantaa.inschool.fi     | `vantaa.fi`              | Vantaa, Helsinki, Espoo |
| Kauniainen                           | kauniainen.inschool.fi | `kauniainen.fi`          | Espoo, Helsinki         |

With Wilma, `parent-recap setup wilma` takes the city from the Wilma address the family signs in to, using this table, and gives no city for an address that isn't in it. When you add a city here, add its Wilma address to `WILMA_CITIES` and its starting allowlist to `CITY_DOMAINS` in `app/src/family_brief/setup_steps.py` too; a test checks they match.

The allowlist is only a starting point. Always run `parent-recap discover gmail-senders` to add the domains the Kids actually get mail from: music schools (such as `emo.fi` in Espoo), sports clubs, hobby classes. Gmail's `from:espoo.fi` also matches subdomains such as `edu.espoo.fi`.

Weekend Picks come from the event database of Helsinki, Espoo and Vantaa (Linked Events), so manage turns them on only for families in those three cities and in Kauniainen. Setup doesn't ask about them. Kauniainen sits inside Espoo, and its families go to Espoo and Helsinki events.

### Cities without a preset

Parent Recap works in any city whose schools use Wilma; only the starting values above have to be found by hand:

- **Wilma address**: the address the browser shows when the family signs in to Wilma on the web, usually `<city>.inschool.fi`. Searching for "<city> Wilma" finds the sign-in page. In the `wilma` sign-in screen, choose that city or school.
- **Starting Gmail allowlist**: the domain after the @ in the addresses the school and the teachers write from, often the city's own domain such as `<city>.fi`. Take it from a school email the family already has. With none at hand, leave the allowlist empty and pick the domains from `parent-recap discover gmail-senders` in the Gmail step.
- **Weekend Picks**: not offered.

### Tested cities

One town outside the presets was set up end to end in a fresh-install test in October 2026: Wilma worked with the town's own Wilma address, and the town's domain served as the starting allowlist. The presets themselves come from each city's Wilma address and mail domain. If you run Parent Recap in a city not listed here, [open an issue](https://github.com/kinlace/parent-recap/issues) to say how it went, so it can be added.

## Key fields

| Field                                  | Meaning                                                                                                                         |
| -------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| `summary_language`                     | Language code of Recipients who don't pick their own: `en` (the default), `zh`, `sv` or others                                  |
| `city`                                 | The Household's town in Finnish, such as `Espoo`, picked from Wilma's town list in setup, with or without Wilma                 |
| `kids[].name`                          | The Kid's full name, ideally spelled as in Wilma; the model uses it to tell which Kid a message is about                        |
| `kids[].everyday_name`                 | What the family calls the Kid; the Brief uses it everywhere (Digest, Action Items, calendar events). Leave it out to use `name`  |
| `kids[].aliases`                       | Other names used in chats and mail: nicknames, English name, Chinese name                                                       |
| `kids[].grade` / `class_name`          | Optional; setup fills them in only from Wilma. Update them every August when the school year changes                           |
| `kids[].activities`                    | Hobby classes and clubs, to help the model understand the group chats                                                           |
| `kids[].myclub_ical_url`               | MyClub calendar subscription link                                                                                               |
| `gmail.allowlist_domains`              | **Only these domains are scanned**; without an allowlist the program refuses to run                                             |
| `gmail.allowlist_senders`              | Individual teachers or coaches who use a personal address                                                                       |
| `wilma.enabled`                        | Set to true only after signing in to Wilma with `parent-recap setup wilma`                                                      |
| `whatsapp.chats[]`                     | Copy `name` exactly; `kid` is the Kid's full name or `both`; `label` is the kind of group                                       |
| `google_calendar.mode`                 | `ics` / `google` / `off`                                                                                                        |
| `google_calendar.invite_attendees`     | In google mode, people automatically invited to new events                                                                      |
| `llm.backend`                          | `claude` (Claude Pro/Max subscription) or `codex` (ChatGPT account); `cli` in older configs means `claude`                      |
| `llm.codex_path`                       | Optional, where codex is. By default the one bundled in Codex.app or ChatGPT.app is used first (ChatGPT.app keeps it in `Contents/Resources/codex-cli/bin/codex`)                                  |
| `llm.timeout_seconds`                  | Timeout for one model call, 300 seconds by default; raise it to 600 if there are a lot of messages, for example after a holiday |
| `ai_filter.enabled`                    | `true` by default: the AI gets placeholders for other people's names, phone numbers, email addresses and links, and nothing of messages that look sensitive. See [What the AI sees](#what-the-ai-sees) |
| `email.to`                             | Brief recipients; see [Recipients in their own language](#recipients-in-their-own-language) below                               |
| `email.weekend_to`                     | Weekend Picks recipients (`email.to` when empty); each can have its own `language` too                                          |
| `weekend_events.kid_preferences`       | Free text describing each Kid's interests                                                                                       |
| `feedback`                             | Optional, pilot families only; see [Pilot feedback](#pilot-feedback) below                                                      |
| `schedule.daily_hour` / `daily_minute` | When the Brief runs; after changing it, run `parent-recap schedule install` again                                               |

## Recipients in their own language

Each entry in `email.to` is an address, which reads the Brief in `summary_language`, or an address with its own `language`, given as a two- or three-letter language code such as `en`, `zh`, `sv` or `fi` (without a region such as `-GB`):

```yaml
email:
  to:
    - you@gmail.com
    - address: partner@gmail.com
      language: zh
```

The model writes the Brief once, in the first Recipient's language, and one more model call translates it for each other language among the Recipients; Recipients who share a language share that translation and its email. So a Household with two languages makes one extra model call per night, and one with a single language makes none. Each language gets its own email, with the calendar attachment in that language too.

What the Household shares stays in the first Recipient's language: the events written to Google Calendar, the archive in `~/ParentRecap`, and the text the pilot feedback links send. On a night the translation fails, that Recipient gets the Brief in the first Recipient's language instead, with a line at the top saying so. A translation that leaves out a Notice, an Action Item or a calendar event, or changes a due date, an event's time, which Kid an item is about or the message it points to, counts as failed, so both parents always get the same items with the same dates. iMessage also sends the Brief in the first Recipient's language.

Weekend Picks work the same way: they are written once, in the language of the first Recipient in `email.weekend_to` (or `email.to` when that is empty), and translated for each other language among their Recipients, with the same fallback to the original and a note when a translation fails or leaves out or reorders a pick. The calendar events, the archive and Weekend Picks sent by iMessage keep the original.

### Reviewed and best-effort languages

`en` (English), `zh` (Chinese) and `fi` (Finnish) are reviewed: we check the program's own text in the Brief (headings, the coverage line, calendar hints, the footer), and they have golden Briefs and eval cases. Any other language is best effort. The first time it's needed, the model translates the program's own text into it once; the result is kept in the `languages` folder next to the state file (for example `~/.family/languages/sv.json`) and used every night after that. `parent-recap language sv` does this ahead of time. If that translation fails, the Brief's own text is in English that night and it's tried again the next time; `parent-recap doctor` shows which languages have their own text yet. Weekend Picks' own text is translated the same way, once, by the same command or on the first Friday that needs it.

## What the AI sees

Every evening the night's messages go to the Household's own Claude or ChatGPT, which writes the Brief. With `ai_filter.enabled: true`, the default, the program first replaces each phone number, email address and link in them with a placeholder such as ⟦P1⟧, ⟦E1⟧ or ⟦L1⟧, and leaves out what the AI doesn't need: the link back to each Gmail message and the Kid's Wilma student number. Other people's names become placeholders such as ⟦N1⟧ too, in their Finnish case forms (Maijalle) and inside Chinese text as well. The program takes these names from the senders of that evening's messages and of the earlier Briefs' nights: Wilma senders, the name in an email's sender, and the people who post in the chosen WhatsApp groups. Calendar attendees aren't among them: the program doesn't read them. It never takes names from a list of names, so a person named only inside a message still reaches the AI. A role in a sender stays (`Opettaja Virtanen` becomes `Opettaja ⟦N1⟧`), an organisation beside a name stays too (`Maija Virtanen, Kilon koulu`), and an organisation alone or a shared mailbox such as info@ names no one. A Kid's name in a Finnish case form (Leon for Leo) is never masked, even when it is also someone's name. Each name comes back as the message wrote it. The Kids' names and aliases, their school and class, and the Recipients' own names in emails they send still reach the AI. The translation for each Recipient gets the same placeholders. The program keeps which value each placeholder stands for in memory on the Mac, for that run only, and puts the real values back before the Brief, the `.ics` file, Google Calendar and the archive are written, so the Brief looks the same. If the AI changed a placeholder so it can't be put back, the Brief says "a phone number", "an email address", "a link" or "someone" in its place, in the Recipient's language, and keeps the item.

A message that looks sensitive doesn't reach the AI at all: one about a diagnosis, a meeting with a psychologist or school social worker, special or intensified support, bullying, harassment, violence, child welfare or the police, also when it is about your own Kids. The program checks each message's subject, body and sender on the Mac against word lists in Finnish, Swedish, English and Chinese (`app/src/family_brief/sensitive_words.yaml`), and lists each such message in the Brief by its Source, sender and subject, with a link to read it in Gmail or Wilma, or a note to read it in WhatsApp. The Brief doesn't summarize it, and it is kept in the archive like the others. Routine school messages still go to the AI, so their Action Items aren't lost: head lice, chickenpox, a sick day, the school nurse's check-ups, a camp notice that asks about medication, a diagnostic test in maths, the school subject psychology, the guidance counsellor's course choices, and a newsletter whose footer lists the school psychologist or social worker with a phone number, an email address or a link. The lists only look for words, so they can't tell a notice to the whole school from a message about one child. So a message sent to everyone is never held back, whatever its words: a Wilma announcement, the announcements in Wilma's notification emails read through Gmail ("Viesti Wilmasta", "Message from Wilma"), and an email sent to a mailing list, such as the city's mass email, which has a `List-Id` or `List-Unsubscribe` header or `Precedence: bulk`. Other people's names in them still become placeholders. Messages to your Household are still checked: a Wilma message, also one a teacher sends to every guardian in the class, since the wilma CLI doesn't say who a message went to, the rest of a Wilma notification email, such as a message it copies, and the posts in your WhatsApp groups.

Two more limits apply with the AI filter on or off. In `google` calendar mode the Brief's model call also gets the calendar's events of the next `google_calendar.lookahead_days_context` days (7 by default), so it leaves out events already there and notices clashes. Only Kid-related events keep their title and location: those Parent Recap added, the MyClub ones among them, and those whose title names a Kid or one of their aliases. Any other event, such as a parent's own appointment, goes as a busy block with its start and end only. The `ics` and `off` modes send none. Weekend Picks are ranked with each Kid as Kid A, Kid B: the Kids' names and aliases in the preferences, the activities and last week's feedback become the same placeholders, and the email, the calendar events and the archive show each Kid's everyday name again.

This reduces what reaches the AI. It doesn't hide everything: names the program can't list and the rest of the text still reach it ([ADR 0013](adr/0013-the-ai-gets-placeholders-for-third-parties-and-nothing-of-sensitive-messages.md)). Claude and ChatGPT process what they get in the US, and by default may use it to train their models. Parent Recap can't check that setting: the setup page and the README's [What the AI sees](../README.md#what-the-ai-sees) say where to turn it off.

To check what the filter catches in your own messages, run `parent-recap ai-filter-report`. It reads every evening in the archive in `~/ParentRecap` and runs the evening Brief's filter on that evening's messages, with that evening's own list of names. It prints counts only: the messages it holds back, by category, the names, phone numbers, email addresses and links that become placeholders, and an estimate of the names it likely missed. It counts messages sent to everyone as the evening Brief does, except an email archived before Parent Recap recorded a mailing list's headers, which it checks like any other. A likely miss is a capitalised word or a Chinese name next to a role or a title (opettaja, teacher, 老师, -n äiti, 妈妈), or right after a name's placeholder, that wasn't on that evening's list. The report prints no message text, no names and no links, and it makes no network call. With the AI filter off, it shows what the filter would do. These counts decide whether a local name-recognition model is worth adding (ADR 0013).

To see which messages it holds back, run `parent-recap ai-filter-report --held-back` yourself in the macOS Terminal app, not with `!` in Claude Code or in the Claude app's Terminal panel, which the AI assistant can read. After the counts, it lists each message the filter holds back, by date, one line each: the date, the Source, the category, the word that matched as the message writes it, the sender, the subject (for WhatsApp, the chat's name) and about 40 characters of the text on each side of the word. A held-back message loses its summary and Action Items, so the list shows which words hold back routine notices, such as a teacher's message to every guardian in the class. It holds the messages' own text, so it prints only to a terminal no AI assistant runs. Piped into another program or captured, as when Claude or Codex runs the command, or in a shell Claude Code or Codex started, such as with `!` in Claude Code (they set `CLAUDECODE`, `AI_AGENT` or Codex's `CODEX_THREAD_ID`), the report prints the counts alone and a line saying to run it in the Terminal app: Claude and Codex never get the list and give you the command instead. As with the counts, nothing leaves the Mac and nothing is saved. With the AI filter off, it lists the messages the filter would hold back.

To turn the AI filter off, set `ai_filter.enabled: false`, or ask Claude or Codex to turn off the AI filter. The AI then gets the messages as they are, sensitive ones included, from the next Brief on. The calendar's busy blocks and Weekend Picks' Kid A and Kid B stay.

## Pilot feedback

For pilot families only. When turned on, the emailed Brief (HTML version) shows a ⭐ and a ❌ link next to every Action Item, and a ❌ link under the Digest. Their labels follow the Brief's language (`⭐ Glad this was here`, `❌ This is wrong`, `❌ The Digest has a mistake`, or `⭐ 幸好有这条`, `❌ 这条错了`, `❌ 摘要里有错` in a Chinese Brief); the form itself is in English and records the English option for every Household. iMessage has no such links.

**Before turning it on, the parents must know and agree that:**

- Clicking ⭐ or ❌ opens a pre-filled Google Form. On submit, the item's text, its Source (Gmail / Wilma / WhatsApp / MyClub), the AI that made the Brief (Claude or Codex), the date, the Household's pseudonym and the Kid as Kid A or Kid B are sent to the Parent Recap team and stored in the team's Google Sheet. The Digest's ❌ sends the Digest (shortened if too long), without Source or Kid. The form also has an optional comment field.
- The text goes as the AI saw it: other people's names, phone numbers, email addresses and links are placeholders such as ⟦N1⟧ and ⟦P1⟧ (see [What the AI sees](#what-the-ai-sees)), and each Kid is Kid A, Kid B in the order of `kids`. This holds with the AI filter off too, since the links go to the team, not the AI. As with the AI, a name the program can't list and the Recipients' own names still go as written.
- Just opening the link without submitting still leaves the item's text and the Household's pseudonym in the browser history and Google's access logs, because they are part of the link.
- Only the item clicked is sent; nothing is sent if nothing is clicked. The rest of the Brief, and the original mail and chat messages, are never sent.
- It can be turned off at any time (`enabled: false`); the links then disappear from the Brief.

**How to configure it:** the program ships the pilot form's details (the team creates the form with the script in `ops/feedback-form`), so nothing is pasted. Turn it on with `parent-recap setup save <<< '{"feedback": {"enabled": true}}'`, or by answering yes on the setup page's Welcome. That writes the whole section: `prefill_base_url` and `fields` from the program, and a `household_label`, which starts as the setup parent's email user (`virtanen.home` for `virtanen.home@gmail.com`). The label stays on the Mac: the form gets a pseudonym made from it, such as `Household 3f9a2c`, the same every evening, so the team can group a Household's feedback without learning the label. Change the label, for example to `"Virtanen family"`, on the setup page's check step or with `parent-recap setup save <<< '{"feedback": {"household_label": "Virtanen family"}}'`. The team then sees the Household's later feedback under a new pseudonym. Don't change `prefill_base_url` or any `entry.` number in `fields`, or the form won't be pre-filled. A version that ships no pilot form doesn't ask, and refuses to turn it on. Without this section, or with `enabled: false`, there are no links.

Afterwards run `parent-recap doctor`: a ⚠️ on the pilot feedback line means a field is missing or `household_label` is empty. It doesn't stop the Brief or setup from finishing. `run --dry-run` doesn't build the HTML version, so it doesn't show the links; only the next real Brief confirms that ⭐ / ❌ appear.

## New school year (every August)

1. Update each Kid's `grade` and `class_name`, if the config has them
2. Class groups are often replaced or renamed: run `parent-recap bg discover whatsapp-chats` and replace the old group names in `whatsapp.chats` with the new ones
3. Music school groups named after the school year (such as `EMO-PIANO 2025-2026`) also get the new year's name
4. Run `parent-recap doctor` once to confirm every group is found
