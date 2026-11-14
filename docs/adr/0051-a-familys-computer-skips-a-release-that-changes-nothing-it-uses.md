---
status: accepted
---

# A family's computer skips a release that changes nothing it uses

Decided by Xi on 2026-11-14 for #947.

Every release goes to every Household. A fix to Konnect for a test family in the Netherlands (ADR 0050) is a release that every Finnish family's computer installs too, by itself and with nobody watching (ADR 0047), though nothing it runs has changed. The test families who report a problem still need the fix the same evening, so releasing less often isn't the answer. Instead a release stays what it is, one signed zip under one version, and says which of its parts changed in `release.json`, the file of Parent Recap's own that already says whether a release needs the family (ADR 0047). It gains the release's version and `parts`: for the core and for each Source, the last version that changed it. The file is in the tree, so it is on `stable` and inside the signed zip. A family's computer reads that one file on `stable` after the evening Brief is sent, in place of the version in Claude Code's `.claude-plugin/plugin.json`, and installs the release only when the core, or a Source the Household has connected, changed in a version newer than the one installed. Otherwise it stays on its version and the Brief says nothing of a newer one.

For that file to say anything, a Source has to be code a release can change without touching the rest. Today it isn't: of the 25 releases from 0.7.3 to 0.19.0, one changed only a Source's own files, and adding Konnect and Social Schools in 0.19.0 also changed the config, the registry, the setup steps, the setup page's script and text, the secret store and uninstall. So each Source moves into a folder of its own, `sources/<group>/<name>`, holding its collector, config model, setup step, texts, `doctor` checks and what uninstall removes, and the shared code finds it only through the registry (ADR 0022). The groups are `fi`, `nl` and `shared`, the last for the Sources no country owns: Gmail, WhatsApp, Local Files and the weekend events.

`parts` is worked out from the paths that changed since the release before, never written by hand. A script writes it in the release's pull request, and `scripts/release.py` works it out again when it builds the zip and refuses a tag whose file says otherwise, as it refuses one that lowered what the file says of needing the family. A path inside a Source's folder is that Source's. A short list of paths counts for nothing, because no family's computer runs them or because every release changes them: the documents, the tests, the workflows, `CHANGELOG.md`, the lines that hold the version, and `release.json` itself. Every other path, the pinned packages and the installer among them, is the core's.

## Considered Options

- **Sources as packages with versions of their own, which the core plugs in.** Rejected for now: each package would be one more download to sign and check, where ADR 0047 has one zip from one address, and a table of which core runs with which Source would have to be kept and tested. The interface between them isn't settled either, since the registry's record still grows with most new Sources. The folders and the file are what such a split would start from, so nothing here is lost if a Source is ever written outside this repository.
- **Move `stable` once a week.** Rejected: a test family that reports a problem gets the fix the evening it is released, and "Update now" on the setup page reads `stable` too.
- **Two channels, a fast one for test families and a slow one for the rest.** Not now: every installed Household is a test family. It can be added later beside this, when there are Households that aren't.
- **A file of its own for `parts`.** Rejected: `release.json` is already Parent Recap's, already in the tree and the zip, and with the version in it the evening's look stays one request.
- **One package per country.** Rejected as the unit: a Wilma fix would still reach a Finnish family that only has MyClub. Countries are how the folders are grouped, and the file is per Source.
- **A maintainer writes in each release which Sources it changed.** Rejected: it would be wrong some evening. The paths are what changed.
- **Read the file only from the zip, where the signature covers it.** Rejected: the computer would download every release to learn it didn't need it. The copy on `stable` is unsigned, as the version there always was. A false one can make a computer skip a release, which holding back `stable` already can, or download one, whose signature is then checked as before.
- **Compare and update when the evening run starts.** Rejected: a release that fails would cost that evening's Brief. The update stays after the Brief is sent (ADR 0047).

## Consequences

- Households no longer run the same version. A Finnish Household can stay several releases behind without missing anything it runs, so "which version are you on" stops meaning "which fixes do you have"; the Run Report's version (ADR 0048) is read with the file beside it.
- A path nobody thought about counts as the core's, so the mistake a new file can make is one install too many. The list of paths that count for nothing is the one place an install too few can come from, so it is short, written in the script and tested. A file that can't be read, that has no `parts`, or that doesn't name a Source the Household has, means the release is installed as it is today.
- A Household that adds a Source later may hold an old copy of it. Adding a Source on the setup page or from a terminal updates Parent Recap first when that Source changed after the installed version.
- A Household with updates turned off is told in the Brief of a newer release only when it changes something the Household uses.
- The evening's look is still one request to the same address, now for `release.json`, and `SECURITY.md` names it. A version installed before this keeps reading `.claude-plugin/plugin.json`, which keeps its version for Claude Code.
- The hosted server keeps deploying every release (ADR 0045), and Claude Code's own copy of the plugin still follows every version on `stable` (ADR 0002). Windows has no background update to skip (ADR 0047).
- A change to shared code still reaches every Household. Until a Source is in its folder nearly every change to it is one, so the Sources move in this order: Konnect and Social Schools first, which have no step on the setup page and are what the Finnish families should stop installing; then the Python of Eepos, eVaka and MyClub; then the setup page and the hosted sign-up page, whose script, markup and texts are one file each today and are split by Source, a change to them counting as the core's until then. Wilma, WhatsApp and Gmail come last or not at all, since nearly every Household has them.
