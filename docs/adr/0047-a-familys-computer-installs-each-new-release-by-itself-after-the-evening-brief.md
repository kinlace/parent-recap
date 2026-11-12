# A family's computer installs each new release by itself after the evening Brief

A Household on its own computer got a new release only when someone pasted the install line again, which the families we build for don't do, so they stayed on old versions while Hosted Households got each release the next morning (ADR 0045). Now the program updates itself: once the evening Brief is sent, it reads the version `stable` holds (ADR 0002), and when that release is newer than its own and at least a day old, it downloads that tag's release zip, checks its signature against a public key that ships inside the installed program, installs it beside the running copy, switches to it and runs `doctor`; when `doctor` fails it switches back. The next Brief says at its top which version it now runs, or that the update failed. It is on for every Household, new or already installed, and the setup page says so with a switch to turn it off. Decided by Xi on 2026-11-12, for the Mac first.

A program that downloads and runs code in the background is what malware does, and many families have a coding agent read the program before they trust it. So the update is built to be read: it is stated on the setup page, in the README and in `SECURITY.md`; it downloads only from this repository's releases, an address the program holds and nothing remote can change; no remote script is piped into a shell; it adds no background job of its own, asks for no password, and writes only inside Parent Recap's folder; the check sends nothing about the Household.

## Considered Options

- **Tell the family and let them press a button.** Rejected as the default: a parent who isn't technical leaves it, and the Household stays behind. It remains the path for a release that needs the family (below).
- **Off until the family turns it on.** Rejected: most Households would never turn it on. Shown with a switch at setup, it is a stated choice, not a hidden one.
- **Announce it to installed Households first and start one release later.** Rejected by Xi: there are very few of them.
- **Download the `stable` branch, as the install line does.** Rejected: a branch can change between the check and the download, and it carries no signature. A tag's release zip is one fixed file.
- **Check a SHA-256 only.** Rejected: the checksum comes from the same place as the zip, so it catches a broken download and not a changed release.
- **Keep the signing key on a maintainer's device.** Rejected for now: every release, and the server's deploy the next morning, would wait for a signature made by hand. The key is a secret of the release workflow, in a protected environment.
- **A scheduled job of its own, in the morning as on the server.** Rejected: the evening Brief is the one time the computer is known to be awake (it is woken for it), and a new background item is one more thing for macOS and a reader of the code to ask about.

## Consequences

- Pushing a `v*` tag now puts code on every updating Household's computer a day or more later, with no person in between. The tag ruleset of ADR 0045 and the signature are what stand in front of that; a release found to be bad within the day is stopped by moving `stable` only forward to a fixed one.
- The day's wait is time for the maintainers to notice a bad release and cut a fixed one before families take it. It isn't a test: the server deploys the release in that day (ADR 0045), but it runs without the Mac install, the scheduled job, the Keychain or the Linked Device, so its check says little about a family's computer.
- A release that needs the family (a password dialog, a new permission, a QR scan) is marked as such when it is cut and is never installed in the background: the Brief and the setup page say an update is waiting and how to start it.
- GitHub never gives a secret back, so the key in the release workflow can be lost with the environment that holds it. The program therefore trusts two keys: the workflow's, and a backup whose private half a maintainer keeps offline and that never reaches GitHub. A program trusts only the keys it was installed with, so a new key has to arrive in a release signed by one of them; the backup is what signs that release when the workflow's key is lost or leaked. If both are lost, each Household pastes the install line once.
- `doctor` passing is the whole check. A fault it doesn't see shows the next evening, and going back from there is the family pasting the install line or the maintainers cutting a fix.
- Claude Code keeps its own copy of the plugin, which this doesn't update, so the skills there can be older than the program.
- Windows isn't covered: a background download there is what Defender's rules stop (#591), and it is decided separately.
