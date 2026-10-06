# Parent Recap replaces FamilyBrief in text, folders and jobs, and three internal names stay

Families know the product as Parent Recap, but the program still says FamilyBrief in the folder they open in Finder (`~/FamilyBrief`), in the launchd jobs (`com.family.brief`, `com.family.weekend-events`, `com.family.bg.*`) and in a lot of text. We rename these now, while the only installs are Xi Xiao's two Macs and Zhao's test account, because every family that installs later would need a move. Docs, comments, log lines and hints say Parent Recap, a new `parent-recap` command sits beside `family-brief` (which keeps working), and the folder, the jobs and the `FAMILY_BRIEF_HOME` and `FAMILY_BRIEF_BG` variables get new names. An install that predates the change is uninstalled with the version that made it and installed again, with no migration code. `get.sh` stops when it finds the old folder or job and says so.

Three names stay, and so does `~/.family`:

- **The Keychain service `family-brief`.** Renaming it moves every secret and risks the macOS prompt that ADR 0009 fixed.
- **The Google Calendar property `family_brief` and `family_brief_hash`, the `.ics` UID suffix `@family-brief` and its PRODID.** Renaming them stops the program from recognising events already in a family's calendar, so they would appear twice. Neither Google Calendar's web page nor its phone app shows them.
- **The Python package `family_brief`.** It is internal, no family sees it, renaming it touches every module and test, and the launchd jobs start the program as `-m family_brief`.

`~/.family` stays because Zhao's own daily-brief shares it.

## Considered Options

- **Migrate installs in place.** Move the folder, rebuild the venv (it holds absolute paths), reinstall the jobs, rewrite the install record and Codex's paths. Every step is a way to lose an evening Brief, and it would run on three Macs.
- **Rename the three as well.** With uninstall and reinstall nothing would need moving, and it leaves one name to maintain. We left them because families never see them, and a family that has the Keychain item or the calendar events pays for the change later.

## Consequences

- Parent Recap's jobs no longer share a label with Zhao's daily-brief, which keeps `com.family.brief`. They still share `~/.family` and the Keychain service, so uninstall must still not run on his main account.
- The Brief's own text names the archive folder, so the en, zh and fi program text changes in the folder name only.
