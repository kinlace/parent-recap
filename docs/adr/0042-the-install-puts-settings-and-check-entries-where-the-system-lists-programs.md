# The install puts Settings and Check entries where the system lists programs

A family that set Parent Recap up on its own computer has had two ways back to it: paste the install line into Terminal or PowerShell again, or ask Claude Code or Codex. A tester who isn't technical (2026-11-05) worried she'd have to write that down to find it again, and when something went wrong, opening Terminal to run `doctor` and screenshot it was a chore. So the install now adds two entries, "Parent Recap Settings", which opens the setup page (`parent-recap setup page`), and "Parent Recap Check", which runs `doctor` in a window that stays open for a screenshot. They go where the system lists programs: `~/Applications` on a Mac, where Spotlight and Launchpad find them, and the Start menu on Windows. The install makes them by itself, so setup asks the family for nothing more; uninstall removes them. A Hosted Household gets none: its settings are the maintainers'. Decided 2026-11-06 by Xi and 赵晗博.

## Considered Options

- **Scripts in `~/ParentRecap`.** Rejected: nobody opens that folder, so the family still has to remember where they are.
- **A desktop icon.** Rejected: the desktop is the family's own, and an icon there is the first thing to be tidied away; Spotlight and the Start menu find an entry by name.
- **Only a link in the Brief (#824).** Kept, but a mail link can't open the setup page, which runs on the family's computer only while its server does, so the link leads to instructions. The instructions now start with the Settings entry.

## Consequences

- Install, update, uninstall, `doctor`, the uninstall's list of what it removes and `docs/setup-internals.md` include the two entries.
- Opening an entry must not ask the family for a permission the install didn't need, such as letting one app control Terminal: that would be the extra step this decision avoids.
