# Changelog

What changes for families in each release, collected from the `What changes for families:` line of every PR merged since the previous release (see [Reviews](CONTRIBUTING.md#reviews)).

## Unreleased

Parent Recap replaces FamilyBrief in text, folders and jobs (ADR 0010).

### Upgrading
- Parent Recap now installs its own Python into `~/ParentRecap/runtime` and no longer needs Homebrew's, so a `brew upgrade` can't stop the evening Brief any more (ADR 0011). The install rebuilds the program's Python packages on it. Since the Python is new, macOS no longer lets Parent Recap read WhatsApp: if it reads your WhatsApp groups, add the Python path the install prints to System Settings → Privacy & Security → App Management once more.
- Parent Recap also installs its own Node, and setup installs the Wilma program into `~/ParentRecap/wilma` and runs it on that Node, so Wilma works on a Mac without Node and you don't need Homebrew any more (ADR 0011). Setup installs the Wilma program again the first time you connect Wilma; a Node or Wilma program the Mac already has isn't used or touched. The install downloads about 230 MB.
- This version does not move an earlier install. If `~/FamilyBrief` holds a program, or the jobs `com.family.brief` or `com.family.weekend-events` are loaded, `get.sh` and `install.sh` stop and say so: run `~/FamilyBrief/app/.venv/bin/family-brief uninstall` first, then install again. An archive you kept in `~/FamilyBrief` doesn't block the install.

### Changed
- The program lives in `~/ParentRecap`, the jobs are `com.parentrecap.daily` and `com.parentrecap.weekend-events`, and the variables are `PARENT_RECAP_HOME` and `PARENT_RECAP_BG`.
- The command is `parent-recap`; `family-brief` still works. The Brief's own text, hints, logs and docs say Parent Recap and `~/ParentRecap`.
- Unchanged: the Keychain service, the Google Calendar event properties, the `.ics` UID and `~/.family`.
- When WhatsApp can't be read, the Brief says Parent Recap's Python may have changed, and `doctor` says when the Python macOS allowed is no longer the evening job's and prints the path to allow.

## 0.5.4 · 2026-10-06

The pilot feedback Form ships with the program.

### Setup
- Setup asks whether you're a pilot family. If you say yes, the evening Brief has ⭐ / ❌ links that open the pilot feedback Form already filled in, so you only press Submit.

## 0.5.3 · 2026-10-05

Fixes from setting up on Xiao xi's Mac mini.

### The evening Brief
- The evening Brief reads the Claude token and the Gmail App Password without a hidden Keychain popup. If doctor says macOS asks for the Keychain password, storing the token or App Password again once fixes it.
- Setup and the evening Brief find Claude Code, Codex, Node and the Wilma CLI even when macOS's own Terminal doesn't have them on its PATH, as with fish, nix or a custom npm folder.

### Setup
- The setup page finishes with "All done" when only warnings are left, and stops you if the evening Brief can't sign in to Claude.
- A "Check again" button on the last step runs the checks again after you fix something, without turning anything on again.
- Run inside tmux or over SSH, setup warns at the start that passwords can't be saved there, and if saving fails it says to run it again in a plain Terminal window, instead of asking you to click Allow on a prompt that never comes.
- Saying yes to pilot feedback will set it up fully, with nothing to paste, once the pilot Form ships with the program. Until then setup doesn't ask.

## 0.5.2 · 2026-10-05

Setup page fixes from the first web setup on an Intel Mac.

### Setup
- The setup page's last step says which check failed and what to do, and always ends: with "All done", or with "Finish for now" and how to come back later. The Terminal window then says it can be closed.
- After each Source is connected, the page says which Source comes next and moves to it.
- The page explains that a sleeping Mac wakes for the evening Brief, but a Mac that's shut down makes no Brief, so leave it asleep or locked in the evening.

## 0.5.1 · 2026-10-05

Install and uninstall fixes from the first tries on an Intel Mac and a Mac mini, and retries when the AI is busy.

### Install and uninstall
- The install line installs only ready-made packages, at the versions we tested, so it works on Intel Macs again and never tries to compile anything on your Mac. Apple Silicon Macs are recommended, and Intel Macs work for now on a best-effort basis.
- If the install can't set up its Python packages, you see a short message saying where its log is and what to do next, instead of pages of build output. Every install keeps a dated log in `~/FamilyBrief/logs`.
- The install leaves out Google's packages, which only Google Calendar uses. They're installed when you turn Google Calendar on, and on every update while you use it.
- Uninstall also removes the wilma CLI, the Wilma sign-in with its password, and the Claude Code plugin when setup installed them, and leaves the ones you had before setup.

### The Brief
- When Claude or ChatGPT is too busy in the evening, Parent Recap tries again a few minutes later instead of sending a Brief without its Digest.

## 0.5.0 · 2026-10-04

Setup moves into a page in your browser (#84).

### Install and setup
- Every family installs with the same line in Terminal. It opens a setup page in your browser, in Suomi, English or 中文, and you click through it instead of chatting. Setup in Claude Code or Codex is still there if you prefer it.
- Welcome asks a few choices with defaults: Claude or ChatGPT, whether your partner gets the Brief too, and pilot feedback. It checks that Claude Code or Codex is installed and signed in.
- You connect your Sources from one list and can skip the ones you don't need:
  - Wilma: find your town in a search box and sign in on the page, where your password manager can fill in the login. A Terminal window is offered only if that doesn't work.
  - Gmail: paste the App Password into the page.
  - Claude: click Authorize in your browser, with nothing to copy. ChatGPT: sign in to Codex from a button.
  - WhatsApp: pictures show what to switch on in System Settings, and the step ticks itself once it works.
  - MyClub: paste each Kid's calendar link.
- One page to confirm what was found: your Kids and the name the Brief calls each of them, the WhatsApp groups and Gmail senders to read (public mail services are never offered), each Recipient's language and the evening time.
- You see your first Brief in the page as it will look in your email, and send it to yourself only. Pilot families can tell us right there when something's wrong.
- One button turns on the evening Brief and the Mac's wake-up, with your Mac password typed only into macOS's own window. A checklist then shows what's set up, and the page closes itself.
- If you get stuck on a step, "Continue in the chat" opens Claude, or tells you what to type in Codex, and setup carries on from the same step.
- Setup in the chat checks your answers before saving them and remembers how far you got.

Passwords typed into the page go only to Parent Recap on your Mac. The page can be opened only from your own Mac, and it stops when setup is done or after 30 minutes without use.

## 0.4.3 · 2026-10-03

Fixes from the first setup rehearsal on a fresh Mac (#56).

### Install and setup
- Setup asks one thing at a time, mostly as choices you tap, and no longer asks for your Kids' school or class.
- The Wilma sign-in window explains in your language what to type, and closes Wilma by itself once you're signed in, so you no longer see the student picker or an error.
- Setup no longer asks about Google Calendar, so new events always come as an attachment on the Brief. The assistant never searches your folders or opens your files.
- If Claude Code isn't installed, setup gives Claude Code's own installer, which keeps it up to date by itself, and explains the "Auto-update failed" notice if you see it.
- If you installed from the zip, pasting the Claude Code line now moves you to the latest release instead of keeping the old version.

## 0.4.2 · 2026-10-02

A new setup: fewer questions, one list of things to connect, and no Terminal windows for passwords (#43).

### Install and setup
- Install by pasting one line into Terminal, one for Claude Code and one for Codex. Pasting it again later updates Parent Recap.
- Setup asks fewer questions. You check a card of defaults, connect each Source from one list (WhatsApp and MyClub can be skipped and added later), confirm one page of what was found, and see a preview of your first Brief before it's sent.
- Passwords and links go into macOS dialogs with a lock icon, never the chat: the Gmail App Password, the Claude token and each Kid's MyClub calendar link. Setup checks each one before saving it.
- Claude's sign-in opens in Terminal for you, so you only copy the token into the dialog, without typing commands.
- Setup signs you in to Wilma, reads your Kids from it and takes your city from the Wilma address.
- Setup sets the Mac's nightly wake-up itself, with your Mac password entered in macOS's own dialog.
- Setup ends with one check that the program is installed, the health check is all OK, the first Brief reached everyone, the nightly job is on and the Mac wakes for it.
- Weekend Picks are no longer asked in setup. Turn them on any time by asking.

### Uninstall
- Remove Parent Recap by telling Claude or Codex "uninstall Parent Recap". It lists everything first, asks whether to keep your past Briefs, and leaves your accounts as they are.

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
