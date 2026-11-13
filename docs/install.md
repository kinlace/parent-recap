# Installing Parent Recap

The [README](../README.md#install) has the one line to paste. This page says what you need first, what happens after the line, the other ways to install, and how to update and uninstall. On a Windows 11 PC, see [Parent Recap on Windows](windows.md) instead.

## What you need

- A Mac with macOS 13.5 (Ventura) or newer: on an older macOS the install stops and asks you to update macOS or use another computer
- A Mac that is on or asleep around 9 pm (the program wakes it up on schedule). A Mac with Apple Silicon (M1 or later) is recommended. An Intel Mac works for now, on a best-effort basis
- Someone logged in to that Mac in the evening, even with the screen locked. The evening job only runs while you're logged in, and after a restart (a macOS update, a power cut) no Brief goes out until someone logs in again; an SSH login doesn't count. Log in once after each restart, or turn on automatic login: "No Brief after the Mac restarted" in [troubleshooting](troubleshooting.md) says what that means for security
- Every Brief is sent from Parent Recap's own address, `brief@parentrecap.com`, to any mailbox. Setup gets the key for sending by itself, and each person who gets the Brief confirms their address once, through a link in an email from that address. Only if Parent Recap's own AI writes your Brief do you need a Gateway Key from the Parent Recap team, which comes with your invitation. If Claude or ChatGPT writes it and you connected Gmail, you can instead have it sent from your own Gmail once setup is done, so the Brief never passes through Parent Recap's server ([Delivering](../SECURITY.md#delivering))
- Either a Claude Pro or Max subscription with Claude Code installed, or a ChatGPT account on any plan, Free included, with the ChatGPT desktop app installed. Install Claude Code with its [native installer](https://code.claude.com/docs/en/setup), which keeps it up to date by itself: paste `curl -fsSL https://claude.ai/install.sh | bash` into Terminal
- Optional: a Gmail account with two-step verification turned on, only if mail from the school and clubs arrives there. Parent Recap only reads it, from the senders you tick, and sends from it only if you choose that later
- Optional: a Wilma parent account, WhatsApp on your phone, a MyClub account

## The hosted version

No computer that stays on? Ask the maintainers to host Parent Recap for you. They send an Invite Link, and on your phone you sign up with your Wilma login, your Recipients and, if you like, each Kid's MyClub link; the Brief then comes every evening from `brief@parentrecap.com`, written through the maintainers' gateway. You install nothing. Hosted, it reads Wilma and MyClub only, not WhatsApp or Gmail, and the maintainers keep your Wilma login and every Brief on their server until you ask them to remove you. [The hosted version](../SECURITY.md#the-hosted-version) says exactly what they hold and who can read it. We host only the families we invite, and only a few for now.

## What the install line does

It downloads the latest stable release into `~/ParentRecap/plugin`, installs the program from it and opens the setup page in your browser. Keep the Terminal window open until setup is done: the page runs from it, on your Mac only. Once you pick Claude or ChatGPT on the page, it also installs the Parent Recap plugin in Claude Code, or the two Parent Recap skills in Codex, so you can ask for changes in the chat later.

The line fetches [`get.sh`](../get.sh) and everything else from the `stable` branch, which only ever holds a tested release. Running it again is safe: it updates what it installed and takes you back to the step setup got to.

The page walks you through setup in Finnish, English or Chinese: a few choices with defaults, then your Sources one at a time from a list (Wilma and MyClub can be skipped and added later), one page of what was found, and a preview of your first Brief before it's sent. Setup downloads about 230 MB, mostly the Python packages the program uses and its own Python and Node, so you don't need Homebrew, Python or Node of your own. Passwords go only into the page's own fields, on your Mac, and never into a chat. If you get stuck, **Continue in the chat** on any step hands setup over to Claude Code or Codex at the same step.

Once installed, if something goes wrong or you want to change a setting (a new school year, a different group, another recipient), type `/parent-recap:manage` (`$parent-recap-manage` in Codex), or just say what you want. [Setting up the Sources](sources.md) and [Configuration](config.md) describe each setting.

## Other ways to install

If you'd rather not paste a line from the internet into Terminal, install by hand.

In Claude Code, type these one at a time, and choose **user** when `/plugin install` asks for a scope ("project" ties the plugin to the folder Claude Code started in):

```
/plugin marketplace add kinlace/parent-recap#stable
/plugin install parent-recap@kinlace
/parent-recap:setup
```

Or from the package file, for Claude Code or Codex:

1. Download `parent-recap-<version>.zip` from the [latest release](https://github.com/kinlace/parent-recap/releases/latest) (under **Assets**). Safari unzips it by itself into a `parent-recap` folder in Downloads; in another browser, double-click the zip in Finder
2. Move the `parent-recap` folder to `~/ParentRecap/plugin` (create the Parent Recap folder in your home folder if it doesn't exist)
3. In Claude Code, type `/plugin marketplace add ~/ParentRecap/plugin`, `/plugin install parent-recap@kinlace` (choose **user**) and `/parent-recap:setup`. For Codex, run `bash ~/ParentRecap/plugin/install.sh --codex` in Terminal, then type `$parent-recap-setup` in a new Codex chat

## Updating

On a Mac, Parent Recap updates itself ([ADR 0047](adr/0047-a-familys-computer-installs-each-new-release-by-itself-after-the-evening-brief.md)). You don't need to do anything:

- **When.** Each evening, once the Brief is sent or there was nothing to send, it looks which version the `stable` branch holds. A newer release is installed after a later evening's Brief, a day or more after this Mac first saw it. A Brief you start yourself, a preview and Weekend Picks never update.
- **How.** It downloads that release's zip and its signature from this repository's [Releases](https://github.com/kinlace/parent-recap/releases), checks the signature against the keys the installed program holds, installs the release beside the running version, switches to it and runs `parent-recap doctor`. If a check fails that passed before, it switches back to the version before. Your settings, passwords and past Briefs stay as they are, and nothing asks for a password. [How Parent Recap updates itself](../SECURITY.md#how-parent-recap-updates-itself) lists what it downloads, from which hosts and how each download is checked.
- **What you see.** The next Brief says at its top which version Parent Recap now runs, with a link to what changed, or that the update didn't work and the version before still runs. [Updates](troubleshooting.md#updates) says what each line means.
- **A release that needs you.** A release that would ask for the Mac password, a new permission or a QR scan isn't installed by itself. The Brief and the setup page say it is waiting and what it will ask, and you start it yourself, as below.

**Turning it off.** Open Parent Recap Settings (search for it with Spotlight) and turn off the switch on its last step, or type `parent-recap update off` in Terminal; `parent-recap update on` turns it on again. Turned off, Parent Recap still looks once each evening whether a newer release is out and says so in the Brief, once for each release. It downloads nothing.

**Updating by hand.** Press **Update now** in Parent Recap Settings, or type `parent-recap update` in Terminal. It installs the newest release right away, without the day's wait, whether the switch is on or off, and checks it and switches back the same way. Pasting the install line into Terminal again updates too.

What still updates the old way:

- **Windows**: Parent Recap doesn't update itself there yet. Paste the install line into PowerShell again ([On Windows](windows.md#updating))
- **Claude Code's own copy of the plugin**: Claude Code keeps the Parent Recap skills in a copy of its own, which the update doesn't touch, so the skills there can be older than the program. To update them, type `/plugin marketplace update kinlace` and `/plugin update parent-recap@kinlace` in Claude Code. The Codex skills are updated with the program
- **A Parent Recap installed before it could update itself**: paste the install line into Terminal once; from then on it updates itself. `parent-recap doctor` has an Updates line that says whether this install does; a `doctor` without that line is from before
- **Installed from the package file, by hand**: to update by hand without the install line, unzip the zip from the [latest release](https://github.com/kinlace/parent-recap/releases/latest) over `~/ParentRecap/plugin`, then tell Claude or Codex "upgrade Parent Recap"; it syncs the program and runs a check. For Codex, run `bash ~/ParentRecap/plugin/install.sh --codex` in Terminal instead
- **The hosted version**: the maintainers update their server; there is nothing for you to update

### From an older version

Installed a version before 0.4.0 in Claude Code? It was called Family Brief: first type `/plugin uninstall family-brief@family-brief` and `/plugin marketplace remove family-brief` to remove it. Your settings and Briefs in `~/.family` and `~/ParentRecap` stay as they are.

The `family-brief` command still works beside `parent-recap`, and `~/.family` keeps its name. Installed an earlier version, whose folder is `~/FamilyBrief`? Uninstall it with that version first (`~/FamilyBrief/app/.venv/bin/family-brief uninstall`), then paste the install line again: the install line stops and says so.

## Uninstalling

Tell Claude or Codex "uninstall Parent Recap". It lists everything setup created on your Mac, asks whether to keep the archive of past Briefs, and removes the rest once you confirm. Your Gmail, Google, Wilma, WhatsApp and MyClub accounts stay as they are. Details are in [Uninstalling](troubleshooting.md#uninstalling).
