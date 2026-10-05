---
name: setup
description: Install and configure Parent Recap, which every evening reads the kids' school mail, Wilma, WhatsApp parent groups and MyClub club calendar, and has Claude or ChatGPT (Codex) write a Brief sent to each parent's inbox in their own language. Use when the user says "install / set up parent recap", "install / set up family brief", "parent recap onboarding", "安装 / 设置 family brief", "家庭日报 onboarding", or has just installed this plugin and wants to start. macOS only.
---

# Parent Recap setup

You are helping a parent install Parent Recap on their own Mac. The user is often a Chinese family in the Helsinki region. Setup has six phases, in this order: **Welcome, Connect, Working, Check, First Brief, Finish**. The parent makes their choices up front, connects each Source from one list, and confirms one page of what was found. Commands do every mechanical step and print one line of JSON; you decide what to tell the parent from that line, rather than working through the step yourself or asking the parent to do it by hand.

**Run setup in the user's language**, not the language of this skill. Guess it from how they wrote (if they haven't written anything yet, open in English) and use it from the first line. The defaults card confirms it. If they switch languages later, follow them in the chat; that doesn't change the language of their Brief. In Chinese, call the Brief 日报 and Weekend Picks 周末活动推荐; in Finnish, kooste and viikonlopun vinkit; in English or any other language, say Brief and Weekend Picks. Commands, config keys and file paths stay as written.

## Rules

1. **A password, App Password, token or MyClub link never goes in the chat.** The `setup` commands ask for each one in a macOS dialog with hidden input (a hidden Terminal prompt without a desktop session), check it and store it themselves, and never print it. Wilma's password is typed only on the Wilma sign-in screen in Terminal, and the Mac password only in macOS's own dialogs. If the user pastes a secret into the chat anyway, tell them to delete it and make a new one afterwards. Once a Kid has a MyClub link, don't open or print the whole config; view it with `grep -v myclub_ical_url ~/.family/config.yaml`, and make changes with commands that don't print the file.
2. **Don't ask "done?" about what a command can check.** Each Source command waits for the parent and checks the result itself (a Gmail sign-in, a test call, a WhatsApp read, Wilma's student list). Tell the parent what to do, run the command, and act on its `result`. Ask the parent only about what the program can't see.
3. **Don't print mail or chat text into the chat.** Check connections with the `setup` commands and `$FB doctor`, which give only status, names and counts. Don't run `$FB collect`; it prints message text.
4. **Don't overwrite an existing config.** If `~/.family/config.yaml` or `~/.family/setup-progress.json` already exists, run `$FB setup status`. If it says `done`, this Mac has been set up before: stop and use `/parent-recap:manage` instead. If not, an earlier setup stopped part-way, in the chat or on the setup page: carry on from where it got to as "Coming from the setup page" says, without asking again what the config already has. If the user insists on setting up again, back it up first as `config.yaml.bak-<date>`. To start again from nothing, they can uninstall first ("uninstall Parent Recap" with `/parent-recap:manage`), then run setup.
5. **Commands that need `sudo`:** only show them, and let the user run them. The wake schedule normally needs none: `$FB schedule install` asks for the Mac password in macOS's administrator dialog.
6. **Always put `bg` in commands that read WhatsApp yourself**, for example `$FB bg run --dry-run`. Without it they can't read WhatsApp from Terminal, Claude Code or Codex, because macOS grants the permission per process. `$FB setup whatsapp` does this itself. Don't have the user give Terminal the permission.
7. **Never search the family's folders or read their files to find something** (a file they were sent, a password, a link). Not with `find`, `ls` or `grep`, not by opening a file to see what's inside, not even after saying you'd only look at file names. Ask the parent where it is, or offer the alternative that needs nothing (such as staying with the `.ics` attachment). Parent Recap's own files and the checks in this skill (the plugin folder, installed apps, `~/.family`) are fine.
8. **Never write the config by hand.** Every answer goes into it through `$FB setup save` (see "Saving answers and progress"), the same command the setup page uses, which checks the answers before writing them.

## Each message

The parent should always know what to do next, without asking.

- **One thing at a time.** Every message ends with exactly one thing for the parent to do or answer: one question, one choice, or one step such as "a Terminal window opens now: sign in there". Ask the next thing in the next message. One AskUserQuestion call, or one page the parent answers in a single reply, counts as one thing. Notes and reminders (where a password is kept, what a Source is for) come before that one thing, in a sentence or two each. Show the Source list only when a status in it changed.
- **Choices before typing.** Where the answer has a few likely values, offer them as choices with the default first: AskUserQuestion in Claude Code, a numbered list in Codex that the parent answers with a number. Ask the parent to type only what has no likely values, such as an email address or a name, and only once they've chosen something that needs it.
- **Long lists come ticked.** A list that could be long (Kids, Gmail senders, WhatsApp groups) comes with your best guess already ticked and "all" as an option, so the parent answers "ok", "all", or names only what to change.
- **No school or class questions.** Never ask for a Kid's school or class; parents often don't know them exactly, and the program works without them. Use them when Wilma gives them, and otherwise leave them out.

## Paths

- **PLUGIN**: the plugin root folder, two levels above this skill's folder (two levels above `skills/setup/`; it contains `install.sh`). If unsure, run `ls -d ~/.claude/plugins/cache/*/parent-recap/*/ | sort -V | tail -1`; for an install from the release zip, with the install line (`get.sh`) or with `get.sh --codex`, `~/FamilyBrief/plugin` is also the plugin root. When running in Codex, a line at the top of this file gives the plugin root.
- The program is installed in `~/FamilyBrief/app`; the command is `FB=~/FamilyBrief/app/.venv/bin/family-brief`.
- Config is `~/.family/config.yaml` (mode 600), logs are in `~/FamilyBrief/logs/`, and daily archives in `~/FamilyBrief/`.
- Read the detailed docs only when needed, not all at once:
  - `PLUGIN/docs/sources.md`: each Source, and what to tell the family about it
  - `PLUGIN/docs/config.md`: config fields and city presets
  - `PLUGIN/docs/troubleshooting.md`: common problems

## Claude Code and Codex

The flow is the same in both:

- AskUserQuestion in this file is Claude Code's question tool. In Codex, write the question and its options as a numbered list, with the default first, and the parent replies with a number.
- In Codex, the `setup` commands, `install.sh`, and anything that goes online, opens a page or dialog, or writes to `~/.family` must run outside the sandbox. Codex asks for approval each time; tell the user once that this is normal and to approve.
- The two invoke skills differently: Claude Code starts with `/parent-recap:`, Codex with `$parent-recap-`. This file already uses the form for the one you are in; pass commands on to the user as written.
- Some commands wait for the parent for up to 10 minutes (Wilma, WhatsApp), and a Brief run can take 15. Give each a long enough command timeout. Claude Code's tool allows at most 10 minutes: pass `--timeout 540` to `setup wilma` and `setup whatsapp`, and run a Brief in the background and wait for it to finish before doing anything else. The commands that show a dialog wait until the parent answers it; if your tool cuts one off, run it again with `--no-open`.

## The `setup` commands

Each prints one line of JSON. `result` says what happened; when it isn't a success, `next` says what to do about it, in English: tell the parent the gist in their language, help with it, and run the command again as `next` says (often with `--no-open`, so the page or window doesn't open twice).

| Command                                    | Success                                        | Also gives                                                           |
| ------------------------------------------ | ---------------------------------------------- | -------------------------------------------------------------------- |
| `$FB setup wilma --timeout 540 --language <code>` | `signed-in` | `kids` (name, school, class), `wilma_address`, `city` (or null) |
| `$FB setup gmail --address <address>`      | `saved`                                        | `address`                                                            |
| `$FB setup claude`                         | `saved`                                        | `test_call: ok`                                                      |
| `$FB setup whatsapp --timeout 540`         | `readable`                                     | `chats` (name, last message, archived, `hint` naming the Kids it seems to be about) |
| `$FB setup myclub --kid "<Kid's name>"`    | `saved`                                        | `events`, the number of events in the calendar                       |
| `$FB setup status`                         | `done`                                         | `outcomes`, each with `ok` and a `reason`                            |
| `$FB setup save` (answers as JSON on stdin) | `saved`                                       | `progress`                                                           |
| `$FB setup save --read`                    | `read`                                         | `progress`                                                           |

A `result` of `no-prompt` means no dialog could open here (for example from inside Codex's sandbox): run the command again outside the sandbox, and only if that fails too, give the parent the command in `next` to run in Terminal.

A `result` of `keychain-not-reachable` (from `setup gmail` or `setup claude`, with macOS's `code`) means macOS won't let the command save in the Keychain here, because it runs inside tmux or over SSH, and no prompt will come. Running it again from here fails the same way: give the parent the command in `next` to run in a plain Terminal window outside tmux or SSH (Shell → New Command… in Terminal works). `keychain-failed` carries macOS's `code` too, when there is one.

## Saving answers and progress

`$FB setup save` writes the parent's answers into `~/.family/config.yaml` (owner-only), and records how far setup has got, so the setup page and this chat can each pick up where the other stopped. It comes with the program, so the first save is right after `install.sh` in Connect, with the card's answers and `"progress": {"phase": "connect"}`. Give it the answers as JSON on stdin, with only the keys you're saving; whatever you leave out stays as it was:

```bash
$FB setup save <<'EOF'
{"language": "zh",
 "kids": [{"name": "Mia Virtanen", "everyday_name": "Mia", "aliases": ["米娅"], "school": "Kilo School", "class_name": "3B", "grade": 3}],
 "recipients": [{"address": "parent@gmail.com"}, {"address": "partner@gmail.com", "language": "fi"}],
 "ai": "claude",
 "evening": "21:00",
 "sources": {"gmail": {"address": "parent@gmail.com", "allowlist_domains": ["espoo.fi"], "allowlist_senders": []},
             "wilma": {"enabled": true},
             "whatsapp": {"enabled": true, "chats": [{"name": "3B vanhemmat", "kid": "Mia Virtanen", "label": "class"}]}},
 "feedback": {"enabled": true, "household_label": "Virtanen family"},
 "progress": {"phase": "connect", "source": "gmail", "sources": {"wilma": "done"}}}
EOF
```

- `kids` is the whole list of Kids: a Kid left out is removed. Each Kid keeps what you don't give for them, such as their MyClub link, which only `setup myclub` saves. Never put a MyClub link in the answers.
- `recipients`, `allowlist_domains`, `allowlist_senders` and `chats` are whole lists too: give every ticked item, not only the new ones.
- `language` is the Household's language code (`summary_language`); a Recipient's `language` is given only when it differs.
- `progress`: `phase` is one of `welcome`, `connect`, `working`, `check`, `first-brief`, `finish`; `source` is the Source the parent is on (`wilma`, `gmail`, `ai`, `whatsapp`, `myclub`); `sources` gives each Source's status, `to-do`, `done` or `skipped`. Save it whenever the phase or a Source's status changes, in the same call as that step's answers when there are any.
- A progress read back after the setup page's Welcome can also have `partner`: the partner's `address` and `language`, or `null` for "Only me". The page saves the Recipients only once it knows the parent's Gmail address, so if `recipients` aren't in the config yet, use this as the Welcome answer instead of asking again, and save `recipients` with the parent's address first once you have it.
- On `invalid-answers`, nothing was saved: `errors` names each answer that's wrong. Fix them and save again. On `bad-config`, the existing config can't be read: run `$FB doctor` and fix what it names.

## Coming from the setup page

Every step of the setup page has a **Continue in the chat** button, for a family that gets stuck there or would rather talk it through. In Claude Code it opens this skill in a Terminal window; in Codex the family types `$parent-recap-setup`. Either way, setup has already started: carry on from the step the page reached, keeping the answers already given, instead of starting over. Do the same for a setup that stopped part-way in an earlier chat.

1. Run `$FB setup save --read`. `phase` is the phase reached, `source` the Source the parent was on in Connect (null once each is done or skipped), and `sources` each Source's status.
2. Read the answers already given with `grep -v myclub_ical_url ~/.family/config.yaml`. `summary_language` is the language the parent picked on the page: use it from your first line, since they haven't written anything yet, and don't ask for it again. The AI (`llm.backend`), pilot feedback (`feedback.enabled`), the Recipients (`email.to`), the Gmail address (`gmail.username`), the city and the Kids are answers too. Without `llm.backend` yet (the parent left Welcome before saving it), the AI is the one whose chat you're in.
3. The progress can also have `partner`, Welcome's choice of partner (see "Saving answers and progress"), `whatsapp_chats`, the WhatsApp groups the page found, in the same form as `chats` from `setup whatsapp`, and `gmail_senders`, the Gmail senders the page read in Working, in the same form as `senders` from `discover gmail-senders --json`, for Check. Use them instead of asking again or reading WhatsApp again.
4. The program is already installed, since it serves the page: skip "Get the Mac ready first" and `install.sh` unless `$FB` is missing.
5. In one short message, say you're carrying on from where setup got to: the phase, and in Connect the Source list with its statuses. Ask the parent to carry on here rather than on the page, which they can close. Then go straight to that step and end with its one thing to do. On Welcome, ask only what isn't answered yet. Don't redo a Source that's `done`; a `skipped` one stays skipped unless the parent asks for it. If the parent says what went wrong on the page, help with that first.

## 1. Welcome

**Introduction.** In a few sentences: every evening Parent Recap reads the Kids' school mail, Wilma, WhatsApp parent groups and MyClub, and emails each parent a short Brief in their own language, with new events attached for the calendar. Then say:

- What a Brief looks like: the link to the demo in the invite the Parent Recap team sent them. If they don't have it, describe a Brief in a sentence: grouped by Kid, with Notices and Action Items with due dates
- Where the data goes: it's read on this Mac. Only that night's messages from the Kids' Sources go to the AI the family already uses, to write the Brief, and the Brief goes out from their own Gmail. Nothing goes to the Parent Recap team, except the ⭐ / ❌ feedback a pilot family chooses to send.
- What it costs: it runs on the paid plan the family already has, Claude Pro or Max, or ChatGPT Plus or above (ChatGPT's Free and Go plans can't run Codex's command line). Nothing else costs money.
- That it needs a Mac that is on in the evening, and Gmail.

**The defaults card.** End the introduction with the defaults, as choices the parent only changes where they're wrong:

| Default            | How you pick it                                                                                                                                         |
| ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Language           | The one they write in. It's setup's language and their Brief's                                                                                          |
| AI                 | Claude in Claude Code; **ChatGPT** ("runs through Codex") in Codex. Say a paid plan is needed                                                            |
| Recipients         | The parent and their partner ("Add my partner"), or "Only me"                                                                                           |
| Pilot family       | Yes. Only when `PLUGIN/app/src/family_brief/pilot_feedback.yaml` exists; without it, leave this line and every pilot question out                       |

The card has no calendar question: new events always come as an `.ics` attachment on the Brief, which one tap adds to any calendar. If the parent asks about Google Calendar, say it can be turned on later through `/parent-recap:manage`, once the Parent Recap team has given them the Google app file it needs.

- **In Claude Code**, make one AskUserQuestion call with four questions, the default first in each: language (the one you guessed first, then whichever of English, Chinese and Finnish are left, at most four options; "Other" covers any other language), AI (Claude / ChatGPT, runs through Codex), Recipients (Add my partner / Only me) and pilot family (yes / no), when the card has it.
- **In Codex**, list language, AI and pilot family as numbered lines and ask the parent to reply "ok", or the number of a line to change; then ask that line's question as a numbered list. Once the card is settled, ask Recipients as its own numbered choice (1. Add my partner, 2. Only me).

Then, one message each:

1. The parent's Gmail address, typed.
2. Only if they added their partner: the partner's email address, typed.
3. Then the partner's language, as a choice with the parent's language first.

Note from the answers:

- The language as its two- or three-letter code, without a region (`en`, `zh`, `sv`, `fi`; not `zh-CN`). English, Chinese and Finnish are reviewed; any other works too, with the program's own text (headings, hints) translated once by the AI ("Reviewed and best-effort languages" in `PLUGIN/docs/config.md`). Say this only if they pick another language.
- The parent's Gmail address. Parent Recap reads school mail in it and sends the Brief from it, so it's the account school mail arrives in. If the parent gives an address that isn't Gmail, ask for the Gmail address school mail arrives in, or is forwarded to ("School mail at another address" in `PLUGIN/docs/sources.md`).
- The Recipients: the parent's address first, then the partner's if they added one, with the partner's language if it differs. The Brief is written in the first Recipient's language, and the archive and calendar events follow it.

**What they'll connect.** Show the Source list below, with one line each on why it's needed, and that WhatsApp and MyClub can be skipped and added later through `/parent-recap:manage`. Ask them to have their Wilma username and password ready, the same ones as on the Wilma website or app; if they don't remember them, they may find them in their browser's saved passwords. End with one choice: start now, or wait while they find their Wilma login.

## 2. Connect

**Get the Mac ready first**, since the `setup` commands come with the program:

```bash
sw_vers -productVersion; for c in python3 node npm claude brew; do printf "%-8s" $c; command -v $c || echo "(missing)"; done; ls ~/.local/bin/claude 2>/dev/null; python3 -c 'import sys; print(sys.version.split()[0])'; ls -d /Applications/Codex.app /Applications/ChatGPT.app 2>/dev/null
```

- Python must be 3.11 or newer (the 3.9 that ships with macOS won't do): `brew install python`. Wilma needs Node: `brew install node`. Without Homebrew, give the user the install command from https://brew.sh to run in Terminal themselves.
- Claude: the nightly job uses the `claude` command. If `claude` is missing and there's no `~/.local/bin/claude` either, give the user Claude Code's native installer to run in Terminal: `curl -fsSL https://claude.ai/install.sh | bash`. It installs into their own folder (`~/.local/bin`) and keeps Claude Code up to date by itself. A `~/.local/bin/claude` counts as installed even when `command -v` says missing: the program finds it there. Don't suggest `npm install -g @anthropic-ai/claude-code`: a global npm install can leave Claude Code unable to update itself.
- If the parent sees Claude Code's red notice "Auto-update failed" (often "no write permission to npm prefix"), or `claude` isn't in `~/.local/bin` and `npm ls -g @anthropic-ai/claude-code` lists it (an npm install), tell them the notice comes from Claude Code itself and doesn't affect Parent Recap. Setup carries on. If they want the notice gone, they can install Claude Code again with the native installer above, after setup.
- ChatGPT: Codex or the ChatGPT desktop app must be installed and signed in to Codex with their ChatGPT account. The program uses the codex bundled in the app.

Then install the program with `bash "PLUGIN/install.sh"`. It takes a few minutes. Once it's done, save the card's answers with `$FB setup save`: `language`, `ai`, `recipients`, `sources.gmail.address`, and `"progress": {"phase": "connect", "source": "wilma"}`.

**The Source list.** Keep a status for each Source (to do, done, skipped), save it in `progress` with `$FB setup save` each time it changes (with `source` set to the Source the parent goes on to), and show the list, with the statuses, when a sub-flow changes one. Go down it in order, from the least to the most sensitive:

1. **Wilma**: the Kids' names, and the school's messages and timetable
2. **Gmail** (required): school and club mail, and the Brief is sent from it
3. **AI login** (required for Claude; for ChatGPT, only a check)
4. **WhatsApp** (can be skipped): the class and club parent groups
5. **MyClub** (can be skipped): the club calendars

Each sub-flow: in one message, say in a sentence or two why it's needed and end with the one thing the parent does (such as what to do in the window that opens); then run its command, act on the result and mark it done. The message after a sub-flow carries its result, any short reminder from it and the list with the new status, and ends with one choice: go on to the next Source, or wait a moment. Keep the next Source's own explanation for its own message. When a command fails, say clearly what went wrong and the one thing to do about it, help with it and run it again. If the parent wants to skip WhatsApp or MyClub, mark it skipped. Gmail and the AI login can't be skipped; neither can Wilma when the school uses it.

### Wilma

Why: Wilma has the school's messages and timetable, and setup reads the Kids' names from it, so the parent doesn't type them.

If the Kids' school doesn't use Wilma, mark Wilma skipped. Ask, one message each, for the city the school is in (a choice: Espoo, Helsinki, Vantaa, Kauniainen, with any other city typed) and each Kid's full name; not their school or class. Then go on to writing the config.

Otherwise:

1. Don't install the wilma CLI yourself: `setup wilma` installs the version setup is tested with when it's missing, and records it so uninstall removes it. On `not-installed`, do what its `next` says (Node is missing: `brew install node`), then run it again.
2. Tell the parent a Terminal window will open with a short guide on top: they type their town in Finnish (Espoo, Helsinki, Vantaa …), pick it from the list, and sign in with their Wilma username and password. The window ends Wilma by itself once they're signed in and says it can be closed. Then run `$FB setup wilma --timeout 540 --language <code>`, with the language code from the card. The guide is in English, Chinese or Finnish; in any other language it's in English, so tell the parent the steps in their language before running it.
3. On `signed-in`, show the Kids it found, by name. Don't ask for the city: `city` comes from the Wilma address. If `city` is null, it's a city without a preset: follow "Cities without a preset" in `PLUGIN/docs/config.md` for the starting Gmail allowlist. Don't ask about the Kids yet; the parent confirms them in Check.
4. In the same message, as a short note before the choice to go on to Gmail, give the gist of "Where the Wilma password is stored" in `PLUGIN/docs/sources.md`: the wilma CLI keeps the password unencrypted in `~/.config/wilmai/config.json`, so they shouldn't sync or back up that folder.

**Save the Kids** now with `$FB setup save`, in one call:

- `kids`: one per Kid, `name` exactly as Wilma spells it (or as the parent typed it without Wilma), `school` and `class_name` only when Wilma has them, and `grade` when it follows from the class (3 for 3B). Their everyday names come in Check
- `city`: the Household's town in Finnish, such as `Espoo`: the `city` from `setup wilma`, or the city the parent gave without Wilma. Leave it out when `setup wilma` gave none
- `sources.gmail.allowlist_domains`: the starting allowlist for the city from "City presets" in `PLUGIN/docs/config.md`
- `sources.wilma`: `{"enabled": true}` with Wilma
- `progress`: Wilma `done` (or `skipped`), and `source` `gmail`

Nothing else goes in the config in setup, including Weekend Picks: they aren't part of setup, and the parent can turn them on later through `/parent-recap:manage`. New events come as an `.ics` attachment, which is the default.

### Gmail

Before anything else, tell the parent, in their language:

- They never give Parent Recap their Google password.
- Instead they create an App Password: a separate 16-letter password that only Parent Recap uses, and that they can delete in their Google account at any time.
- Google may ask them to sign in on its own page first.
- On Google's App passwords page, they type **Parent Recap** as the app name. It's only a label, for finding the password later. Then they click Create, copy the 16 letters and paste them into the Parent Recap dialog.
- If the page says the setting isn't available, they click **The page isn't available** in the dialog. That usually means 2-Step Verification is off.

Then run `$FB setup gmail --address <address>`. It opens the App passwords page and the dialog, signs in to Gmail with what was pasted, and stores it only if Gmail accepts it.

- `saved`: done.
- `app-passwords-unavailable`: 2-Step Verification is off, or a work or school account doesn't allow App Passwords. Give the parent the 2-Step Verification link from `next`, wait for them to turn it on, then run the command again.
- `not-an-app-password`, `rejected`, `cancelled`, `no-connection`, `keychain-failed`: tell the parent what `next` says and run it again.

### AI login

**Claude** (`llm.backend: claude`). Why: the nightly job runs in the background, where it can't use Claude Code's login, so it needs its own token, valid for a year. Tell the parent a Terminal window and Claude's sign-in page will open: they click Authorize, copy the token the Terminal window shows (it starts with `sk-ant-oat01-`), paste it into the Parent Recap dialog, and then press Enter in the Terminal window to clear it. Run `$FB setup claude`. On `saved`, it has made a test call. On `test-call-failed`, check the plan is Claude Pro or Max.

**ChatGPT** (`llm.backend: codex`). There's no token step: the nightly job reuses the login Codex already has. Tell the parent so; the test call happens in Working. If Codex isn't signed in with a ChatGPT account, have them sign in in the Codex app first.

### WhatsApp

Why: class and club parent groups are often where things are said first. Parent Recap reads WhatsApp for Mac's own data on this Mac; nothing from WhatsApp leaves it except that night's messages from the groups the parent picks.

It needs WhatsApp for Mac from the App Store (not the older version from WhatsApp's website), linked to the phone that's in those groups, with its chats synced. The command finds out whether it's there.

Tell the parent that if macOS needs a permission, Finder will show a Python file and System Settings will open at App Management: they drag the file into the list and turn its switch on, and click Allow if macOS asks whether python3.x may access data from other apps. Then run `$FB setup whatsapp --timeout 540`. It waits for the permission itself.

- `readable`: keep `chats` for Check. Don't show them yet.
- `not-installed`: WhatsApp for Mac isn't installed or signed in. Offer a choice: install it now (they install it, link it and let the chats sync, and you run the command again), or skip WhatsApp for now.
- `no-permission` or `waiting`: help with what `next` says, then run `$FB setup whatsapp --no-open --timeout 540`. If App Management doesn't work, the same file goes into Full Disk Access.
- `bg-failed` or `unreadable`: run it again once; if it fails again, do what `next` says.

### MyClub

Why: club practices, matches and their changes are in MyClub. Each Kid's calendar has a subscription link that works like a password, so it goes into a dialog and never into the chat.

First ask which Kids have a MyClub calendar, as a choice with every Kid ticked and "all" and "none (skip MyClub)" as options. For each Kid ticked, one at a time, tell the parent MyClub's page will open: they sign in, open that Kid's calendar, choose Calendar subscription (Tilaa kalenteri), copy the link starting with `webcal://` and paste it into the dialog. Run `$FB setup myclub --kid "<Kid's name>"`, with the name exactly as under `kids:` in the config. On `saved`, say how many events it found. Otherwise (most often `not-a-myclub-link`: they copied the browser's address, or their password), tell them what `next` says and run it again with `--no-open`.

Mention that they can also subscribe to that link in their phone's calendar, so club changes show up straight away ("MyClub" in `PLUGIN/docs/sources.md`).

## 3. Working

Tell the parent they can step away for a few minutes. Save `"progress": {"phase": "working"}`. Then:

1. Run `$FB language <code>` for each language in the config (`summary_language` and any Recipient's `language`). For a reviewed language it only says so; for any other, the AI translates the program's own text once. If it fails, run it again, or the preview shows English headings.
2. Run `$FB discover gmail-senders --json`. It reads only the senders of the last 60 days, takes 1 to 2 minutes, and prints one line of JSON: `senders`, each with its `domain`, how many mails (`count`), an `example` sender name, `likely` when it looks like school, city or club mail, and `public` for a mail service anyone has an address at. If the setup page already read them, they're in the progress's `gmail_senders` (see "Coming from the setup page"): use those instead.
3. Run `$FB doctor`. It makes a test call to the AI, and with ChatGPT this is the check for the AI login. Fix each ❌ with the parent before going on.

Wilma's Kids and WhatsApp's chats came back from their sub-flows; read them again only if they're lost (`$FB setup wilma --no-open`, `$FB setup whatsapp --no-open`).

## 4. Check

Show **one page**, with everything already ticked as you think it should be, and have the parent untick what isn't about the Kids:

- **Kids**: each Kid with the everyday name the Brief will call them, guessed from the first name. The parent corrects it and adds any other names used in chats and mail (`aliases`). A Kid who shouldn't be in the Brief is unticked.
- **Gmail senders**: the domains from `discover gmail-senders --json` with `likely`, and any others that look like the school, the teachers, clubs, hobbies and the music school, ticked; the starting allowlist ticked. **Never tick public domains** (those with `public`) like gmail.com or outlook.com, or private mail gets read; a teacher or coach writing from one goes in `allowlist_senders` as the full address. If no school senders turned up, say so on the page: school mail may arrive at another address, and it has to be forwarded to this Gmail ("School mail at another address" in `PLUGIN/docs/sources.md`), or the Briefs come out empty.
- **WhatsApp groups**: the chats from `setup whatsapp`, those with a `hint` ticked and marked with the Kids it names (or `both`), and a type (class / football / piano …). The rest listed unticked.

Give each list an "all" line the parent can tick instead of going through it. The lists are too long for AskUserQuestion's few options, so in both Claude Code and Codex write the page as text with ☑ and ☐, and end it with the one thing to do: reply "ok" to keep it as ticked, "all" with a list's name (such as "all groups") to tick that whole list, or name what to change. Ask nothing else on the page; an everyday name or alias they want changed goes in the same reply.

Then save it with `$FB setup save`: `kids` with every ticked Kid's `name`, `everyday_name` and `aliases`; `sources.gmail` with the ticked domains in `allowlist_domains` and the ticked addresses in `allowlist_senders`; `sources.whatsapp` with `"enabled": true` and each ticked group in `chats`, with `name` **copied exactly** from `chats` (trailing spaces, curly quotes and emoji all have to match), `kid` (a Kid's `name`, or `both`) and `label`; and `"progress": {"phase": "check"}`.

**Pilot feedback**, if the card says pilot family. Read the "Pilot feedback" section of `PLUGIN/docs/config.md` and tell the parent every point the parents must know before turning it on, in your own words, leaving none out, especially that "just opening the link without submitting still leaves a record". End with one choice: turn it on, or not now. If not, save no `feedback`, and say they can turn it on later with `/parent-recap:manage`. If they agree, in the next message offer a `household_label` as a choice: the family name from the Kids' names (such as "Virtanen family"), with their own typed as the other option. Save `"feedback": {"enabled": true, "household_label": "..."}` with `$FB setup save`: it writes the rest of the section from the pilot form the program ships. Nothing is pasted.

Then run `$FB doctor` once more. Fix every ❌, and with pilot feedback on, any ⚠️ on its line. Other ⚠️ don't stop setup: tell the parent what each one means and what to do, as doctor says.

## 5. First Brief

Save `"progress": {"phase": "first-brief"}` first.

1. **Preview**, with no email sent and nothing recorded: `$FB bg run --dry-run --lookback-hours 72` (72 hours, since a single day often has nothing new). Show the parent the preview and end with one choice: it looks right, or something is off (a wrong Kid, a group or sender that shouldn't be there, something missing). If something is off, they say what in their own words; save the fix with `$FB setup save`, and show a new preview. Repeat until they're happy.
2. **The real Brief**: `$FB bg run --lookback-hours 72`. It goes to both Recipients.
3. Each run can take several minutes. Never start one while another is going. If your tool cut the real run off, don't run it again: it may still be going and will send the Brief. Wait a few minutes, then check with `$FB setup status` whether it went out. A run started while another is going stops at once with "Another FamilyBrief run is still going"; then wait, don't retry.
4. With the partner in another language, the preview shows only the parent's. Ask the parent to have their partner check that their copy came in their language, without a line at the top saying it couldn't be translated.
5. With pilot feedback on, the preview has no ⭐ / ❌. Ask the parent to check the real Brief has a ⭐ and a ❌ next to each Action Item, and one ❌ under the Digest.

## 6. Finish

Save `"progress": {"phase": "finish"}` first.

1. Tell the parent macOS will ask for their Mac password in its own dialog, to wake the Mac 5 minutes before the Brief. Run `$FB schedule install`. If it warns the Mac already has a repeating wake schedule, show the parent what it lists and ask, as a choice, whether to replace it (keep it first); only if they choose to replace it, run `$FB schedule install --replace-wake`. If it prints a `sudo pmset ...` command, give it to the parent to run in Terminal.
2. Run `$FB setup status`. It reports five outcomes: the program installed, the health check with nothing to fix (no ❌; its `reason` names any ⚠️, which you tell the parent about with what to do, but which don't stop setup), the first Brief delivered to the setup parent (the first Recipient; the others' first is the first evening one), the nightly job loaded, and the wake schedule set or the Mac never sleeping. Show them as a checklist. For each with `ok: false`, do what its `reason` says, and run it again, until `result` is `done`.
3. The sixth outcome is yours to confirm: after the Mac restarts, such as after a macOS update, someone must log in to this Mac user once, or no Brief comes until they do. The wake-up wakes a sleeping Mac only: a Mac that's shut down makes no Brief, so in the evening it should be left asleep or locked, not shut down. Tell the parent both, and end with one choice for them to confirm they've got it.

Setup ends only when `setup status` says `done` and the parent has confirmed the restart reminder. Then tell them:

- What time the Brief comes each day
- That on the first nightly run, if macOS asks whether python3.x may access data from other apps, they click Allow
- That for any problem or change later (a new school year, a new group, a skipped Source, another Recipient, Weekend Picks, uninstalling), they type `/parent-recap:manage` or just say what they want
- That the config is in `~/.family/config.yaml`, and the archive of Briefs in `~/FamilyBrief/`
