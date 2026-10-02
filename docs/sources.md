# Setting up the Sources

Each section is written for the agent (Claude or Codex) guiding the user. Every command marked **Terminal** must be run by the user in their own Terminal app, not by the agent, because these commands either need hidden input or need macOS permissions granted to Terminal itself.

`FB=~/FamilyBrief/app/.venv/bin/family-brief`, `PY=~/FamilyBrief/app/.venv/bin/python`

## Gmail (required)

Gmail is used both to **receive** (scanning mail from schools and clubs) and to **send** (mailing the Brief). It uses an App Password; no Google Cloud project is needed.

1. Check that the account has two-step verification on: https://myaccount.google.com/security → "2-Step Verification"
2. Open https://myaccount.google.com/apppasswords, enter `FamilyBrief` as the app name and click "Create"; you get a 16-character password
3. **Terminal**: `$PY ~/FamilyBrief/app/scripts/setup_gmail_imap.py you@gmail.com`, then paste the 16 characters (the input is hidden). The script tests the login and, if it works, saves the password in the Keychain
4. Back in the agent: `$FB discover gmail-senders` lists the sender domains of the past 60 days (senders only, no message bodies); pick the school, class, club and music school domains together with the user

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

1. **Terminal**: `claude setup-token`, authorize in the browser when prompted; Terminal prints a token starting with `sk-ant-oat01-` (valid for one year)
2. **Terminal**: `$PY ~/FamilyBrief/app/scripts/setup_claude_token.py`, then paste the token at each of the Keychain's two prompts (input is hidden). The script saves it in the Keychain (service `family-brief`, account `claude-oauth-token`) and makes one test call

Notes:

- `setup-token` needs a Claude Pro or Max subscription
- If the user's `~/.zshrc` exports `ANTHROPIC_API_KEY`, it clashes with subscription login. The program prefers the Keychain token, but it's best to have the user remove that line
- Without a subscription, an API key works too: in **Terminal** run `security add-generic-password -U -s family-brief -a anthropic-api-key -w`, press Enter and paste the key. It's billed per use, a few cents a day

## Wilma (optional)

This uses the community open-source wilma CLI (not affiliated with Visma).

1. `npm install -g @wilm-ai/wilma-cli`
2. **Terminal**: `wilma` opens an interactive screen: choose the city or school (Espoo / Helsinki / Vantaa / Kauniainen / Helsinki private and state schools, or for another city, its Wilma address as in "Cities without a preset" in `config.md`) and log in with the **parent account**. If the account has two-step verification, use `--totp-secret` as the CLI prompts
3. Back in the agent: `$FB discover wilma-students` lists the students (it works before the config exists, so setup can prefill the Kids from it)
4. Each Kid's `name` in the config should match Wilma exactly; put the name the family calls the Kid by in `everyday_name` (the Brief uses it everywhere) and any other names in `aliases`
5. Set `wilma.enabled: true`

Note: Wilma only publishes the timetable for about two weeks ahead, so later dates being empty is normal.

### Where the Wilma password is stored

After login, the wilma CLI stores the Wilma username and password in `~/.config/wilmai/config.json`. If you chose to save the two-step secret, it's stored there too. The password is only Base64-encoded, **not encrypted**: anyone with this file can recover the password, and two-step verification doesn't stop them. The file's permissions let only the current Mac user read it, and that is its only protection. So:

- Don't sync `~/.config/wilmai` to cloud storage, put it in a dotfiles repository, or copy it to another computer
- When you stop using FamilyBrief (including at the end of the pilot), delete it:

  ```bash
  rm -rf ~/.config/wilmai
  npm uninstall -g @wilm-ai/wilma-cli
  ```

- If someone else may have had this file: change the password on the Wilma website; if you saved the two-step secret, also set up two-step verification again in Wilma so the old secret stops working

## WhatsApp (optional)

This reads the local database of WhatsApp for Mac; the data never leaves the computer.

1. It needs **WhatsApp** from the App Store (not the old Electron version), linked by scanning the QR code with the phone, with chat history fully synced
2. **Give the scheduled job permission**: run `$FB app-management`. It selects the scheduled job's Python file in Finder (it sits in a hidden folder) and opens System Settings → Privacy & Security → **App Management**. Drag the file from Finder into the list and turn its switch on. If App Management isn't in the list, use "Full Disk Access" the same way. If the command can't open Finder or System Settings, it prints the two `open` commands to run in Terminal
3. Run `$FB bg discover whatsapp-chats`; Claude or Codex can run this itself. `bg` reads in the background with the scheduled job's Python, so if step 2 was done right, this works. If "python3.x would like to access data from other apps" pops up, click Allow
4. Pick the groups about the Kids: class parent groups, teacher groups, team groups, carpool groups, hobby groups, playdate groups. For each group set:
   - `name`: **copied exactly** from the output (the text inside the quotes, keeping trailing spaces, curly quotes ’ and emoji)
   - `kid`: which Kid it belongs to (matching a `name` in kids), or `both` if the two Kids share it
   - `label`: class / football / piano / carpool / playdate …
5. Set `whatsapp.enabled: true`

Note: without `bg`, reading WhatsApp directly from Terminal, Claude Code or Codex fails with "Operation not permitted". That's because macOS grants this permission per process; don't give Terminal the permission. `doctor` automatically checks WhatsApp with the scheduled job's Python. If it still fails with `bg`, step 2 wasn't done right: check that what was added is the "real Python path", and if it still fails, add it to "Full Disk Access" too.

## MyClub (optional)

1. The user logs in to MyClub on the web (https://id.myclub.fi), finds "Tilaa kalenteri / Calendar subscription" on the Kid's calendar page, and copies the link starting with `webcal://` (menu names may change between versions)
2. **Terminal**: `$PY ~/FamilyBrief/app/scripts/setup_myclub.py "Kid's name"`, then paste the link (the input is hidden). The script tests the link and saves it in that Kid's `myclub_ical_url`. The link contains a personal token, so it's never pasted in the chat and is only kept in the local config; if the link doesn't open, doctor and the logs name only the MyClub server and the HTTP status
3. In ics calendar mode, it's worth also subscribing to this link directly in the phone's calendar, so it stays in sync with the club in real time:
   - Google Calendar on the web: "Other calendars" on the left → "+" → "From URL", replace `webcal://` with `https://` and paste
   - iPhone: Settings → Calendar → Accounts → Add Account → Other → Add Subscribed Calendar

## Calendar

Whichever mode you pick, an event the model finds goes into the calendar only if it points to a message read that night, and it keeps a link only if that message has the link. The model is also told that message text is data, not instructions: a message in a parent group or an email that tells it to add an event or a link is reported in the Brief instead of followed.

Pick one of the two modes at step 9 of setup:

**ics (default)**: `google_calendar.mode: ics`. New events the model finds in Wilma, mail and group chats (parent evenings, trips, deadlines …) are put in one `.ics` attachment on the Brief email. Tap the attachment on the phone to add them to the calendar (on iPhone choose "Add All"). Each event is sent only once, and its UID in the attachment is fixed, so importing it again doesn't create duplicates.

**google**: `mode: google` writes straight into Google Calendar, removes duplicates, and can invite a partner automatically.
The plugin doesn't include a Google app. Authorizing needs a Google app file (the JSON of a Desktop OAuth client); either:

- **Pilot families**: use the file the FamilyBrief maintainers sent you separately
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

## Weekend Picks (optional, Helsinki, Espoo and Vantaa only)

Every Friday, 12 weekend events suitable for kids, at most €20 per person, are picked from the public event database of Helsinki / Espoo / Vantaa (Linked Events) and emailed as recommendations.

Ask the user:

- What each Kid likes (sports, outdoors, crafts, theatre …) and dislikes
- What the parents want the Kids to try (such as art or music)
- Who the picks go to (`email.weekend_to`)

In google calendar mode, the picks are written to the calendar as tentative events. When the user deletes the ones they won't go to, the program reads that the following week and adjusts its picks.
