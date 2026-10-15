# Parent Recap on Windows

Parent Recap also runs on a Windows PC instead of a Mac, installed natively, with its own Python and Node (ADR 0019). This page says what it needs there, how to install it, and what it doesn't do on Windows yet. Everything else, such as what the Brief looks like and [What the AI sees](../README.md#what-the-ai-sees), is the same as on a Mac.

## What's supported

| On Windows                  | Supported                                                                                                                                                                                                                                     |
| --------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| The PC                      | Windows 11 on an x64 PC (Intel or AMD). Not Windows 10, whose support Microsoft ended in October 2025 ([Microsoft](https://www.microsoft.com/en-us/windows/end-of-support)), and not Windows on ARM, such as a PC with a Snapdragon processor |
| Signed in at 9 pm           | The family stays signed in to Windows in the evening. Locked, asleep, or with another Windows user signed in on top is fine. Signed out or shut down is not: that night's Brief doesn't go out (ADR 0021)                                     |
| Power                       | Leave the PC plugged in. Whether Windows wakes a sleeping laptop for the evening run on battery hasn't been tested yet                                                                                                                        |
| Waking up                   | The power plan's "Allow wake timers" must be on, so Windows lets the evening run wake the PC. Setup and `doctor` check it, and never change it themselves                                                                                     |
| Restarts for Windows Update | After a restart nobody is signed in, and no Brief goes out until someone is. Turn on "Use my sign-in info to automatically finish setting up after an update" so Windows signs you back in by itself; `doctor` checks it                      |
| Sources                     | Gmail, Wilma and MyClub. Not WhatsApp: Parent Recap reads it from WhatsApp for Mac's own data, which has no counterpart on Windows                                                                                                            |
| The AI                      | Parent Recap's gateway (invited Households) or ChatGPT, through Codex: the setup page installs the Codex CLI with OpenAI's own installer if it isn't there, and you sign in with your ChatGPT account. Not Claude yet                         |
| Calendar events and extras  | New events come as a calendar attachment in the Brief email. Weekend Picks work as on a Mac. Writing events straight into Google Calendar isn't offered on Windows yet: it's turned on in the chat                                            |
| Changes after setup         | Not in a chat yet: the Claude Code and Codex skills that change settings set up a Mac. Ask the Parent Recap team                                                                                                                              |
| Uninstalling                | Not yet. Ask the Parent Recap team                                                                                                                                                                                                            |

## What you need

- A Windows 11 PC as above, signed in with the Windows account the Briefs should run under. No administrator rights are needed
- Either an invitation to Parent Recap's gateway (a Gateway Key from the Parent Recap team) or ChatGPT Plus or higher
- A Gmail account with two-step verification turned on
- Optional: a Wilma parent account, a MyClub account

## Install

Open PowerShell (press the Windows key, type PowerShell and press Enter), paste this line and press Enter:

```powershell
irm https://raw.githubusercontent.com/kinlace/parent-recap/stable/install.ps1 | iex
```

It downloads the latest stable release into `%LOCALAPPDATA%\ParentRecap\plugin`, installs the program from it with its own Python and Node, and opens the setup page in your browser. Keep the PowerShell window open until setup is done: the page runs from it, on your PC only. The page then walks you through setup as on a Mac, without the WhatsApp step.

The line fetches [`install.ps1`](../install.ps1) from the `stable` branch, which only ever holds a tested release. It isn't signed yet, and on a PC with Smart App Control turned on, Windows may block the Python it downloads: Smart App Control blocks an unsigned app it can't vouch for ([Microsoft](https://support.microsoft.com/en-us/windows/security/threat-malware-protection/smart-app-control-frequently-asked-questions)). Running it again is safe: it picks up where it stopped.

Before your first evening, check the two settings above:

- **Allow wake timers**: Control Panel › Hardware and Sound › Power Options › Change plan settings › Change advanced power settings, and under Sleep › Allow wake timers choose Enable
- **Signing back in after an update**: Settings › Accounts › Sign-in options › Use my sign-in info to automatically finish setting up after an update

## Updating

Paste the install line into PowerShell again. It updates what it installed and never touches your settings or passwords.

## Checking how it runs

In PowerShell, run:

```powershell
& "$env:LOCALAPPDATA\ParentRecap\app\.venv\Scripts\parent-recap.exe" doctor
```

It checks the evening run's tasks in Task Scheduler, "Allow wake timers", signing back in after an update, and the stored passwords and AI sign-in, and says what to fix. The evening run's log is `%USERPROFILE%\ParentRecap\logs\run-stderr.log`, and the install's logs are in `%LOCALAPPDATA%\ParentRecap\logs`. Don't paste mail or message text from the logs into a chat.

## Where it keeps things

- `%LOCALAPPDATA%\ParentRecap`: the program, its own Python and Node, the downloaded release and the install's logs
- `%USERPROFILE%\ParentRecap`: every Brief and the messages it was built from, the evening run's logs, and the wilma CLI
- `%USERPROFILE%\.family`: your configuration and what was already handled
- Credential Manager (Control Panel › Credential Manager › Windows Credentials): the Gmail App Password and the Gateway Key, as `Parent Recap/<name>`
- The evening jobs: Task Scheduler, as tasks named `Parent Recap …`

Who can read these on the PC, and how it differs from a Mac, is in [SECURITY.md](../SECURITY.md#on-windows).
