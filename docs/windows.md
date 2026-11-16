# Parent Recap on Windows

Parent Recap also runs on a Windows PC instead of a Mac, installed natively, with its own Python and Node (ADR 0019). This page says what it needs there, how to install it, and what it doesn't do on Windows yet. Everything else, such as what the Brief looks like and [What the AI sees](../README.md#what-the-ai-sees), is the same as on a Mac.

## What's supported

| On Windows                  | Supported                                                                                                                                                                                                                                                                                                                                                                                                                 |
| --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| The PC                      | Windows 11 on an x64 PC (Intel or AMD). Not Windows 10, whose support Microsoft ended in October 2025 ([Microsoft](https://www.microsoft.com/en-us/windows/end-of-support)), and not Windows on ARM, such as a PC with a Snapdragon processor                                                                                                                                                                             |
| Signed in at 9 pm           | The family stays signed in to Windows in the evening. Locked, asleep, or with another Windows user signed in on top is fine. If nobody is signed in at Brief time, signed out or shut down, the Brief comes when someone next signs in within 10 hours of the Brief time (until 07:00 for 21:00), saying at the top that it was sent late. Later than that, the next evening's Brief covers the missed evening (ADR 0021) |
| Power                       | Leave the PC plugged in. Whether Windows wakes a sleeping laptop for the evening run on battery hasn't been tested yet                                                                                                                                                                                                                                                                                                    |
| Waking up                   | The power plan's "Allow wake timers" must be on, so Windows lets the evening run wake the PC. Setup and `doctor` check it, and never change it themselves                                                                                                                                                                                                                                                                 |
| Restarts for Windows Update | After a restart nobody is signed in, and the Brief waits until someone is, up to 10 hours. Turn on "Use my sign-in info to automatically finish setting up after an update" so Windows signs you back in by itself; `doctor` checks it                                                                                                                                                                                    |
| Sources                     | The same as on a Mac: Gmail, Wilma, MyClub and WhatsApp, which is read as a Linked Device, paired on the setup page                                                                                                                                                                                                                                                                                                       |
| The AI                      | Parent Recap's gateway (invited Households) or ChatGPT, through Codex: the setup page installs the Codex CLI with OpenAI's own installer if it isn't there, and you sign in with your ChatGPT account. Or Claude, through Claude Code: the setup page installs it with Anthropic's own installer if it isn't there, opens a PowerShell window running `claude setup-token`, and you paste the token into the page         |
| Calendar events and extras  | New events come as a calendar attachment in the Brief email. Weekend Picks work as on a Mac. Writing events straight into Google Calendar isn't offered on Windows yet: it's turned on in the chat                                                                                                                                                                                                                        |
| Changes after setup         | To add a Source you skipped, such as Wilma or MyClub, paste the [install line](#install) again: the setup page opens at Finish, where "Add a Source you skipped" leads back to the list of Sources. Anything else isn't in a chat yet, since the Claude Code and Codex skills that change settings set up a Mac: ask the Parent Recap team                                                                                |
| Uninstalling                | See [Uninstalling](#uninstalling)                                                                                                                                                                                                                                                                                                                                                                                         |

## What you need

- A Windows 11 PC as above, signed in with the Windows account the Briefs should run under. No administrator rights are needed
- Every Brief is sent from Parent Recap's own address, `brief@parentrecap.com`, to any mailbox. Setup gets the key for sending by itself, and each person who gets the Brief confirms their address once, through a link in an email from that address. To have Parent Recap's gateway write the Brief too, you need a Gateway Key from the Parent Recap team, which comes with your invitation; otherwise you need a ChatGPT account on any plan, Free included
- Optional: a Gmail account with two-step verification turned on, only if mail from the school and clubs arrives there. Parent Recap only reads it, from the senders you tick, and sends from it only if you choose that later (ADR 0034)
- Optional: a Wilma parent account, a MyClub account

## Install

Open PowerShell (press the Windows key, type PowerShell and press Enter), paste this line and press Enter:

```powershell
irm https://raw.githubusercontent.com/kinlace/parent-recap/stable/install.ps1 | iex
```

It downloads the latest stable release into `%LOCALAPPDATA%\ParentRecap\plugin`, installs the program from it with its own Python and Node, and opens the setup page in your browser. Keep the PowerShell window open until setup is done: the page runs from it, on your PC only. The page then walks you through setup as on a Mac.

The line fetches [`install.ps1`](../install.ps1) from the `stable` branch, which only ever holds a tested release. It isn't signed yet, and on a PC with Smart App Control turned on, Windows may block the Python it downloads: Smart App Control blocks an unsigned app it can't vouch for ([Microsoft](https://support.microsoft.com/en-us/windows/security/threat-malware-protection/smart-app-control-frequently-asked-questions)). Running it again is safe: it picks up where it stopped.

Before your first evening, check the two settings above:

- **Allow wake timers**: Control Panel › Hardware and Sound › Power Options › Change plan settings › Change advanced power settings, and under Sleep › Allow wake timers choose Enable
- **Signing back in after an update**: Settings › Accounts › Sign-in options › Use my sign-in info to automatically finish setting up after an update

## Updating

Parent Recap updates itself on a Windows PC as it does [on a Mac](install.md#updating) (ADR 0052): after the evening Brief, a day or more after the PC first saw a new release, it downloads the release, checks its signature with Windows' own `ssh-keygen`, installs it beside the version you have, switches to it and runs `parent-recap doctor`, and switches back if a check fails that passed before or the install didn't finish. Parent Recap Settings has the switch that turns it off and on, and **Update now** once a newer release is out; `parent-recap update off` and `parent-recap update on` in PowerShell do the same, and `parent-recap update` installs the newest release now. The Updates line of `parent-recap doctor` shows whether updating is on, the installed version, and an update that waits for you or failed. Adding a school system or club that changed after your version updates Parent Recap first, on the setup page and in PowerShell, as on a Mac. It starts no PowerShell and runs no `.ps1` file: the release's own installer is a Python program, run with Parent Recap's own Python. If something on the PC stops a step, such as a company's Defender rule, the version you have keeps running, and `%LOCALAPPDATA%\ParentRecap\logs\update.log` names the step and the error Windows gave.

Paste the install line into PowerShell again instead when:

- Parent Recap was installed before it could update itself: once, and from then on it updates itself
- The PC has no `ssh-keygen` in `C:\Windows\System32\OpenSSH` (someone removed Windows' OpenSSH client): then it can't check a release's signature, so it doesn't update itself, and `parent-recap doctor` says so

The install line updates what it installed and never touches your settings or passwords. It too installs the new version beside the one you have and switches to it only once it finished, keeping the version before: if the new one turns out bad, `parent-recap switch-back` runs the version before again, and running it again switches forward. If the install says another program uses Parent Recap's folder, close Parent Recap Settings and any window that runs Parent Recap, then paste the line again.

## Checking how it runs

In a new PowerShell window, run:

```powershell
parent-recap doctor
```

If PowerShell doesn't know `parent-recap` (an install from before 0.9.0, or a window opened before the install), paste the install line again, or use its full path: `& "$env:LOCALAPPDATA\ParentRecap\app\.venv\Scripts\python.exe" -m family_brief doctor`.

It checks the evening run's tasks in Task Scheduler, "Allow wake timers", signing back in after an update, and the stored passwords and AI sign-in, and says what to fix. The evening run's log is `%USERPROFILE%\ParentRecap\logs\run-stderr.log`, and the install's logs are in `%LOCALAPPDATA%\ParentRecap\logs`. Don't paste mail or message text from the logs into a chat.

## Uninstalling

In PowerShell, run:

```powershell
parent-recap uninstall
```

It lists everything setup created on the PC, asks whether to keep the archive of past Briefs, and removes the rest once you confirm: the tasks in Task Scheduler, the `Parent Recap/…` credentials in Credential Manager, the `ParentRecap\bin` entry in your PATH, and the folders below. The program itself goes last, just after uninstall ends. Your Gmail, Wilma and MyClub accounts stay as they are: delete the Gmail App Password yourself in your Google account.

## Where it keeps things

- `%LOCALAPPDATA%\ParentRecap`: the program, its own Python and Node, the downloaded release and the install's logs, and `bin`, which holds only the `parent-recap` command and is on your PATH
- `%USERPROFILE%\ParentRecap`: every Brief and the messages it was built from, the evening run's logs, and the wilma CLI
- `%USERPROFILE%\.family`: your configuration and what was already handled
- Credential Manager (Control Panel › Credential Manager › Windows Credentials): the Gmail App Password, the Gateway Key and the Claude token, as `Parent Recap/<name>`
- The evening jobs: Task Scheduler, as tasks named `Parent Recap …`

Who can read these on the PC, and how it differs from a Mac, is in [SECURITY.md](../SECURITY.md#on-windows).
