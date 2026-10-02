---
name: setup
description: Install and configure Parent Recap, which every evening reads the kids' school mail, Wilma, WhatsApp parent groups and MyClub club calendar, has Claude or ChatGPT (Codex) write a Brief sent to each parent's inbox in their own language, and optionally sends Weekend Picks every Friday. Use when the user says "install / set up parent recap", "install / set up family brief", "parent recap onboarding", "安装 / 设置 family brief", "家庭日报 onboarding", or has just installed this plugin and wants to start. macOS only.
---

# Parent Recap setup wizard

You are helping a parent install Parent Recap on their own Mac. The user is often a Chinese family in the Helsinki region. Go one step at a time: finish and verify each step before starting the next. The whole thing takes about 30–45 minutes; tell the user before you start.

**Run setup in the user's language**, not the language of this skill. Guess it from how they wrote (if they haven't written anything yet, open in English), confirm it as the first thing in step 0, and use it for the rest of setup. If they switch languages later, follow them in the chat; that doesn't change the language of their Brief. In Chinese, call the Brief 日报 and Weekend Picks 周末活动推荐; in Finnish, kooste and viikonlopun vinkit; in English or any other language, say Brief and Weekend Picks. Commands, config keys and file paths stay as written.

## Rules

1. **Never let the user send a password, App Password or token in the chat.** For steps that need a secret, give the user a command to run in their own Terminal app; the script hides the input and stores it in the macOS Keychain. If the user pastes a secret into the chat anyway, remind them to revoke it and generate a new one afterwards. A MyClub calendar link counts as a token: the user saves it into the config with `scripts/setup_myclub.py` (step 8). Once a Kid has one, don't open or print the whole config; view it with `grep -v myclub_ical_url ~/.family/config.yaml`, and make changes with commands that don't print the file.
2. **Don't print mail or chat text into the chat.** Check connections only with `$FB doctor` (it shows only status and counts). Don't run `$FB collect`; it prints message text.
3. **Don't overwrite an existing config.** If `~/.family/config.yaml` already exists, this Mac has been set up before: stop and use `/parent-recap:manage` instead. If the user insists on reinstalling, back it up first as `config.yaml.bak-<date>`.
4. Commands that need `sudo`: only show them, and let the user run them.
5. For anything the user has to do in a web page or System Settings, give the exact click path, then wait for the user to say it's done before continuing.
6. Always put `bg` in commands that read WhatsApp, for example `$FB bg discover whatsapp-chats`. Without `bg` they can't read it from Terminal, Claude Code or Codex, because macOS grants permission per process. Don't have the user give Terminal the permission.

## Paths

- **PLUGIN**: the plugin root folder, two levels above this skill's folder (two levels above `skills/setup/`; it contains `install.sh`). If unsure, run `ls -d ~/.claude/plugins/cache/*/parent-recap/*/ | sort -V | tail -1`; for an install from the release zip, `~/FamilyBrief/plugin` is also the plugin root. When running in Codex, a line at the top of this file gives the plugin root.
- The program is installed in `~/FamilyBrief/app`; the command is `FB=~/FamilyBrief/app/.venv/bin/family-brief` and Python is `PY=~/FamilyBrief/app/.venv/bin/python`.
- Config is `~/.family/config.yaml` (mode 600), logs are in `~/FamilyBrief/logs/`, and daily archives in `~/FamilyBrief/`.
- Read the detailed docs only when needed, not all at once:
  - `PLUGIN/docs/sources.md`: step-by-step setup for each Source
  - `PLUGIN/docs/config.md`: config fields and city presets
  - `PLUGIN/docs/troubleshooting.md`: common problems

## Claude Code and Codex

This wizard works in both Claude Code and Codex:

- AskUserQuestion in this file is Claude Code's question tool. In Codex, ask in plain text and list the options for the user to pick from.
- In Codex, commands that install software, go online or write to `~/.family` must run outside the sandbox. Codex will ask for approval; tell the user this is normal and to approve.
- The two invoke skills differently: Claude Code starts with `/parent-recap:`, Codex with `$parent-recap-`. This file already uses the form for the one you are in; pass commands on to the user as written.

## Steps

### 0. Opening

First confirm the user's language with AskUserQuestion: offer the language you guessed from how they wrote first, then the reviewed languages (English, Chinese, Finnish) it isn't, at most four options in all; the question's own "Other" covers any other language. Tell them it is also the language their own Brief will come in, and that English, Chinese and Finnish are the reviewed languages: any other works too, with the program's own text (headings, hints) translated once by the AI ("Reviewed and best-effort languages" in `PLUGIN/docs/config.md`). Continue in their language from here on. Note its two- or three-letter code, without a region (`en`, `zh`, `sv`, `fi`; not `zh-CN` or `en-GB`), for step 4.

Then explain what Parent Recap does and what's needed: a Mac, a Claude Pro or Max subscription (or ChatGPT Plus or higher; Free and Go can't use the Codex command line), and Gmail with 2-Step Verification on. Optional: a Wilma parent account, WhatsApp for Mac, MyClub.

Then find out, in this order:

1. **The city**: which city the Household lives in, and the city of the Kids' school if that's another one. Ask in plain text, since any city works. The home city decides Weekend Picks (item 6). The school's city decides the Wilma address and the starting Gmail allowlist: look it up in "City presets" in `PLUGIN/docs/config.md`. For a city without a preset, tell the user it still works, and that setup will find its Wilma address and school mail domain together with them (step 3 and step 4).
2. **The AI** (AskUserQuestion): which AI writes the nightly summary, Claude (Pro or Max subscription) or ChatGPT (through Codex). Someone running this wizard in Codex usually means ChatGPT.
3. **Where school mail arrives**: which email address the school, the teachers and the clubs write to. Parent Recap reads and sends with one Gmail account, so school mail has to arrive in that account, or the Briefs come out empty. If it arrives at another address (a work address, Outlook, iCloud, another Gmail), tell the user the gist of "School mail at another address" in `PLUGIN/docs/sources.md`: set up automatic forwarding from that address to the Gmail account, or give the school the Gmail address. Parent Recap can't read a second account itself. Either way, ask which Gmail account Parent Recap will use (the one school mail arrives in, or the one it will be forwarded to) and note it for step 4. If they choose forwarding, they can set it up while setup goes on, and step 13's preview shows whether school mail is arriving.
4. **WhatsApp groups**: whether the Household has WhatsApp groups about the Kids or their school (class parents, teams, hobbies). If they do, say what reading them needs, before they pick their Sources: WhatsApp for Mac from the App Store (not the old version from WhatsApp's website), installed and linked to the phone that's in those groups, with its chat history synced. It can be done any time before step 7.
5. **The Sources** (AskUserQuestion, multi-select: Wilma / WhatsApp / MyClub): which to connect. Gmail is required, because the Brief is sent through Gmail. Offer WhatsApp only if they have such groups.
6. **Weekend Picks**: only if the home city is Helsinki, Espoo, Vantaa or Kauniainen, ask whether they want Weekend Picks every Friday. Anywhere else, don't offer them: they come from the event database of Helsinki, Espoo and Vantaa, so they'd have nothing near the Household. Say so in one sentence.

### 1. Check the environment

```bash
sw_vers -productVersion; for c in python3 node npm claude codex wilma brew; do printf "%-8s" $c; command -v $c || echo "(missing)"; done; python3 -c 'import sys; print(sys.version.split()[0])'; ls -d /Applications/Codex.app /Applications/ChatGPT.app 2>/dev/null
```

- Python must be 3.11 or newer (the 3.9 that ships with macOS won't do). Install: `brew install python`
- No Homebrew: give the user the install command from the https://brew.sh home page to run in Terminal themselves
- Wilma needs Node: `brew install node`
- Claude users: the `claude` command must be on the PATH, since the scheduled job uses it. If missing: `npm install -g @anthropic-ai/claude-code`
- ChatGPT users: Codex or the ChatGPT desktop app must be installed, and they must have signed in to Codex with their ChatGPT account. The program uses the codex bundled in the app, so it doesn't matter whether `codex` is on the PATH or works

### 2. Install the program

```bash
bash "PLUGIN/install.sh"
```

Note the **real Python path** in the output; step 7 needs it.

### 3. Wilma (optional)

Only if the user chose Wilma in step 0. Signing in before the household step lets step 4 read the Kids from Wilma, so the user doesn't type what Wilma already knows.

Follow the "Wilma" section of `sources.md`: install the CLI and have the user run `wilma` in **Terminal** to sign in. For a city without a preset, help them find its Wilma address first, as "Cities without a preset" in `PLUGIN/docs/config.md` says. Then you run `$FB discover wilma-students` (it needs no config yet) and keep its output for step 4: it lists each student with the full name as Wilma spells it, and may also give the school and class. If it says "not signed in to wilma", have the user sign in again. If it still fails, or lists no students, step 4 asks for the Kids as it does without Wilma, and `wilma.enabled` stays off until `discover wilma-students` works.

After sign-in, tell the user in a sentence or two the gist of "Where the Wilma password is stored" in `sources.md`: the password is stored unencrypted in `~/.config/wilmai/config.json`; don't sync or back up that folder; delete it when they stop using Parent Recap.

### 4. Household details

Ask about each item, then write a **minimal config** to `~/.family/config.yaml` (`chmod 600`) in the format of `PLUGIN/app/config.example.yaml`. At this point fill in only `summary_language`, kids, gmail, email, `llm.backend` (`claude` or `codex`) and, if `discover wilma-students` worked in step 3, `wilma.enabled: true`; leave everything else off:

- `summary_language`: the user's language from step 0, as its code. The whole Brief comes in it, headings and hints included
- Each Kid:
  - **With Wilma**: prefill one Kid per student in the `discover wilma-students` output from step 3: `name` exactly as Wilma spells it, and `school` and `class_name` when the output has them (the grade usually follows from the class, such as 3 for 3B). Show the user what you prefilled and have them confirm it, rather than asking for it again. Then ask only what Wilma doesn't know: each Kid's everyday name (for `everyday_name`, which the Brief calls them everywhere) and any other names used in chats and mail (for `aliases`), the grade if it doesn't follow from the class, and activities. Leave out a student who shouldn't be in the Brief
  - **Without Wilma**: ask for each Kid's full name (`name`), everyday name (`everyday_name`), grade, class, school and activities
- `gmail.username`: the Gmail account from step 0 that school mail arrives in (directly or forwarded)
- The starting Gmail allowlist, for the city from step 0: take it from the city presets in `PLUGIN/docs/config.md`; for a city without a preset, follow "Cities without a preset" there
- Who gets the Brief (`email.to`): usually their own and their partner's email, with the user's own address first. The Brief is written in the first Recipient's language, and what the Household shares (Google Calendar events, the archive) stays in it; mention this, in case they'd rather put the partner first. When you add each further address, ask that person's language with AskUserQuestion (the user's language first, then the reviewed languages it isn't, at most four options; "Other" covers the rest). If it differs from `summary_language`, write that entry as `{address: ..., language: <code>}`, as in "Recipients in their own language" in `PLUGIN/docs/config.md`; each gets the same Brief in their own language, at one extra AI call per night for each extra language. Leave `email.weekend_to` out, unlike the example config: Weekend Picks then go to the same Recipients, each in their language

### 5. Gmail (required)

Follow the "Gmail" section of `PLUGIN/docs/sources.md` to walk the user through creating an App Password, then have the user run in **Terminal**:

```bash
~/FamilyBrief/app/.venv/bin/python ~/FamilyBrief/app/scripts/setup_gmail_imap.py user@gmail.com
```

Once it succeeds, you run `$FB discover gmail-senders`. It usually takes 1 to 2 minutes and prints how many senders it has read so far; tell the user to wait. Then together with the user pick the domains of the school, teachers, clubs and music school from the list for `gmail.allowlist_domains`. **Don't add public domains like gmail.com or outlook.com**, or private mail gets scanned; for a teacher who uses a private address, add that address alone to `allowlist_senders`.

### 6. AI login (required)

Do one of these, according to the choice in step 0; details are in the "AI login" section of `sources.md`.

**ChatGPT (`llm.backend: codex`)**: no extra token; the scheduled job uses the login Codex has saved. Confirm the user has signed in to the Codex app with their ChatGPT account, then run `$FB doctor` and check the Codex line is ✅.

**Claude (`llm.backend: claude`)**: the scheduled job runs in the background and can't use Claude Code's login, so it needs a long-lived token. Have the user run in **Terminal**, one after the other:

```bash
claude setup-token
~/FamilyBrief/app/.venv/bin/python ~/FamilyBrief/app/scripts/setup_claude_token.py
```

The second has the Keychain ask twice for the token printed by the first (input is hidden, so paste it and press Enter each time; nothing appears), stores it in the Keychain and makes one test call.

**Once the AI login works**, run `$FB language <code>` for every language in the config (`summary_language` and any Recipient's `language`). For a reviewed language it only says so; for any other, the AI translates the program's own text into it once and it is kept. If that fails, run it again: the nightly run would retry on its own, but the preview in step 13 would show English headings.

### 7. WhatsApp (optional)

Follow the "WhatsApp" section of `sources.md`:

1. Confirm the App Store version of WhatsApp from step 0 is installed and signed in
2. You run `$FB app-management`: it selects the scheduled job's Python file in Finder and opens **System Settings → Privacy & Security → App Management**. Have the user drag the file from Finder into the list and turn its switch on. If it says it couldn't open them, give the user the two `open` commands it prints to run in Terminal
3. You run `$FB bg discover whatsapp-chats`. If a popup says "python3.x would like to access data from other apps", have the user click Allow. If it still can't read, the path added in step 2 is wrong, or it also needs adding to Full Disk Access
4. Together with the user, pick the groups about the kids, and mark each with its Kid (the Kid's full name, or `both`) and type (class / football / piano …). **Copy group names exactly from the output**; trailing spaces, curly quotes and emoji all have to match.
5. Write them into `whatsapp.chats` and set `whatsapp.enabled: true`

### 8. MyClub (optional)

Follow the "MyClub" section of `sources.md`: have the user find each Kid's calendar subscription link and save it in their own **Terminal** with `~/FamilyBrief/app/.venv/bin/python ~/FamilyBrief/app/scripts/setup_myclub.py "<Kid's name>"`, once per Kid. The link carries a personal token, so like a password it doesn't go in the chat (Rule 1), and you don't write it into the config yourself.

### 9. Calendar

Use AskUserQuestion to have the user pick one of two:

- **Email attachment (recommended, no authorization)**: `google_calendar.mode: ics`. New events come as an .ics attachment on the Brief email; one tap adds them to any calendar. For MyClub, suggest subscribing to that link directly in the phone's calendar, so it stays fully in sync with the club (steps in `sources.md`).
- **Write to Google Calendar automatically**: `mode: google`. Needs a Google app file (the JSON of a Desktop OAuth client), which the plugin doesn't include. First check whether `~/.family/calendar_credentials.json` exists; if it does, go straight to authorizing. If not, ask the user for it: pilot families use the file the Parent Recap maintainers sent separately; everyone else creates their own by following "Creating your own Google app" in `PLUGIN/docs/sources.md`, or switches to the email attachment if that's too much hassle. The file contains a secret: ask only where it is (for example `~/Downloads/xxx.json`), and don't let the user paste its contents into the chat. Once you have the path, save it with the command in step 1 of google mode under "Calendar" in `sources.md`. Then have the user run in **Terminal** `~/FamilyBrief/app/.venv/bin/python ~/FamilyBrief/app/scripts/setup_google_calendar.py`; the browser will say "Google hasn't verified this app": click "Advanced" → "Go to (app name)" → "Allow". The partner's Gmail can go in `invite_attendees` so new events invite them automatically. On a night when the authorization has expired or been revoked (for example after changing the Google password, or removing the app's access in the Google account) or a write fails, new events go into an .ics attachment on the Brief instead, so nothing is lost; re-authorizing won't write them twice.

### 10. Weekend Picks (optional)

Only if the user chose it in step 0, which offers it only in Helsinki, Espoo, Vantaa and Kauniainen: ask what each Kid likes and what the parents want the kids to try, and write it into `weekend_events.kid_preferences` and `parent_preferences`; set `regions` to the home city's Weekend Picks regions in "City presets" in `PLUGIN/docs/config.md`; set `enabled: true`. Weekend Picks go to the Brief's Recipients, each in their language, unless the user wants different addresses: then write them in `email.weekend_to`, in the same form as `email.to`, asking each new person's language the same way and running `$FB language <code>` for a new language.

### 11. Pilot feedback (pilot families only)

Do this step only if the user says they're in the Parent Recap pilot, or brings a `feedback` config section sent by the Parent Recap team. Every other family skips it: don't mention it, don't ask, and don't write a `feedback` section.

1. Read the "Pilot feedback" section of `PLUGIN/docs/config.md`, and tell the user every point the parents must know and agree to before turning it on, in your own words, leaving none out, especially that "just opening the link without submitting still leaves a record".
2. Use AskUserQuestion to ask whether they agree (turn it on / not now). If not, don't write a `feedback` section; tell the user they can turn it on later with `/parent-recap:manage`.
3. If they agree, have the user paste the whole section the team sent into the chat. It isn't a secret, so pasting is fine. Ask what to use for `household_label` (for example "Virtanen family"; if the team assigned one, use that), then write the whole section into `~/.family/config.yaml` as is, changing only `household_label` and not a single character of `prefill_base_url` or `fields`.

### 12. Health check

```bash
$FB doctor
```

Fix every ❌; with pilot feedback on, also fix any ⚠️ on the Pilot feedback line. A ⚠️ on a Language line means the program's own text in that language isn't translated yet: run the `$FB language <code>` it shows. doctor checks WhatsApp with the scheduled job's Python on its own, and says so on that line.

### 13. First Brief

Preview first (no email sent, nothing recorded). With WhatsApp connected, run `$FB bg run --dry-run --lookback-hours 72`; without `bg` the preview is missing the WhatsApp part. Without WhatsApp, run `$FB run --dry-run --lookback-hours 72`. 72 hours because a single day often has no new messages and the preview would be empty. Have the user check the content and which Kid each item belongs to. If something's wrong, adjust aliases, group assignments or the allowlist. Then run `$FB bg run --lookback-hours 72` to send the first real Brief, and ask the user to confirm it arrived in their inbox. If they chose Weekend Picks, also run `$FB weekend-events --dry-run` once.

Each of these runs can take several minutes. Give the command a timeout of at least 15 minutes; if your command tool allows less (Claude Code's allows 10), run it in the background and wait for it to finish before doing anything else. Never start a run again while one is still going. If your tool cut the real run off anyway, don't run it again straight away: it may still be going and send the Brief. Wait a few minutes and ask the user whether the Brief arrived. A run started while another is going stops at once with "Another FamilyBrief run is still going"; then wait, don't retry.

The preview shows only the first Recipient's language. With Recipients in more than one language, ask the user to check with their partner that the partner's copy of the real Brief arrived in the partner's language, without a line at the top saying it couldn't be translated.

With pilot feedback on, the preview shows no ⭐ / ❌; that's expected. Ask the user to confirm in this real Brief that each Action Item has a ⭐ and a ❌ next to it, and there's one ❌ under the Digest.

### 14. Schedule

```bash
$FB schedule install
```

If it prints a `sudo pmset ...` command, give it to the user to run in Terminal themselves, so the Mac wakes 5 minutes before the job starts. If it warns that the Mac already has a repeating wake schedule, show the user the schedule it lists and ask before they run the command, since it replaces that schedule. It prints no command when the Mac never sleeps or already wakes at that time. Remind the user: on the first scheduled run, if a popup says "python3.x would like to access data from other apps", click Allow.

### 15. Wrap-up

Tell the user:

- What time the Brief is sent each day, and what time Weekend Picks are sent each Friday
- For any problem later, or to change the config (new school year, new group, new recipient), just say "use /parent-recap:manage to take a look"
- The config is in `~/.family/config.yaml`, and the archive in `~/FamilyBrief/`
