# Kinlace Parent Recap

Every evening Parent Recap reads the places your kids' news comes from, has Claude or ChatGPT summarize it, and emails you a Brief in English, Chinese, Finnish or another language, each parent in their own:

- **Gmail**: only mail from the schools, teachers and clubs on your allowlist
- **Wilma**: school messages, announcements and the timetable for the next school day
- **WhatsApp**: the parent, teacher, team and hobby groups you pick (read from the local data of WhatsApp for Mac)
- **MyClub**: the club's training and match calendar

The Brief is grouped by Kid and lists **Notices** (things to know) and **Action Items** (things to do, with due dates). New events come with the email as a calendar attachment you can add with one tap, or can be written straight into Google Calendar. Optional: Weekend Picks, family events in the Helsinki region recommended every Friday.

The whole program runs on your own Mac. Data only moves between this Mac, the AI you chose (Claude or ChatGPT) and your mailbox; there is no other server.

## What you need

- A Mac that is on or asleep around 9 pm (the program wakes it up on schedule)
- Either a Claude Pro or Max subscription with Claude Code installed, or ChatGPT Plus or higher with the ChatGPT desktop app installed
- A Gmail account with two-step verification turned on
- Optional: a Wilma parent account, WhatsApp from the App Store, a MyClub account

## Install

With Claude Code, use method 1 or 2; with ChatGPT, use method 3. None of them needs a GitHub account. Once installed, all three work the same.

Installed a version before 0.4.0 in Claude Code (from the maintainers' private repository or a zip they sent)? It was called Family Brief. First type `/plugin uninstall family-brief@family-brief` and `/plugin marketplace remove family-brief` to remove it, then install with method 1 or 2. Your settings and Briefs in `~/.family` and `~/FamilyBrief` stay as they are.

### Method 1: Claude Code (later updates take one command)

In Claude Code, type these one at a time:

```
/plugin marketplace add kinlace/parent-recap#stable
/plugin install parent-recap@kinlace
/parent-recap:setup
```

`#stable` means you only ever get tested releases.

### Method 2: Claude Code from the package file

1. Download `parent-recap-<version>.zip` from the [latest release](https://github.com/kinlace/parent-recap/releases/latest) (under **Assets**)
2. Unzip it and move the resulting `parent-recap` folder to `~/FamilyBrief/plugin` (create the FamilyBrief folder if it doesn't exist)
3. In Claude Code, type these one at a time:

```
/plugin marketplace add ~/FamilyBrief/plugin
/plugin install parent-recap@kinlace
/parent-recap:setup
```

### Method 3: Codex (with a ChatGPT account)

1. As in method 2, download the zip from the [latest release](https://github.com/kinlace/parent-recap/releases/latest) and unzip it to `~/FamilyBrief/plugin`
2. Open Terminal and run:

```bash
bash ~/FamilyBrief/plugin/install.sh --codex
```

3. Open Codex, start a new chat and type `$parent-recap-setup`

The Codex install hasn't been fully tested end to end yet; if you get stuck, [open an issue](https://github.com/kinlace/parent-recap/issues).

### Next

After you type `/parent-recap:setup` (`$parent-recap-setup` in Codex), Claude or Codex walks you through setup step by step, which takes about 30–45 minutes. Whenever a password is needed, it asks you to type it in your own Terminal, so it never passes through the chat.

Once installed, if something goes wrong or you want to change a setting (a new school year, a different group, another recipient), type `/parent-recap:manage` (`$parent-recap-manage` in Codex), or just say what you want.

## Updating

- **Method 1**: type `/plugin update parent-recap@kinlace`
- **Method 2**: unzip the zip from the [latest release](https://github.com/kinlace/parent-recap/releases/latest) over `~/FamilyBrief/plugin`, then in Claude Code type `/plugin marketplace update kinlace` and `/plugin update parent-recap@kinlace`
- **Codex**: unzip the zip from the [latest release](https://github.com/kinlace/parent-recap/releases/latest) over `~/FamilyBrief/plugin`, then in Terminal run `bash ~/FamilyBrief/plugin/install.sh --codex` again

After updating, tell Claude or Codex "upgrade Parent Recap"; it syncs the program and runs a check.

## Privacy

- Passwords and tokens are stored in the macOS Keychain; the config file `~/.family/config.yaml` is readable only by you
- Gmail only scans senders on your allowlist, and skips mail you sent yourself, drafts, Promotions and Social
- WhatsApp only reads the groups you picked; the database is copied first, read, and the copy deleted
- Every Brief and the raw data it was built from are archived in `~/FamilyBrief/`

## Disclaimer

This is an open-source tool made by parents for their own use. It is not affiliated with or endorsed by Visma (Wilma), Eepos, myClub, Meta (WhatsApp) or Google. Wilma is read through the unofficial, community-maintained [wilma CLI](https://github.com/aikarjal/wilmai), not an interface provided by Visma. If a Source stops working, contact the maintainers of FamilyBrief or the wilma CLI, not these companies' customer service.

How data flows, where each password is stored, and how to report a security problem privately: see [SECURITY.md](SECURITY.md). Licensed under MIT, see [LICENSE](LICENSE).

## For maintainers

Run the tests before and after changing code (in the `app/` folder):

```bash
python -m pip install -e '.[test]'
python -m pytest
```

The tests compare the Brief's text, HTML, `.ics` attachment and the full prompt sent to the model, word for word, against the versions saved in `app/tests/golden/`. If you meant to change the Brief's content or the prompt, run `python -m pytest --update-goldens`, check with `git diff app/tests/golden` that the changes are what you expected, and commit them together. See `app/tests/README.md` for details.

To work through issues labelled `ready-for-agent` unattended, one PR at a time, use `scripts/issue-loop.sh` (macOS, Claude Code). What it needs and how to run it are at the top of that file; try `scripts/issue-loop.sh --dry-run` first.

The Google app file shared with pilot families is not in the repository or the package: the maintainers send it separately to pilot families who use Google Calendar mode, and setup saves it to `~/.family/calendar_credentials.json`.

To contribute a fix or an idea, see [CONTRIBUTING.md](CONTRIBUTING.md).
