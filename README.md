# Kinlace Parent Recap

Every evening Parent Recap reads the places your kids' news comes from, has Claude or ChatGPT summarize it, and emails you a Brief in English, Chinese, Finnish or another language, each parent in their own:

- **Gmail**: only mail from the schools, teachers and clubs on your allowlist
- **Wilma**: school messages, announcements and the timetable for the next school day
- **WhatsApp**: the parent, teacher, team and hobby groups you pick (read from the local data of WhatsApp for Mac)
- **MyClub**: the club's training and match calendar

The Brief is grouped by Kid and lists **Notices** (things to know) and **Action Items** (things to do, with due dates). New events come with the email as a calendar attachment you can add with one tap, or can be written straight into Google Calendar. Optional: Weekend Picks, family events in the Helsinki region recommended every Friday.

The whole program runs on your own Mac. Data only moves between this Mac, the AI you chose (Claude or ChatGPT) and your mailbox; there is no other server.

## What you need

- A Mac that is on or asleep around 9 pm (the program wakes it up on schedule). A Mac with Apple Silicon (M1 or later) is recommended. An Intel Mac works for now, on a best-effort basis
- Either a Claude Pro or Max subscription with Claude Code installed, or ChatGPT Plus or higher with the ChatGPT desktop app installed. Install Claude Code with its [native installer](https://code.claude.com/docs/en/setup), which keeps it up to date by itself: paste `curl -fsSL https://claude.ai/install.sh | bash` into Terminal
- [Homebrew](https://brew.sh), which setup uses to install Python 3.11 or later and Node. A Mac comes with only Python 3.9, which is too old, and no Node, so without Homebrew setup stops at its first step
- A Gmail account with two-step verification turned on
- Optional: a Wilma parent account, WhatsApp from the App Store, a MyClub account

## Install

Open Terminal (press ⌘Space, type Terminal and press Enter), paste this line and press Enter. It's the same for every family, with Claude or ChatGPT, and needs no GitHub account:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/kinlace/parent-recap/stable/get.sh)"
```

It downloads the latest stable release into `~/FamilyBrief/plugin`, installs the program from it and opens the setup page in your browser. Keep the Terminal window open until setup is done: the page runs from it, on your Mac only. Once you pick Claude or ChatGPT on the page, it also installs the Parent Recap plugin in Claude Code, or the two Parent Recap skills in Codex, so you can ask for changes in the chat later.

The line fetches [`get.sh`](get.sh) and everything else from the `stable` branch, which only ever holds a tested release. Running it again is safe: it updates what it installed and takes you back to the step setup got to.

### Next

The page walks you through setup in Finnish, English or Chinese: a few choices with defaults, then your Sources one at a time from a list (WhatsApp and MyClub can be skipped and added later), one page of what was found, and a preview of your first Brief before it's sent. Setup downloads about 175 MB, mostly the Python packages the program uses. Passwords go only into the page's own fields, on your Mac, and never into a chat. If you get stuck, **Continue in the chat** on any step hands setup over to Claude Code or Codex at the same step.

Once installed, if something goes wrong or you want to change a setting (a new school year, a different group, another recipient), type `/parent-recap:manage` (`$parent-recap-manage` in Codex), or just say what you want.

### Other ways to install

If you'd rather not paste a line from the internet into Terminal, install by hand. Installed a version before 0.4.0 in Claude Code? It was called Family Brief: first type `/plugin uninstall family-brief@family-brief` and `/plugin marketplace remove family-brief` to remove it. Your settings and Briefs in `~/.family` and `~/FamilyBrief` stay as they are.

In Claude Code, type these one at a time, and choose **user** when `/plugin install` asks for a scope ("project" ties the plugin to the folder Claude Code started in):

```
/plugin marketplace add kinlace/parent-recap#stable
/plugin install parent-recap@kinlace
/parent-recap:setup
```

Or from the package file, for Claude Code or Codex:

1. Download `parent-recap-<version>.zip` from the [latest release](https://github.com/kinlace/parent-recap/releases/latest) (under **Assets**). Safari unzips it by itself into a `parent-recap` folder in Downloads; in another browser, double-click the zip in Finder
2. Move the `parent-recap` folder to `~/FamilyBrief/plugin` (create the FamilyBrief folder in your home folder if it doesn't exist)
3. In Claude Code, type `/plugin marketplace add ~/FamilyBrief/plugin`, `/plugin install parent-recap@kinlace` (choose **user**) and `/parent-recap:setup`. For Codex, run `bash ~/FamilyBrief/plugin/install.sh --codex` in Terminal, then type `$parent-recap-setup` in a new Codex chat

## Updating

- **Installed with the line**: paste it into Terminal again. With Claude Code, also type `/plugin marketplace update kinlace` and `/plugin update parent-recap@kinlace`
- **Installed by hand in Claude Code**: type `/plugin marketplace update kinlace` and `/plugin update parent-recap@kinlace`
- **From the package file**: unzip the zip from the [latest release](https://github.com/kinlace/parent-recap/releases/latest) over `~/FamilyBrief/plugin`, then in Claude Code type `/plugin marketplace update kinlace` and `/plugin update parent-recap@kinlace`, or for Codex run `bash ~/FamilyBrief/plugin/install.sh --codex` in Terminal again

After updating, tell Claude or Codex "upgrade Parent Recap"; it syncs the program and runs a check.

## Uninstalling

Tell Claude or Codex "uninstall Parent Recap". It lists everything setup created on your Mac, asks whether to keep the archive of past Briefs, and removes the rest once you confirm. Your Gmail, Google, Wilma, WhatsApp and MyClub accounts stay as they are. Details are in [Uninstalling](docs/troubleshooting.md#uninstalling).

## Privacy

- Passwords and tokens are stored in the macOS Keychain; the config file `~/.family/config.yaml` is readable only by you
- Gmail only scans senders on your allowlist, and skips mail you sent yourself, drafts, Promotions and Social
- WhatsApp only reads the groups you picked; the database is copied first, read, and the copy deleted
- Every Brief and the raw data it was built from are archived in `~/FamilyBrief/`

The folders `~/FamilyBrief` and `~/.family` and the `family-brief` command keep the product's earlier name, so families who installed before the rename keep their settings, archive and schedule as they are.

## Disclaimer

This is an open-source tool made by parents for their own use. It is not affiliated with or endorsed by Visma (Wilma), Eepos, myClub, Meta (WhatsApp) or Google. Wilma is read through the unofficial, community-maintained [wilma CLI](https://github.com/aikarjal/wilmai), not an interface provided by Visma. If a Source stops working, contact the maintainers of Parent Recap or the wilma CLI, not these companies' customer service.

How data flows, where each password is stored, and how to report a security problem privately: see [SECURITY.md](SECURITY.md). Licensed under MIT, see [LICENSE](LICENSE).

## For maintainers

Run the tests before and after changing code (in the `app/` folder):

```bash
python -m pip install --only-binary :all: -c constraints.txt -e '.[test]'
python -m pytest
```

The tests compare the Brief's text, HTML, `.ics` attachment and the full prompt sent to the model, word for word, against the versions saved in `app/tests/golden/`. If you meant to change the Brief's content or the prompt, run `python -m pytest --update-goldens`, check with `git diff app/tests/golden` that the changes are what you expected, and commit them together. See `app/tests/README.md` for details.

To work through issues labelled `ready-for-agent` unattended, one PR at a time, use `scripts/issue-loop.sh` (macOS, Claude Code). What it needs and how to run it are at the top of that file; try `scripts/issue-loop.sh --dry-run` first.

The Google app file shared with pilot families is not in the repository or the package: the Parent Recap team sends it separately to pilot families who want Google Calendar mode, and `/parent-recap:manage` saves it to `~/.family/calendar_credentials.json`.

To contribute a fix or an idea, see [CONTRIBUTING.md](CONTRIBUTING.md).
