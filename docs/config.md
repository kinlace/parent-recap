# Configuration

For a full example see `app/config.example.yaml`. The config file lives at `~/.family/config.yaml`; after writing it, run `chmod 600 ~/.family/config.yaml`. Changes need no restart; they take effect on the next run.

## City presets

| City                                 | Wilma address          | Starting Gmail allowlist | Weekend Picks regions   |
| ------------------------------------ | ---------------------- | ------------------------ | ----------------------- |
| Espoo                                | espoo.inschool.fi      | `espoo.fi`               | Espoo, Helsinki, Vantaa |
| Helsinki (city schools)              | helsinki.inschool.fi   | `hel.fi`                 | Helsinki, Espoo, Vantaa |
| Helsinki (private and state schools) | yvkoulut.inschool.fi   | The school's own domain  | Helsinki, Espoo, Vantaa |
| Vantaa                               | vantaa.inschool.fi     | `vantaa.fi`              | Vantaa, Helsinki, Espoo |
| Kauniainen                           | kauniainen.inschool.fi | `kauniainen.fi`          | Not offered             |

The allowlist is only a starting point. Always run `family-brief discover gmail-senders` to add the domains the Kids actually get mail from: music schools (such as `emo.fi` in Espoo), sports clubs, hobby classes. Gmail's `from:espoo.fi` also matches subdomains such as `edu.espoo.fi`.

Weekend Picks come from the event database of Helsinki, Espoo and Vantaa (Linked Events), so setup offers them only to families in those three cities.

### Cities without a preset

Parent Recap works in any city whose schools use Wilma; only the starting values above have to be found by hand:

- **Wilma address**: the address the browser shows when the family signs in to Wilma on the web, usually `<city>.inschool.fi`. Searching for "<city> Wilma" finds the sign-in page. In the `wilma` sign-in screen, choose that city or school.
- **Starting Gmail allowlist**: the domain after the @ in the addresses the school and the teachers write from, often the city's own domain such as `<city>.fi`. Take it from a school email the family already has. With none at hand, leave the allowlist empty and pick the domains from `family-brief discover gmail-senders` in the Gmail step.
- **Weekend Picks**: not offered.

### Tested cities

One town outside the presets was set up end to end in a fresh-install test in October 2026: Wilma worked with the town's own Wilma address, and the town's domain served as the starting allowlist. The presets themselves come from each city's Wilma address and mail domain. If you run Parent Recap in a city not listed here, [open an issue](https://github.com/kinlace/parent-recap/issues) to say how it went, so it can be added.

## Key fields

| Field                                  | Meaning                                                                                                                         |
| -------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| `summary_language`                     | Language code of Recipients who don't pick their own: `en` (the default), `zh`, `sv` or others                                  |
| `kids[].name`                          | The Kid's full name, ideally spelled as in Wilma; the model uses it to tell which Kid a message is about                        |
| `kids[].everyday_name`                 | What the family calls the Kid; the Brief uses it everywhere (Digest, Action Items, calendar events). Leave it out to use `name`  |
| `kids[].aliases`                       | Other names used in chats and mail: nicknames, English name, Chinese name                                                       |
| `kids[].grade` / `class_name`          | Grade and class; update them every August when the school year changes                                                          |
| `kids[].activities`                    | Hobby classes and clubs, to help the model understand the group chats                                                           |
| `kids[].myclub_ical_url`               | MyClub calendar subscription link                                                                                               |
| `gmail.allowlist_domains`              | **Only these domains are scanned**; without an allowlist the program refuses to run                                             |
| `gmail.allowlist_senders`              | Individual teachers or coaches who use a personal address                                                                       |
| `wilma.enabled`                        | Set to true only after logging in to wilma in Terminal                                                                          |
| `whatsapp.chats[]`                     | Copy `name` exactly; `kid` is the Kid's full name or `both`; `label` is the kind of group                                       |
| `google_calendar.mode`                 | `ics` / `google` / `off`                                                                                                        |
| `google_calendar.invite_attendees`     | In google mode, people automatically invited to new events                                                                      |
| `llm.backend`                          | `claude` (Claude Pro/Max subscription) or `codex` (ChatGPT account); `cli` in older configs means `claude`                      |
| `llm.codex_path`                       | Optional, where codex is. By default the one bundled in Codex.app or ChatGPT.app is used first                                  |
| `llm.timeout_seconds`                  | Timeout for one model call, 300 seconds by default; raise it to 600 if there are a lot of messages, for example after a holiday |
| `email.to`                             | Brief recipients; see [Recipients in their own language](#recipients-in-their-own-language) below                               |
| `email.weekend_to`                     | Weekend Picks recipients (`email.to` when empty); each can have its own `language` too                                          |
| `weekend_events.kid_preferences`       | Free text describing each Kid's interests                                                                                       |
| `feedback`                             | Optional, pilot families only; see [Pilot feedback](#pilot-feedback) below                                                      |
| `schedule.daily_hour` / `daily_minute` | When the Brief runs; after changing it, run `family-brief schedule install` again                                               |

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

What the Household shares stays in the first Recipient's language: the events written to Google Calendar, the archive in `~/FamilyBrief`, and the text the pilot feedback links send. On a night the translation fails, that Recipient gets the Brief in the first Recipient's language instead, with a line at the top saying so. A translation that leaves out a Notice, an Action Item or a calendar event, or changes a due date, an event's time, which Kid an item is about or the message it points to, counts as failed, so both parents always get the same items with the same dates. iMessage also sends the Brief in the first Recipient's language.

Weekend Picks work the same way: they are written once, in the language of the first Recipient in `email.weekend_to` (or `email.to` when that is empty), and translated for each other language among their Recipients, with the same fallback to the original and a note when a translation fails or leaves out or reorders a pick. The calendar events, the archive and Weekend Picks sent by iMessage keep the original.

### Reviewed and best-effort languages

`en` (English), `zh` (Chinese) and `fi` (Finnish) are reviewed: we check the program's own text in the Brief (headings, the coverage line, calendar hints, the footer), and they have golden Briefs and eval cases. Any other language is best effort. The first time it's needed, the model translates the program's own text into it once; the result is kept in the `languages` folder next to the state file (for example `~/.family/languages/sv.json`) and used every night after that. `family-brief language sv` does this ahead of time. If that translation fails, the Brief's own text is in English that night and it's tried again the next time; `family-brief doctor` shows which languages have their own text yet. Weekend Picks' own text is translated the same way, once, by the same command or on the first Friday that needs it.

## Pilot feedback

For pilot families only. When turned on, the emailed Brief (HTML version) shows a ⭐ and a ❌ link next to every Action Item, and a ❌ link under the Digest. Their labels follow the Brief's language (`⭐ Glad this was here`, `❌ This is wrong`, `❌ The Digest has a mistake`, or `⭐ 幸好有这条`, `❌ 这条错了`, `❌ 摘要里有错` in a Chinese Brief); the form itself is in English and records the English option for every Household. iMessage has no such links.

**Before turning it on, the parents must know and agree that:**

- Clicking ⭐ or ❌ opens a pre-filled Google Form. On submit, the item's text, its Source (Gmail / Wilma / WhatsApp / MyClub), the AI that made the Brief (Claude or Codex), the date, the Household label and the Kid's name are sent to the FamilyBrief team and stored in the team's Google Sheet. The Digest's ❌ sends the Digest (shortened if too long), without Source or Kid. The form also has an optional comment field.
- Just opening the link without submitting still leaves the item's text, the Kid's name and the Household label in the browser history and Google's access logs, because they are part of the link.
- Only the item clicked is sent; nothing is sent if nothing is clicked. The rest of the Brief, and the original mail and chat messages, are never sent.
- It can be turned off at any time (`enabled: false`); the links then disappear from the Brief.

**How to configure it:** the FamilyBrief team generates the whole section with the script in `ops/feedback-form` and sends it to pilot families. Paste it into `config.yaml` as is and change only `household_label`. Set `household_label` to a name the team will recognize, such as `"Virtanen family"`; the feedback sheet uses it to tell Households apart. Don't change `prefill_base_url` or any `entry.` number in `fields`, or the form won't be pre-filled. Without this section, or with `enabled: false`, there are no links.

Afterwards run `family-brief doctor`: a ⚠️ on the pilot feedback line means a field is missing or `household_label` is empty. `run --dry-run` doesn't build the HTML version, so it doesn't show the links; only the next real Brief confirms that ⭐ / ❌ appear.

## New school year (every August)

1. Update each Kid's `grade` and `class_name`
2. Class groups are often replaced or renamed: run `family-brief bg discover whatsapp-chats` and replace the old group names in `whatsapp.chats` with the new ones
3. Music school groups named after the school year (such as `EMO-PIANO 2025-2026`) also get the new year's name
4. Run `family-brief doctor` once to confirm every group is found
