# Security and privacy

FamilyBrief runs on the Household's own Mac. There is no FamilyBrief server: the program reads each Source from that Mac, sends what it read to the model the Household chose, and emails the Brief from the Household's own Gmail. This page lists where data goes and where every secret lives, so you can decide before installing whether to trust it with your Household's messages.

## How data flows

Each evening the scheduled job on your Mac does the following.

### Reading the Sources

| Source                   | How it's read                                                                                                                                                                | What leaves the Mac                                     |
| ------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------- |
| Gmail                    | IMAP to Google, using your App Password. Only senders on your allowlist are fetched; mail you sent yourself, drafts, Promotions and Social are skipped.                      | Nothing new: the mail is already at Google.             |
| Wilma                    | The unofficial community [wilma CLI](https://github.com/aikarjal/wilmai) logs in to your school's Wilma server as you and returns messages, announcements and the timetable. | Your Wilma login, sent by the CLI to the Wilma server.  |
| WhatsApp                 | The local database of WhatsApp for Mac is copied to a temporary folder, only the chats you picked are read, and the copy is deleted.                                         | Nothing: it's never sent to WhatsApp or Meta.           |
| MyClub                   | Your personal calendar subscription link is downloaded.                                                                                                                      | The link, including its personal token, sent to MyClub. |
| Weekend Picks (optional) | Public events are fetched from the Helsinki region's Linked Events API.                                                                                                      | Nothing about your Household.                           |

### Summarizing

Everything read above for that evening, plus your Kids' names and aliases and the Weekend Picks preferences you configured, goes in one prompt to the model you chose:

- **Claude** (`llm.backend: claude`): through the `claude` CLI to Anthropic, under your Claude subscription or API key. Each call runs in an empty temporary folder with no tools, no MCP servers and none of your own Claude settings, so the model can't run commands or read files. The prompt goes to the CLI on standard input, not on its command line, so other accounts can't see it in the process list. The session isn't saved, so nothing is left under `~/.claude/projects`.
- **ChatGPT** (`llm.backend: codex`): through `codex exec` to OpenAI, under your ChatGPT account. Each call runs in an empty temporary folder with a read-only sandbox, with Codex's shell, command execution, apps and plugins turned off, so the model can't run commands or read files. The prompt goes on standard input, the session isn't saved, and your own Codex configuration and MCP tools aren't loaded.

How long Anthropic or OpenAI keep that data, and whether they train on it, is set by your account with them, not by FamilyBrief.

### Delivering

- The Brief is sent through Gmail SMTP from your own address to the recipients you configured. New calendar events go with it as an `.ics` attachment.
- In Google Calendar mode, new events are written to your calendar through the Google Calendar API instead.
- If the pilot feedback links are turned on, clicking ⭐ or ❌ in a Brief opens a pre-filled Google Form owned by the FamilyBrief maintainers. The link carries that item's text, its Source, the model, the date, your Household label and the Kid. Opening the link sends these fields to Google as part of the address, and the maintainers receive them only if you press submit.

### What stays on the Mac

- `~/.family/config.yaml`: your configuration, including Kids' names, allowlisted senders, chosen WhatsApp chats and MyClub links. Setup creates it owner-only (`chmod 600`), inside `~/.family`, which the installer makes owner-only (`chmod 700`).
- `~/.family/state.json`: which messages and events were already handled, and when a Brief last went out to each Recipient's address.
- `~/FamilyBrief/`: every Brief as Markdown, plus the raw messages it was built from (`*.raw.json`), Weekend Picks under `weekend_events/`, and logs and model diagnostics under `~/FamilyBrief/logs/`. These are not encrypted beyond your Mac's own disk encryption.

Everything FamilyBrief writes is owner-only: files `600`, folders `700`, so other accounts on the same Mac, such as a Kid's own account, can't read them. The installer makes `~/FamilyBrief` owner-only, the program and its scheduled jobs create files with umask `077`, and each run takes group and other access off the archive, log, Weekend Picks and `~/.family` folders and the files in them (such as config backups), which also fixes installs from before 0.4.0.

### Where MyClub links can appear

A MyClub calendar link carries a personal token that lets anyone read the Kid's club calendar. The parent pastes it into a macOS dialog with hidden input (`family-brief setup myclub`), or into their own Terminal with the older `scripts/setup_myclub.py`, never into the Claude or Codex chat, and it's kept only in `~/.family/config.yaml` and the config backups next to it in `~/.family`. Each night it's sent to MyClub to download the calendar. When the download fails, `doctor`, the logs, the archive and the Brief name only the MyClub server and the HTTP status, never the link's path or token.

## Where each secret lives

| Secret                                                 | Where                                                                       | Protection                                                                                                                                                                                                                                   |
| ------------------------------------------------------ | --------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Gmail App Password                                     | macOS Keychain, service `family-brief`, account `gmail-imap-<your address>` | Keychain. `family-brief setup gmail` asks for it in a macOS dialog with hidden input (a hidden Terminal prompt without a desktop session, such as over SSH), tests the Gmail sign-in and stores it, so it never reaches the chat, the output, the logs or a command line |
| Claude subscription token                              | macOS Keychain, service `family-brief`, account `claude-oauth-token`        | Keychain. `family-brief setup claude` opens `claude setup-token` in a Terminal window, asks for the token it shows in a macOS dialog with hidden input, makes one test call and stores it, so it never reaches the chat, the output, the logs or a command line. The older `scripts/setup_claude_token.py` has it typed at the Keychain's own prompt in Terminal |
| Anthropic API key (if used instead)                    | macOS Keychain, service `family-brief`, account `anthropic-api-key`         | Keychain. It's typed at the Keychain's own prompt in Terminal, so it never appears on a command line                                                                                                                                        |
| ChatGPT login                                          | `~/.codex`, managed by Codex itself                                         | Codex's own storage                                                                                                                                                                                                                          |
| **Wilma username, password and saved two-step secret** | `~/.config/wilmai/config.json`, written by the wilma CLI                    | **Not encrypted.** The password is only Base64-encoded, so anyone who can read the file has your Wilma login, and two-step verification doesn't stop them if the secret was saved. The only protection is the file's owner-only permissions. |
| MyClub calendar links                                  | `~/.family/config.yaml`                                                     | Owner-only, inside owner-only `~/.family`. `family-brief setup myclub` asks for each Kid's link in a macOS dialog with hidden input (a hidden Terminal prompt without a desktop session), downloads it once and saves it, so it never reaches the chat, the output, the logs or a command line. The older `scripts/setup_myclub.py` has it pasted at a hidden prompt in Terminal |
| Google Calendar OAuth client                           | `~/.family/calendar_credentials.json`                                       | Owner-only, inside owner-only `~/.family`                                                                                                                                                                                                    |
| Google Calendar access token                           | `~/.family/calendar_token.json`                                             | Owner-only, inside owner-only `~/.family`                                                                                                                                                                                                    |

`family-brief uninstall` deletes every secret in this table that Parent Recap stored, but leaves the ChatGPT login (Codex's) and the Wilma login (the wilma CLI's), and never touches your accounts: delete the Gmail App Password and any Google Calendar access in your Google account. What each setup step writes, and how uninstall removes it, is in [docs/setup-internals.md](docs/setup-internals.md).

Keep `~/.family` and `~/.config/wilmai` out of cloud sync, dotfiles repos and backups you share. How to remove the Wilma file when you stop using FamilyBrief, and what to do if it may have leaked, is in [docs/sources.md](docs/sources.md#where-the-wilma-password-is-stored).

## Reporting a security problem

Please don't open a public issue. Report it privately through GitHub: on this repository's **Security** tab, click **Report a vulnerability**. Only the maintainers can see the report. Include what you found, how to reproduce it, and what it exposes. We'll reply there and credit you when the fix is released, unless you'd rather stay anonymous.
