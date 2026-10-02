# Changelog

What changes for families in each release, collected from the `What changes for families:` line of every PR merged since the previous release (see [Reviews](CONTRIBUTING.md#reviews)).

## Unreleased

## 0.4.1 · 2026-10-02

Fixes from the first fresh-install rehearsal (#2).

### The Brief
- The emailed Brief shows each Kid's name as a heading with their points as a bulleted list, instead of raw `##`, `**` and `-` marks.
- Dates in the Digest, Notices and Action Items are written the way your language writes them, never as 2026-10-07.
- Each Kid is called by the same everyday name throughout the Brief. A household set up before 0.4.1 can ask the assistant to add each Kid's everyday name.
- The mail preview lists the Action Items instead of only counting them.
- The problem lines in the Brief, the notes on calendar events and Weekend Picks, and the calendar attachment's name say Parent Recap instead of FamilyBrief.

### Calendar
- An exam, outdoor day, shortened school day or other school event that a message gives a date for but no start time now arrives in the calendar as an all-day event.
- The calendar no longer gets an entry for an Action Item's due date, or for a regular training that just carries on, such as after a holiday.

### Setup
- Setup asks your city, where school mail arrives and about WhatsApp groups first, and fills in your Kids from Wilma instead of asking.
- Weekend Picks are offered only where they have events nearby: Helsinki, Espoo, Vantaa and Kauniainen.
- The Gmail sender scan shows its progress, the WhatsApp permission step is a single drag from Finder, and the wake-up command warns before it replaces a wake schedule the Mac already has.
- The setup guides say Parent Recap instead of FamilyBrief.

### Reliability
- When Gmail, Wilma or MyClub is slow to answer, Parent Recap tries once more instead of sending the Brief without it.

## 0.4.0 · 2026-10-01

The first public release, now called **Kinlace Parent Recap**.

### Install and setup
- The product is now called Kinlace Parent Recap. Install it with `/plugin install parent-recap@kinlace` from the public `kinlace/parent-recap` repository, or from the latest release zip, with no GitHub account. Start it with `/parent-recap:setup`. Families who installed an earlier version in Claude Code remove the old `family-brief` marketplace first (see the README).
- The README, setup docs, example config and installer messages are in English. Setup and manage reply in the language the parent writes in, and `doctor` and the setup scripts speak English.
- Setup asks each parent's language, and a parent can change theirs or add a Recipient later just by asking.
- Pilot families are told what the ⭐/❌ feedback links send and are asked for consent during setup. They can turn the links on or off later.
- A new household that picks Google Calendar mode is asked for a Google client file (the one we hand out, or their own) before authorizing. The default `.ics` attachments need nothing.
- The README and install zip say how data flows, where each password lives, and that Parent Recap isn't affiliated with the services it reads (SECURITY.md, MIT licence).

### Languages
- Each family chooses the Brief's language, and the whole Brief follows it, the program's own lines included. A config without `summary_language` now gets an English Brief. Configs copied from the old example already say `zh` and stay Chinese.
- Each parent can get the Brief in their own language, with the same content. The Brief is written once and translated, so a second language costs one extra model call a night.
- A parent never gets a translated Brief that quietly dropped an item or changed its date or Kid. On a night the translation goes wrong, they get the original with a short note at the top.
- A parent can pick any language, such as Swedish, and the whole Brief comes in it, headings and hints included. English, Chinese and Finnish are the reviewed languages. Others are best effort.
- Weekend Picks and the archive in `~/FamilyBrief` follow each Recipient's language too.
- Dates and times read the way people write them in each language ("Thu 1 Oct 09:00", "10月1日 周四 09:00", "to 1.10. klo 9.00").

### A more reliable nightly Brief
- If a night's Brief fails to send, the next night's Brief includes everything from the failed night.
- A hiccup while reading Gmail, Wilma or WhatsApp no longer makes messages disappear. They show up in the next Brief.
- On a night when no Source could be read, the parents get a short Brief that says which Sources failed and how to fix each one.
- An odd-shaped or cut-off reply from the AI no longer crashes the night or quietly drops items. A Brief that may be incomplete says so.
- MyClub match and training times show Helsinki time. A night that wasn't delivered no longer creates duplicate Google Calendar events.
- Events already written to Google Calendar before a failure partway through the night are still listed in the Brief.
- The Brief no longer adds "drop off / pick up" to-dos for events and schedule changes it already lists.
- One odd pick from the AI no longer stops the Friday Weekend Picks email or mixes up next week's kept and deleted picks.
- A setup preview that gets cut off no longer leaves a hidden job running, and two runs can't happen at once.

### Safety and privacy
- A message in a parent group or an email can no longer get Parent Recap to add a made-up event or link to the family calendar. Calendar events must cite a real message, and the model is told that message text is data, not instructions.
- The AI call can't run commands or read other files on the parents' Mac. The prompt goes over stdin, Claude sessions aren't saved, and the Claude token stays off the command line.
- Other accounts on the family Mac can't read the archive or logs: `~/FamilyBrief` and everything Parent Recap writes is owner-only. The MyClub calendar link stays out of logs and chat, and parents add it in their own Terminal.

### For operators
- The pilot feedback form is in English for every Household. Re-run `ops/feedback-form/create_feedback_form.gs` and hand out the new `feedback` section, since links from this version pre-fill the English options.
- Releases are tagged builds: pushing `vX.Y.Z` runs the tests on macOS, publishes the zip as a GitHub Release and moves `stable`.
