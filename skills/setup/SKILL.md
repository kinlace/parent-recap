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
4. **Don't overwrite an existing config.** If `~/.family/config.yaml` already exists, run `$FB setup status`. If it says `done`, this Mac has been set up before: stop and use `/parent-recap:manage` instead. If not, an earlier setup stopped part-way: tell the user, work out from the config and the outcomes which Sources are done, and carry on from there, without asking again what the config already has. If the user insists on setting up again, back it up first as `config.yaml.bak-<date>`. To start again from nothing, they can uninstall first ("uninstall Parent Recap" with `/parent-recap:manage`), then run setup.
5. **Commands that need `sudo`:** only show them, and let the user run them. The wake schedule normally needs none: `$FB schedule install` asks for the Mac password in macOS's administrator dialog.
6. **Always put `bg` in commands that read WhatsApp yourself**, for example `$FB bg run --dry-run`. Without it they can't read WhatsApp from Terminal, Claude Code or Codex, because macOS grants the permission per process. `$FB setup whatsapp` does this itself. Don't have the user give Terminal the permission.
7. **Never search the family's folders or read their files to find something** (a file they were sent, a password, a link). Not with `find`, `ls` or `grep`, not by opening a file to see what's inside, not even after saying you'd only look at file names. Ask the parent where it is, or offer the alternative that needs nothing (such as staying with the `.ics` attachment). Parent Recap's own files and the checks in this skill (the plugin folder, installed apps, `~/.family`) are fine.

## Paths

- **PLUGIN**: the plugin root folder, two levels above this skill's folder (two levels above `skills/setup/`; it contains `install.sh`). If unsure, run `ls -d ~/.claude/plugins/cache/*/parent-recap/*/ | sort -V | tail -1`; for an install from the release zip or with `get.sh --codex`, `~/FamilyBrief/plugin` is also the plugin root. When running in Codex, a line at the top of this file gives the plugin root.
- The program is installed in `~/FamilyBrief/app`; the command is `FB=~/FamilyBrief/app/.venv/bin/family-brief`.
- Config is `~/.family/config.yaml` (mode 600), logs are in `~/FamilyBrief/logs/`, and daily archives in `~/FamilyBrief/`.
- Read the detailed docs only when needed, not all at once:
  - `PLUGIN/docs/sources.md`: each Source, and what to tell the family about it
  - `PLUGIN/docs/config.md`: config fields and city presets
  - `PLUGIN/docs/troubleshooting.md`: common problems

## Claude Code and Codex

The flow is the same in both:

- AskUserQuestion in this file is Claude Code's question tool. In Codex, write the question and its options as text, with the default first, and the parent replies.
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

A `result` of `no-prompt` means no dialog could open here (for example from inside Codex's sandbox): run the command again outside the sandbox, and only if that fails too, give the parent the command in `next` to run in Terminal.

## 1. Welcome

**Introduction.** In a few sentences: every evening Parent Recap reads the Kids' school mail, Wilma, WhatsApp parent groups and MyClub, and emails each parent a short Brief in their own language, with new events attached for the calendar. Then say:

- What a Brief looks like: the link to the demo in the invite the Parent Recap team sent them. If they don't have it, describe a Brief in a sentence: grouped by Kid, with Notices and Action Items with due dates
- Where the data goes: it's read on this Mac. Only that night's messages from the Kids' Sources go to the AI the family already uses, to write the Brief, and the Brief goes out from their own Gmail. Nothing goes to the Parent Recap team, except the ⭐ / ❌ feedback a pilot family chooses to send.
- What it costs: it runs on the paid plan the family already has, Claude Pro or Max, or ChatGPT Plus or above (ChatGPT's Free and Go plans can't run Codex's command line). Nothing else costs money.
- That it needs a Mac that is on in the evening, and Gmail.

**The defaults card.** Show the defaults as a short list, then let the parent change only what's wrong:

| Default            | How you pick it                                                                                                                                         |
| ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Language           | The one they write in. It's setup's language and their Brief's                                                                                          |
| AI                 | Claude in Claude Code; **ChatGPT** ("runs through Codex") in Codex. Say a paid plan is needed                                                            |
| Recipients         | The parent, and their partner, whose language is the parent's unless they say otherwise                                                                |
| Pilot family       | Yes                                                                                                                                                     |

The card has no calendar question: new events always come as an `.ics` attachment on the Brief, which one tap adds to any calendar. If the parent asks about Google Calendar, say it can be turned on later through `/parent-recap:manage`, once the Parent Recap team has given them the Google app file it needs.

- **In Claude Code**, make one AskUserQuestion call with three questions, the default first in each: language (the one you guessed first, then whichever of English, Chinese and Finnish are left, at most four options; "Other" covers any other language), AI (Claude / ChatGPT, runs through Codex) and pilot family (yes / no). Then ask in plain text for the parent's Gmail address and their partner's email, and say the partner gets the Brief in the parent's language unless they name another.
- **In Codex**, list the defaults as text and ask for the two addresses in the same message. The parent replies "ok" with the addresses, or names what to change.

Note from the answers:

- The language as its two- or three-letter code, without a region (`en`, `zh`, `sv`, `fi`; not `zh-CN`). English, Chinese and Finnish are reviewed; any other works too, with the program's own text (headings, hints) translated once by the AI ("Reviewed and best-effort languages" in `PLUGIN/docs/config.md`). Say this only if they pick another language.
- The parent's Gmail address. Parent Recap reads school mail in it and sends the Brief from it, so it's the account school mail arrives in. If the parent gives an address that isn't Gmail, ask for the Gmail address school mail arrives in, or is forwarded to ("School mail at another address" in `PLUGIN/docs/sources.md`).
- The Recipients: the parent's address first, then the partner's, with the partner's language if it differs. The Brief is written in the first Recipient's language, and the archive and calendar events follow it.

**What they'll connect.** Show the Source list below, with one line each on why it's needed, and that WhatsApp and MyClub can be skipped and added later through `/parent-recap:manage`. Ask them to have their Wilma username and password ready, the same ones as on the Wilma website or app; if they don't remember them, they may find them in their browser's saved passwords. Then start.

## 2. Connect

**Get the Mac ready first**, since the `setup` commands come with the program:

```bash
sw_vers -productVersion; for c in python3 node npm claude brew; do printf "%-8s" $c; command -v $c || echo "(missing)"; done; ls ~/.local/bin/claude 2>/dev/null; python3 -c 'import sys; print(sys.version.split()[0])'; ls -d /Applications/Codex.app /Applications/ChatGPT.app 2>/dev/null
```

- Python must be 3.11 or newer (the 3.9 that ships with macOS won't do): `brew install python`. Wilma needs Node: `brew install node`. Without Homebrew, give the user the install command from https://brew.sh to run in Terminal themselves.
- Claude: the nightly job uses the `claude` command. If `claude` is missing and there's no `~/.local/bin/claude` either, give the user Claude Code's native installer to run in Terminal: `curl -fsSL https://claude.ai/install.sh | bash`. It installs into their own folder (`~/.local/bin`) and keeps Claude Code up to date by itself. A `~/.local/bin/claude` counts as installed even when `command -v` says missing: the program finds it there. Don't suggest `npm install -g @anthropic-ai/claude-code`: a global npm install can leave Claude Code unable to update itself.
- If the parent sees Claude Code's red notice "Auto-update failed" (often "no write permission to npm prefix"), or `claude` isn't in `~/.local/bin` and `npm ls -g @anthropic-ai/claude-code` lists it (an npm install), tell them the notice comes from Claude Code itself and doesn't affect Parent Recap. Setup carries on. If they want the notice gone, they can install Claude Code again with the native installer above, after setup.
- ChatGPT: Codex or the ChatGPT desktop app must be installed and signed in to Codex with their ChatGPT account. The program uses the codex bundled in the app.

Then install the program with `bash "PLUGIN/install.sh"`. It takes a few minutes.

**The Source list.** Keep a status for each Source (to do, done, skipped) and show the list, with the statuses, after each sub-flow. Go down it in order, from the least to the most sensitive:

1. **Wilma**: the Kids' names, school and class, and the school's messages and timetable
2. **Gmail** (required): school and club mail, and the Brief is sent from it
3. **AI login** (required for Claude; for ChatGPT, only a check)
4. **WhatsApp** (can be skipped): the class and club parent groups
5. **MyClub** (can be skipped): the club calendars

Each sub-flow: say in a sentence or two why it's needed, before asking for anything; run its command; act on the result; mark it done and show the list again. When a command fails, say clearly what went wrong and what to do, help with it and run it again. If the parent wants to skip WhatsApp or MyClub, mark it skipped. Gmail and the AI login can't be skipped; neither can Wilma when the school uses it.

### Wilma

Why: Wilma has the school's messages and timetable, and setup reads the Kids' names, school and class from it, so the parent doesn't type them.

If the Kids' school doesn't use Wilma, mark Wilma skipped, ask for each Kid's full name, school and class, and which city the school is in, then go on to writing the config.

Otherwise:

1. If `wilma` isn't installed, run `npm install -g @wilm-ai/wilma-cli`.
2. Tell the parent a Terminal window will open with a short guide on top: they type their town in Finnish (Espoo, Helsinki, Vantaa …), pick it from the list, and sign in with their Wilma username and password. The window ends Wilma by itself once they're signed in and says it can be closed. Then run `$FB setup wilma --timeout 540 --language <code>`, with the language code from the card. The guide is in English, Chinese or Finnish; in any other language it's in English, so tell the parent the steps in their language before running it.
3. On `signed-in`, show the Kids it found. Don't ask for the city: `city` comes from the Wilma address. If `city` is null, it's a city without a preset: follow "Cities without a preset" in `PLUGIN/docs/config.md` for the starting Gmail allowlist.
4. Tell the parent in a sentence or two the gist of "Where the Wilma password is stored" in `PLUGIN/docs/sources.md`: the wilma CLI keeps the password unencrypted in `~/.config/wilmai/config.json`, so they shouldn't sync or back up that folder.

**Write the config** now, as `~/.family/config.yaml` (`chmod 600`) in the format of `PLUGIN/app/config.example.yaml`, with only:

- `summary_language`: the language code from the card
- `kids`: one per Kid from Wilma, `name` exactly as Wilma spells it, `school` and `class_name` when it has them, and `grade` when it follows from the class (3 for 3B). Their everyday names come in Check
- `gmail.username`: the Gmail address from the card, and `gmail.allowlist_domains`: the starting allowlist for the city from "City presets" in `PLUGIN/docs/config.md`
- `email.to`: the Recipients from the card, a partner with another language as `{address: ..., language: <code>}` ("Recipients in their own language" in `PLUGIN/docs/config.md`). Leave out `email.weekend_to`
- `llm.backend`: `claude` or `codex`
- `google_calendar.mode: ics`
- `wilma.enabled: true` with Wilma

Leave everything else off, including Weekend Picks: they aren't part of setup, and the parent can turn them on later through `/parent-recap:manage`.

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
- `not-installed`: WhatsApp for Mac isn't installed or signed in. Offer to skip WhatsApp, or wait while they install it, link it and let the chats sync, then run the command again.
- `no-permission` or `waiting`: help with what `next` says, then run `$FB setup whatsapp --no-open --timeout 540`. If App Management doesn't work, the same file goes into Full Disk Access.
- `bg-failed` or `unreadable`: run it again once; if it fails again, do what `next` says.

### MyClub

Why: club practices, matches and their changes are in MyClub. Each Kid's calendar has a subscription link that works like a password, so it goes into a dialog and never into the chat.

For each Kid with a MyClub calendar, tell the parent MyClub's page will open: they sign in, open that Kid's calendar, choose Calendar subscription (Tilaa kalenteri), copy the link starting with `webcal://` and paste it into the dialog. Run `$FB setup myclub --kid "<Kid's name>"`, with the name exactly as under `kids:` in the config. On `saved`, say how many events it found. Otherwise (most often `not-a-myclub-link`: they copied the browser's address, or their password), tell them what `next` says and run it again with `--no-open`.

Mention that they can also subscribe to that link in their phone's calendar, so club changes show up straight away ("MyClub" in `PLUGIN/docs/sources.md`).

## 3. Working

Tell the parent they can step away for a few minutes. Then:

1. Run `$FB language <code>` for each language in the config (`summary_language` and any Recipient's `language`). For a reviewed language it only says so; for any other, the AI translates the program's own text once. If it fails, run it again, or the preview shows English headings.
2. Run `$FB discover gmail-senders`. It reads only the senders of the last 60 days, and takes 1 to 2 minutes.
3. Run `$FB doctor`. It makes a test call to the AI, and with ChatGPT this is the check for the AI login. Fix each ❌ with the parent before going on.

Wilma's Kids and WhatsApp's chats came back from their sub-flows; read them again only if they're lost (`$FB setup wilma --no-open`, `$FB setup whatsapp --no-open`).

## 4. Check

Show **one page**, with everything already ticked as you think it should be, and have the parent untick what isn't about the Kids:

- **Kids**: each Kid with the everyday name the Brief will call them, guessed from the first name. The parent corrects it and adds any other names used in chats and mail (`aliases`). A Kid who shouldn't be in the Brief is unticked.
- **Gmail senders**: the domains from `discover gmail-senders` that look like the school, the teachers, clubs, hobbies and the music school, ticked; the starting allowlist ticked. **Never tick public domains** like gmail.com or outlook.com, or private mail gets read; a teacher or coach writing from one goes in `allowlist_senders` as the full address. If no school senders turned up, say so on the page: school mail may arrive at another address, and it has to be forwarded to this Gmail ("School mail at another address" in `PLUGIN/docs/sources.md`), or the Briefs come out empty.
- **WhatsApp groups**: the chats from `setup whatsapp`, those with a `hint` ticked and marked with the Kids it names (or `both`), and a type (class / football / piano …). The rest listed unticked.

In Claude Code, write the page as a list with ☑ and ☐, and the parent replies with what to change. In Codex, the same as text.

Then write it to the config: `everyday_name` and `aliases` on each Kid, the ticked domains in `gmail.allowlist_domains`, and each ticked group in `whatsapp.chats` with `name` **copied exactly** from `chats` (trailing spaces, curly quotes and emoji all have to match), `kid` (a Kid's `name`, or `both`) and `label`, with `whatsapp.enabled: true`.

**Pilot feedback**, if the card says pilot family. Read the "Pilot feedback" section of `PLUGIN/docs/config.md` and tell the parent every point the parents must know before turning it on, in your own words, leaving none out, especially that "just opening the link without submitting still leaves a record". Ask whether they agree. If not, write no `feedback` section, and say they can turn it on later with `/parent-recap:manage`. If they agree, have them paste the whole section the Parent Recap team sent; it isn't a secret. Ask what to use for `household_label` (if the team assigned one, use that), and write the section into the config as is, changing only `household_label`.

Then run `$FB doctor` once more. Fix every ❌, and with pilot feedback on, any ⚠️ on its line.

## 5. First Brief

1. **Preview**, with no email sent and nothing recorded: `$FB bg run --dry-run --lookback-hours 72` (72 hours, since a single day often has nothing new). Show the parent the preview and ask whether anything is off: a wrong Kid, a group or sender that shouldn't be there, something missing. They say it in their own words; fix the config, and show a new preview. Repeat until they're happy.
2. **The real Brief**: `$FB bg run --lookback-hours 72`. It goes to both Recipients.
3. Each run can take several minutes. Never start one while another is going. If your tool cut the real run off, don't run it again: it may still be going and will send the Brief. Wait a few minutes, then check with `$FB setup status` whether it reached every Recipient. A run started while another is going stops at once with "Another FamilyBrief run is still going"; then wait, don't retry.
4. With the partner in another language, the preview shows only the parent's. Ask the parent to have their partner check that their copy came in their language, without a line at the top saying it couldn't be translated.
5. With pilot feedback on, the preview has no ⭐ / ❌. Ask the parent to check the real Brief has a ⭐ and a ❌ next to each Action Item, and one ❌ under the Digest.

## 6. Finish

1. Tell the parent macOS will ask for their Mac password in its own dialog, to wake the Mac 5 minutes before the Brief. Run `$FB schedule install`. If it warns the Mac already has a repeating wake schedule, show the parent what it lists and ask whether to replace it; only if they say yes, run `$FB schedule install --replace-wake`. If it prints a `sudo pmset ...` command, give it to the parent to run in Terminal.
2. Run `$FB setup status`. It reports five outcomes: the program installed, the health check all OK, the first Brief delivered to every Recipient, the nightly job loaded, and the wake schedule set or the Mac never sleeping. Show them as a checklist. For each with `ok: false`, do what its `reason` says, and run it again, until `result` is `done`.
3. The sixth outcome is yours to confirm: after the Mac restarts, such as after a macOS update, someone must log in to this Mac user once, or no Brief comes until they do. Tell the parent, and have them confirm they've got it.

Setup ends only when `setup status` says `done` and the parent has confirmed the restart reminder. Then tell them:

- What time the Brief comes each day
- That on the first nightly run, if macOS asks whether python3.x may access data from other apps, they click Allow
- That for any problem or change later (a new school year, a new group, a skipped Source, another Recipient, Weekend Picks, uninstalling), they type `/parent-recap:manage` or just say what they want
- That the config is in `~/.family/config.yaml`, and the archive of Briefs in `~/FamilyBrief/`
