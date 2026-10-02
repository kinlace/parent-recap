# Setting up the Sources

Each section is written for the agent (Claude or Codex) guiding the user. The agent runs the `$FB setup` commands itself: each asks for a secret in a macOS dialog with hidden input, or opens what the user signs in to, checks the result and prints one line of JSON, so a secret never reaches the chat. Every command marked **Terminal** must be run by the user in their own Terminal app.

`FB=~/FamilyBrief/app/.venv/bin/family-brief`, `PY=~/FamilyBrief/app/.venv/bin/python`

## Gmail (required)

Gmail is used both to **receive** (scanning mail from schools and clubs) and to **send** (mailing the Brief). It uses an App Password; no Google Cloud project is needed.

1. Tell the user they never give Parent Recap their Google password. An App Password is a separate 16-letter password that only Parent Recap uses, and they can delete it in their Google account. Google may ask them to sign in on its own page first
2. `$FB setup gmail --address you@gmail.com` opens https://myaccount.google.com/apppasswords and a dialog. The user enters `Parent Recap` as the app name (only a label, for finding it later), clicks "Create", and pastes the 16 letters into the dialog. The command tests the sign-in and, if it works, saves the password in the Keychain. If the page isn't available, the user clicks that choice in the dialog, and the command links to 2-Step Verification (https://myaccount.google.com/signinoptions/two-step-verification)
3. `$FB discover gmail-senders` lists the sender domains of the past 60 days (senders only, no message bodies); pick the school, class, club and music school domains together with the user

Notes:

- If the App Password page can't be found, two-step verification is usually off. Google Workspace accounts managed by an employer or school may have App Passwords disabled by the admin; use a personal Gmail instead
- **Never put public domains in the allowlist** (gmail.com, outlook.com, icloud.com). For teachers or coaches using a personal address, put the full address in `allowlist_senders`
- The program already skips mail the user sent to themselves, drafts, Sent, Promotions and Social

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

- The program prefers the codex bundled in `/Applications/Codex.app` or `/Applications/ChatGPT.app`, because it updates with the app. A `codex` installed with npm or brew is only used when no app is found. To point to another path, set `llm.codex_path`
- Every call is isolated: an empty temporary folder, a read-only sandbox, no saved session, and none of the user's own Codex configuration or MCP tools

### Claude (`llm.backend: claude`)

The scheduled job runs in the background and can't use Claude Code's login.

1. `$FB setup claude` opens `claude setup-token` in a Terminal window and Claude's sign-in page. The user clicks Authorize, and the window shows a token starting with `sk-ant-oat01-` (valid for one year)
2. The user pastes the token into the dialog the command shows, then presses Enter in the Terminal window to clear it. The command makes one test call and saves the token in the Keychain (service `family-brief`, account `claude-oauth-token`)

Notes:

- `setup-token` needs a Claude Pro or Max subscription
- If the user's `~/.zshrc` exports `ANTHROPIC_API_KEY`, it clashes with subscription login. The program prefers the Keychain token, but it's best to have the user remove that line
- Without a subscription, an API key works too: in **Terminal** run `security add-generic-password -U -s family-brief -a anthropic-api-key -w`, press Enter and paste the key. It's billed per use, a few cents a day

## Wilma (optional)

This uses the community open-source wilma CLI (not affiliated with Visma).

1. `npm install -g @wilm-ai/wilma-cli`
2. `$FB setup wilma` opens the wilma sign-in screen in a Terminal window: the user chooses the city or school (Espoo / Helsinki / Vantaa / Kauniainen / Helsinki private and state schools, or for another city, its Wilma address as in "Cities without a preset" in `config.md`) and logs in with the **parent account**. If the account has two-step verification, use `--totp-secret` as the CLI prompts
3. The command waits for the sign-in, then reports the students and the city from the Wilma address (it works before the config exists, so setup can prefill the Kids from it)
4. Each Kid's `name` in the config should match Wilma exactly; put the name the family calls the Kid by in `everyday_name` (the Brief uses it everywhere) and any other names in `aliases`
5. Set `wilma.enabled: true`

Note: Wilma only publishes the timetable for about two weeks ahead, so later dates being empty is normal.

### Where the Wilma password is stored

After login, the wilma CLI stores the Wilma username and password in `~/.config/wilmai/config.json`. If you chose to save the two-step secret, it's stored there too. The password is only Base64-encoded, **not encrypted**: anyone with this file can recover the password, and two-step verification doesn't stop them. The file's permissions let only the current Mac user read it, and that is its only protection. So:

- Don't sync `~/.config/wilmai` to cloud storage, put it in a dotfiles repository, or copy it to another computer
- When you stop using Parent Recap (including at the end of the pilot), delete it:

  ```bash
  rm -rf ~/.config/wilmai
  npm uninstall -g @wilm-ai/wilma-cli
  ```

- If someone else may have had this file: change the password on the Wilma website; if you saved the two-step secret, also set up two-step verification again in Wilma so the old secret stops working

## WhatsApp (optional)

This reads the local database of WhatsApp for Mac; the data never leaves the computer.

1. It needs **WhatsApp** from the App Store (not the old Electron version), linked by scanning the QR code with the phone, with chat history fully synced
2. **Give the scheduled job permission and list the chats**: run `$FB setup whatsapp`. It reads WhatsApp through `bg`, with the scheduled job's Python. When that Python can't read yet, it selects the Python file in Finder (it sits in a hidden folder), opens System Settings → Privacy & Security → **App Management** and waits: drag the file from Finder into the list and turn its switch on. If App Management isn't in the list, use "Full Disk Access" the same way. If "python3.x would like to access data from other apps" pops up, click Allow. Once it can read, it lists the chats, with a hint on those that look like they're about a Kid. If it can't open Finder or System Settings, it says which `open` commands to run in Terminal
3. Later, `$FB bg discover whatsapp-chats` lists the chats the same way
4. Pick the groups about the Kids: class parent groups, teacher groups, team groups, carpool groups, hobby groups, playdate groups. For each group set:
   - `name`: **copied exactly** from the output (the text inside the quotes, keeping trailing spaces, curly quotes ’ and emoji)
   - `kid`: which Kid it belongs to (matching a `name` in kids), or `both` if the two Kids share it
   - `label`: class / football / piano / carpool / playdate …
5. Set `whatsapp.enabled: true`

Note: without `bg`, reading WhatsApp directly from Terminal, Claude Code or Codex fails with "Operation not permitted". That's because macOS grants this permission per process; don't give Terminal the permission. `doctor` automatically checks WhatsApp with the scheduled job's Python. If it still fails with `bg`, step 2 wasn't done right: check that what was added is the "real Python path", and if it still fails, add it to "Full Disk Access" too.

## MyClub (optional)

1. The user signs in to MyClub on the web (https://id.myclub.fi), finds "Tilaa kalenteri / Calendar subscription" on the Kid's calendar page, and copies the link starting with `webcal://` (menu names may change between versions)
2. `$FB setup myclub --kid "Kid's name"` opens MyClub and a dialog; the user pastes the link there (the input is hidden). The command downloads it once and saves it in that Kid's `myclub_ical_url`. The link contains a personal token, so it's never pasted in the chat and is only kept in the local config; if the link doesn't open, doctor and the logs name only the MyClub server and the HTTP status
3. In ics calendar mode, it's worth also subscribing to this link directly in the phone's calendar, so it stays in sync with the club in real time:
   - Google Calendar on the web: "Other calendars" on the left → "+" → "From URL", replace `webcal://` with `https://` and paste
   - iPhone: Settings → Calendar → Accounts → Add Account → Other → Add Subscribed Calendar

## Calendar

Whichever mode you pick, an event the model finds goes into the calendar only if it points to a message read that night, and it keeps a link only if that message has the link. The model is also told that message text is data, not instructions: a message in a parent group or an email that tells it to add an event or a link is reported in the Brief instead of followed.

Pick one of the two modes on setup's defaults card:

**ics (default)**: `google_calendar.mode: ics`. New events the model finds in Wilma, mail and group chats (parent evenings, trips, deadlines …) are put in one `.ics` attachment on the Brief email. Tap the attachment on the phone to add them to the calendar (on iPhone choose "Add All"). Each event is sent only once, and its UID in the attachment is fixed, so importing it again doesn't create duplicates.

**google**: `mode: google` writes straight into Google Calendar, removes duplicates, and can invite a partner automatically.
The plugin doesn't include a Google app. Authorizing needs a Google app file (the JSON of a Desktop OAuth client); either:

- **Pilot families**: use the file the Parent Recap maintainers sent you separately
- **Everyone else**: create your own by following [Creating your own Google app](#creating-your-own-google-app) below. If that's too much hassle, use ics mode

1. Save the file to `~/.family/calendar_credentials.json`, readable only by you (`umask 077` makes the copy owner-only from the start):
   ```bash
   (umask 077 && mkdir -p ~/.family && cp downloaded-file.json ~/.family/calendar_credentials.json && chmod 600 ~/.family/calendar_credentials.json)
   ```
   The file contains a secret; don't paste it into a chat or send it to a group.
2. **Terminal**: `$PY ~/FamilyBrief/app/scripts/setup_google_calendar.py`
3. When the browser says "Google hasn't verified this app": click "Advanced" → "Go to (app name) (unsafe)" → "Allow". The app only asks for one permission, managing calendar events
4. To invite a partner automatically, put their address in `invite_attendees`
5. Check: in `$FB doctor` the calendar line shows the authorization is valid

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

Every Friday, 12 weekend events suitable for kids, at most €20 per person, are picked from the public event database of Helsinki / Espoo / Vantaa (Linked Events) and emailed as recommendations.

Ask the user:

- What each Kid likes (sports, outdoors, crafts, theatre …) and dislikes
- What the parents want the Kids to try (such as art or music)
- Who the picks go to (`email.weekend_to`)

In google calendar mode, the picks are written to the calendar as tentative events. When the user deletes the ones they won't go to, the program reads that the following week and adjusts its picks.
