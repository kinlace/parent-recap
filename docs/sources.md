# Setting up the Sources

Each section but the last is written for the agent (Claude or Codex) guiding the user; [Adding a Source](#adding-a-source) is for developers. The agent runs the `$FB setup` commands itself: each asks for a secret in a macOS dialog with hidden input, or opens what the user signs in to, checks the result and prints one line of JSON, so a secret never reaches the chat. Every command marked **Terminal** must be run by the user in their own Terminal app.

`FB=~/ParentRecap/app/.venv/bin/parent-recap`, `PY=~/ParentRecap/app/.venv/bin/python`

## Gmail

Gmail **sends** the Brief, so every Household connects it. It also **receives**: it reads the mail of the senders ticked, for news no other Source brings (a school's or the city's own mail, a club that emails). It uses an App Password; no Google Cloud project is needed.

1. Tell the user they never give Parent Recap their Google password. An App Password is a separate 16-letter password that only Parent Recap uses, and they can delete it in their Google account. Google may ask them to sign in on its own page first
2. `$FB setup gmail --address you@gmail.com` opens https://myaccount.google.com/apppasswords and a dialog. The user enters `Parent Recap` as the app name (only a label, for finding it later), clicks "Create", and pastes the 16 letters into the dialog. The command tests the sign-in and, if it works, saves the password in the Keychain. If the page isn't available, the user clicks that choice in the dialog, and the command links to 2-Step Verification (https://myaccount.google.com/signinoptions/two-step-verification)
3. `$FB discover gmail-senders` lists the sender domains of the past 60 days (senders only, no message bodies); pick the school, class, club and music school domains together with the user. Leave out `myclub.fi` when MyClub is connected and `inschool.fi` when Wilma is: that mail is the news the Source already brings, so the Brief would have it twice

Notes:

- If the App Password page can't be found, two-step verification is usually off. Google Workspace accounts managed by an employer or school may have App Passwords disabled by the admin; use a personal Gmail instead
- **Never put public domains in the allowlist** (gmail.com, outlook.com, icloud.com). For teachers or coaches using a personal address, put the full address in `allowlist_senders`
- The program already skips mail the user sent to themselves, drafts, Sent, Promotions and Social
- With no sender ticked and another Source connected (Wilma, MyClub or WhatsApp), Gmail only sends the Brief: the evening run doesn't read it, and the health check only signs in. With no other Source, a Gmail with no sender fails the health check, since the Brief would be empty

### School mail at another address

The program reads and sends with the one Gmail account above, and can't read a second account. If the school, the teachers or the clubs write to another address (a work address, Outlook, iCloud, another Gmail), the Briefs come out empty until that mail reaches the Gmail account. Either:

- **Forward it automatically** from the other address to the Gmail account. Use the mail service's automatic forwarding setting (in Gmail: Settings → See all settings → Forwarding and POP/IMAP → Add a forwarding address; in Outlook.com: Settings → Mail → Forwarding). Forwarding by hand doesn't work: a forwarded message comes from the parent, not the school, so the allowlist doesn't match it. If the service can forward only some mail, a filter on the school's domains is enough
- **Give the school the Gmail address**: update the contact details in Wilma, if the school lets parents do that there, and tell the teachers and clubs

## AI login (required, pick one)

`llm.backend` in the config decides which AI writes the Brief each night.

### ChatGPT (`llm.backend: codex`)

1. Install the Codex or ChatGPT desktop app, open it and log in to Codex with a ChatGPT account. This needs **ChatGPT Plus or higher**: the Free and Go plans only include Codex in the desktop app, not the command line, and the nightly job uses the command line (`codex exec`). For what each plan includes, see [OpenAI's pricing page](https://learn.chatgpt.com/docs/pricing)
2. No extra token is needed. The scheduled job uses the codex bundled with the app and its saved login (`~/.codex`)
3. `$FB doctor` makes a test call; once the Codex line shows ✅ you're done

Notes:

- The program prefers the codex bundled in `Codex.app` or `ChatGPT.app` (in `/Applications` or `~/Applications`; the ChatGPT app keeps it in `Contents/Resources/codex-cli/bin/codex`), because it updates with the app. A `codex` installed with npm or brew is only used when no app is found. To point to another path, set `llm.codex_path`
- Every call is isolated: an empty temporary folder, a read-only sandbox, no saved session, and none of the user's own Codex configuration or MCP tools

### Claude (`llm.backend: claude`)

The scheduled job runs in the background and can't use Claude Code's login.

1. `$FB setup claude` opens `claude setup-token` in a Terminal window and Claude's sign-in page. The user clicks Authorize, and the window shows a token starting with `sk-ant-oat01-` (valid for one year)
2. The user pastes the token into the dialog the command shows, then presses Enter in the Terminal window to clear it. The command makes one test call and saves the token in the Keychain (service `family-brief`, account `claude-oauth-token`)

Notes:

- `setup-token` needs a Claude Pro or Max subscription
- If the user's `~/.zshrc` exports `ANTHROPIC_API_KEY`, it clashes with subscription login. The program prefers the Keychain token, but it's best to have the user remove that line
- Without a subscription, an API key works too: in **Terminal** run `security add-generic-password -U -s family-brief -a anthropic-api-key -T /usr/bin/security -w`, press Enter and paste the key (`-T /usr/bin/security` lets the evening job read it without a Keychain prompt). It's billed per use, a few cents a day

### Parent Recap's gateway (`llm.backend: gateway`)

For invited Households without a Claude or ChatGPT subscription (ADR 0014). The maintainers send the Household a Gateway Key, starting with `sk-`, over a private channel. Neither Claude Code nor Codex is needed.

1. Before the Household picks it, tell them what [The maintainers' gateway](../SECURITY.md#the-maintainers-gateway) in SECURITY.md says: after the AI filter when it's on, their messages go through a server the maintainers run in the EU, which keeps token counts and cost but not the text
2. `$FB setup gateway` asks for the key in a macOS dialog, makes one small test call through the gateway and saves it in the Keychain (service `family-brief`, account `gateway-key`). On the setup page, the key goes into the AI step's own field instead
3. `$FB doctor` checks that the gateway answers and the key works

Notes:

- Each key is capped at $0.10 a day. A key past its cap is ⚠️ in doctor until the next day, and that night's Brief comes without its Digest
- A key the gateway doesn't know, or that the maintainers turned off, is ❌ in doctor: ask the maintainers for a new one and run `$FB setup gateway` again
- `llm.gateway_url` says where the gateway is, `https://gateway.parentrecap.com` by default

## Wilma (optional)

This uses the community open-source wilma CLI (not affiliated with Visma).

1. The wilma CLI 2.1.2, the version setup is tested with. The setup page and `setup wilma` install it themselves into `~/ParentRecap/wilma`, with Parent Recap's own Node, which the install downloads, so the Mac needs no Node or Homebrew; the CLI always runs on that Node (ADR 0011). Each update of Parent Recap moves a wilma CLI installed there to the version that release pins. One that couldn't be updated, as on a Mac offline, keeps working, and the next update tries again. The setup page then signs in without a Terminal window: the family picks their town from Wilma's list and types the Wilma username and password into the page, which writes the CLI's saved sign-in the way the CLI does and checks it by listing the Kids (ADR 0008). A wrong password is said to be just that; any other failure offers the Terminal window below
2. `$FB setup wilma --language <code>` opens the wilma sign-in screen in a Terminal window, under a short guide in that language (English, Chinese or Finnish; English for any other): the user types the town in Finnish and picks the city or school (Espoo / Helsinki / Vantaa / Kauniainen / Helsinki private and state schools, or for another city, its Wilma address as in "Cities without a preset" in `config.md`), then logs in with the **parent account**, the same username and password as on the Wilma website or app. If the account has two-step verification, use `--totp-secret` as the CLI prompts. Once the CLI has saved the sign-in, the window ends it, so the user doesn't see its student picker or menu: setup reads every Kid anyway
3. The command waits for the sign-in, then reports the students and the city from the Wilma address (it works before the config exists, so setup can prefill the Kids from it)
4. Each Kid's `name` in the config should match Wilma exactly; put the name the family calls the Kid by in `everyday_name` (the Brief uses it everywhere) and any other names in `aliases`
5. Set `wilma.enabled: true`

Note: Wilma only publishes the timetable for about two weeks ahead, so later dates being empty is normal.

The wilma CLI signs in with every sign-in saved in `~/.config/wilmai/config.json`, also one made with a wilma CLI of the family's own, and Parent Recap reads the Kids of each. When a sign-in for none of the Kids stops working, the Brief doesn't say so: `$FB doctor` names it, with the command that removes it.

### Where the Wilma password is stored

After login, the wilma CLI stores the Wilma username and password in `~/.config/wilmai/config.json`. If you chose to save the two-step secret, it's stored there too. The password is only Base64-encoded, **not encrypted**: anyone with this file can recover the password, and two-step verification doesn't stop them. The file's permissions let only the current Mac user read it, and that is its only protection. Next to it, in `wilmai-sessions.json`, the CLI keeps the Wilma session its last command used, for up to six hours, so the evening run signs in to Wilma once rather than for every message: each sign-in also signs the parent out of Wilma in their own browser. A saved session gives the same access as the password while it lasts, and the file has the same protection. So:

- Don't sync `~/.config/wilmai` to cloud storage, put it in a dotfiles repository, or copy it to another computer
- When you stop using Parent Recap (including at the end of the pilot), `parent-recap uninstall` deletes the sign-in setup made, with the sessions the CLI saved, and the wilma CLI setup installed. If you had signed in with a wilma CLI of your own before setup, uninstall leaves that sign-in, and you delete it yourself:

  ```bash
  rm -rf ~/.config/wilmai
  ```

- If someone else may have had this file: change the password on the Wilma website; if you saved the two-step secret, also set up two-step verification again in Wilma so the old secret stops working

## WhatsApp (optional)

This reads the local database of WhatsApp for Mac; the data never leaves the computer.

1. It needs **WhatsApp** from the App Store (not the old Electron version), linked by scanning the QR code with the phone, with chat history fully synced
2. **Give the scheduled job permission and list the chats**: run `$FB setup whatsapp`. It reads WhatsApp through `bg`, with the scheduled job's Python. It first checks, without macOS asking anything, whether that Python has Full Disk Access. Until it does, it selects the Python file in Finder (it sits in a hidden folder), opens System Settings → Privacy & Security → **Full Disk Access** and waits: drag the file from Finder into the list and turn its switch on. WhatsApp isn't read before that, so macOS never asks whether python3.x may access data from other apps; on macOS 26 an Allow there only lasts one read (ADR 0012). Once it has Full Disk Access, it lists the chats, with a hint on those that look like they're about a Kid. If it can't open Finder or System Settings, it says which `open` commands to run in Terminal
3. Later, `$FB bg discover whatsapp-chats` lists the chats the same way
4. Pick the groups about the Kids: class parent groups, teacher groups, team groups, carpool groups, hobby groups, playdate groups. For each group set:
   - `name`: **copied exactly** from the output (the text inside the quotes, keeping trailing spaces, curly quotes ’ and emoji)
   - `kid`: which Kid it belongs to (matching a `name` in kids), or `both` if the two Kids share it
   - `label`: class / football / piano / carpool / playdate …
5. Set `whatsapp.enabled: true`

Note: without `bg`, reading WhatsApp directly from Terminal, Claude Code or Codex fails with "Operation not permitted". That's because macOS grants this permission per process; don't give Terminal the permission. `doctor` automatically checks WhatsApp with the scheduled job's Python. If it still fails with `bg`, step 2 wasn't done right: check that what was added is the "real Python path", and if it still fails, add it to "Full Disk Access" too.

## MyClub (optional)

1. The user signs in to MyClub on the web (https://id.myclub.fi), finds "Tilaa kalenteri / Calendar subscription" on the Kid's calendar page, chooses "Valitut jäsenet / Selected members" under "Jäsenet / Members" with only that Kid ticked, saves, and copies the link starting with `webcal://` (menu names may change between versions). Each Kid needs their own link, since the program attributes every event in a link to the one Kid it is saved for (#207)
2. `$FB setup myclub --kid "Kid's name"` opens MyClub and a dialog; the user pastes the link there (the input is hidden). The command downloads it once and saves it in that Kid's `myclub_ical_url`. The link contains a personal token, so it's never pasted in the chat and is only kept in the local config; if the link doesn't open, doctor and the logs name only the MyClub server and the HTTP status
3. In ics calendar mode, it's worth also subscribing to this link directly in the phone's calendar, so it stays in sync with the club in real time:
   - Google Calendar on the web: "Other calendars" on the left → "+" → "From URL", replace `webcal://` with `https://` and paste
   - iPhone: Settings → Calendar → Accounts → Add Account → Other → Add Subscribed Calendar

## Calendar

Whichever mode you pick, an event the model finds goes into the calendar only if it points to a message read that night, and it keeps a link only if that message has the link. The model is also told that message text is data, not instructions: a message in a parent group or an email that tells it to add an event or a link is reported in the Brief instead of followed.

There are two modes. Setup always starts with ics; a family who wants Google Calendar turns it on later with `/parent-recap:manage`, once they have the Google app file:

**ics (default)**: `google_calendar.mode: ics`. New events the model finds in Wilma, mail and group chats (parent evenings, trips, deadlines …) are put in one `.ics` attachment on the Brief email. Tap the attachment on the phone to add them to the calendar (on iPhone choose "Add All"). Each event is sent only once, and its UID in the attachment is fixed, so importing it again doesn't create duplicates.

**google**: `mode: google` writes straight into Google Calendar, removes duplicates, and can invite a partner automatically.
The plugin doesn't include a Google app. Authorizing needs a Google app file (the JSON of a Desktop OAuth client); either:

- **Pilot families**: use the file the Parent Recap team gives you separately. Until you have it, stay with ics mode
- **Everyone else**: create your own by following [Creating your own Google app](#creating-your-own-google-app) below. If that's too much hassle, use ics mode

1. Save the file to `~/.family/calendar_credentials.json`, readable only by you (`umask 077` makes the copy owner-only from the start):
   ```bash
   (umask 077 && mkdir -p ~/.family && cp downloaded-file.json ~/.family/calendar_credentials.json && chmod 600 ~/.family/calendar_credentials.json)
   ```
   The file contains a secret; don't paste it into a chat or send it to a group.
2. Set `google_calendar.mode: google` in the config, then run `bash PLUGIN/install.sh` again. In google mode it installs Google's packages, which a default install leaves out because only Google Calendar uses them. Every later update keeps them while the mode is google
3. **Terminal**: `$PY ~/ParentRecap/app/scripts/setup_google_calendar.py`
4. When the browser says "Google hasn't verified this app": click "Advanced" → "Go to (app name) (unsafe)" → "Allow". The app only asks for one permission, managing calendar events
5. To invite a partner automatically, put their address in `invite_attendees`
6. Check: in `$FB doctor` the calendar line shows the authorization is valid

### Creating your own Google app

At https://console.cloud.google.com, with your own Google account (menu names may change between versions):

1. Create a project and enable the **Google Calendar API** under "APIs & Services → Library"
2. "Google Auth Platform" (OAuth consent screen): fill in the app name and email, and choose "External" as the user type
3. Under "Data Access", add the scope `https://www.googleapis.com/auth/calendar.events`, and only that one
4. Under "Audience", click **Publish app** so the status becomes "In production". **Don't skip this**: while an external app is in "Testing", Google's authorization expires after 7 days and the calendar has to be authorized again every week. Publishing needs no Google review; authorizing just shows "Google hasn't verified this app"
5. Under "Clients", create an OAuth client of type **Desktop app** and download the JSON file
6. Go back to step 1 of [Calendar](#calendar) above, save the file and authorize

## Weekend Picks (optional, Helsinki, Espoo, Vantaa and Kauniainen only)

Setup doesn't ask about them; the family turns them on later through manage.

Every Friday, 12 weekend events suitable for kids, at most €20 per person, are picked from the public event database of Helsinki / Espoo / Vantaa (Linked Events) and emailed as recommendations. The AI that ranks them gets each Kid as Kid A, Kid B, never their names, and the email and the calendar show each Kid's everyday name again.

Ask the user:

- What each Kid likes (sports, outdoors, crafts, theatre …) and dislikes
- What the parents want the Kids to try (such as art or music)
- Who the picks go to (`email.weekend_to`)

In google calendar mode, the picks are written to the calendar as tentative events. When the user deletes the ones they won't go to, the program reads that the following week and adjusts its picks.

## Adding a Source

Each Source is one record in a registry ([ADR 0022](adr/0022-sources-are-declared-in-one-registry.md)). The evening run, the catch-up lookback, the Brief's coverage line, `doctor` and uninstall go through the records and name no Source themselves. The e2e test modules `test_nightly_new_source` and `test_new_source_doctor_uninstall` register a made-up Source and nothing else, and show what each part of its record reaches. A new Source needs:

1. **Its collector**: a module in `family_brief.collectors` with one function, `collect(cfg, state, kid_terms)`, returning a `Collected` (`family_brief.collectors.base`) with the Source's Messages, its events or both. It reads only its own transport (IMAP, HTTP, a command-line program, a local database) and skips what is older than its lookback
2. **Its config model**: a section of `Config` in `family_brief.config` for what setup saves, or a field on `Kid` when each Kid has their own, as MyClub's link. A section with `lookback_hours` lets the run widen the lookback after nights the Source missed
3. **Its record** in `family_brief.sources`, added to `ALL` at its place in the run's order. Every record has a name, a label in each reviewed language (en, fi, zh), `connected` (whether the Household's config has it read), `skippable` (on the setup page), `connect_rank` (its place on the setup page's list, one of its own) and `collect`. The other fields are optional hooks, each with a default, as `Source` describes them:
   - the run and the Brief: `lookback`, `counts_events` (the coverage line counts its events), `permission_lacked`
   - `macos_only`: off macOS, `doctor` and the setup page leave it out
   - `sender_domain`: the domain of the mail it sends of its own news, which Gmail's sender suggestions leave out once it is connected
   - `doctor`: `check`, and `check_items` when it makes more than one check. Without `check`, `doctor` says nothing of the Source
   - uninstall: `secrets`, `survey` and `notes`, for what setup made for it and what the family removes themselves
   - Held-back Messages ([ADR 0013](adr/0013-the-ai-gets-placeholders-for-third-parties-and-nothing-of-sensitive-messages.md)): `sent_to_everyone`, `checked_body`, `group` and `held_back_link`. Without them, a message is held back when in doubt and listed by sender and subject, with no link
   - the calendar: `in_ics`

   A hook in another module goes through `_in`, which imports it when called

4. **Its transport fake**: a `SourceFake` in the tests' `fake_sources`, listed in `FAKES` under the Source's name; the tests' `sources` fixture refuses to start without one. It serves what a test puts in `sources.tonight[name]` (`harness.sources[name]` in an end-to-end test) the way the transport carries it, so the real collector runs in every test of the evening run
5. **Its setup answers**: a field of `SourcesAnswer` in `family_brief.setup_save`, and the lines there that write it into its config section. The chat and the setup page both save through `setup save`, so they write the same config. A Source that only saves a secret, or a link per Kid as MyClub does, saves it in its own setup module instead
6. **Its setup page panel**, which ADR 0022 leaves out of the registry: each Source's panel differs, so it is written by hand. The page's Source list, its order and its skip button come from the record; the panel is:
   - a phase in `family_brief.setup_server`, listed in its `PHASES`, which answers the panel's calls and saves the Source as done with `context.connect.complete`. A site a panel's button opens is named in `SITES` of `family_brief.setup_server.connect`, since the page names none
   - its `doctor` keys in `HEALTH_CHECKS` of `family_brief.setup_server.health`, so that the check page and Finish name its checks, which they otherwise show as "other"
   - the panel in the page's `index.html`, an element with the id `source-<name>`
   - its entry in the `STEPS` table of the page's `page.js`: what runs as its panel is first shown, drawn again and left, its line on the Working step after Connect, and what the first Brief's progress calls it. Without one the page never shows the Source's panel, and setup can't get past it
   - its text in the page's `text.json`, `source.<name>`, `health.check.<name>` and the panel's own keys, in each of en, fi and zh
7. **Its chat setup**: a `setup <name>` command for the agent, and a section above saying how to guide the user through it
