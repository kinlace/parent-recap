# A family's computer installs each new release by itself after the evening Brief

A Household on its own computer got a new release only when someone pasted the install line again, which the families we build for don't do, so they stayed on old versions while Hosted Households got each release the next morning (ADR 0045). Now the program updates itself: once the evening Brief is sent, it reads the version `stable` holds (ADR 0002), and when that release is newer than its own and at least a day old, it downloads that tag's release zip, checks its signature against a public key that ships inside the installed program, installs it beside the running copy, switches to it and runs `doctor`; when `doctor` fails it switches back. The next Brief says at its top which version it now runs, or that the update failed. It is on for every Household, new or already installed, and the setup page says so with a switch to turn it off. Decided by Xi on 2026-11-12, for the Mac first.

A program that downloads and runs code in the background is what malware does, and many families have a coding agent read the program before they trust it. So the update is built to be read, and each of these is something a test shows:

- It is stated on the setup page, in the README and in `SECURITY.md`.
- The update's own requests go only to this repository on GitHub, at an address the program holds and nothing remote can change, and they carry nothing about the Household.
- Nothing it downloads is run before its signature verified, and nothing is piped from the network into a shell. What runs is the verified release's own installer.
- That installer fetches what the release pins from the hosts an install already uses (the Python packages, the Linked Device's npm tree, and a new Python or Node when the release changes them). `SECURITY.md` lists those hosts and how each download is checked.
- It adds no background job of its own and asks for no password: a release that would ask for one isn't installed in the background, and waits for the family to start it (below).
- It writes only to the places an install already writes, which `SECURITY.md` lists: Parent Recap's folder and the few files outside it that belong to the install.
- It reads nothing the maintainers' gateway answers, so the Run Report (ADR 0048) can't start an update or choose what a Household gets: every Household takes the same release from the same public address.

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
- A release that needs the family (a password dialog, a new permission, a QR scan) says so inside the zip, where the signature covers it: it names the lowest version that can take it in the background, with a sentence on what the family will be asked. A Household on an older version than that is never updated in the background: the Brief and the setup page say an update is waiting and how to start it. Later releases carry that version forward, so a Household several releases behind is held back too, without the program looking at the releases in between.
- The version `stable` holds isn't signed, so after verifying the zip the program reads the version inside it: it must be the one asked for and higher than the one installed. An older signed zip put in a newer release's place is refused.
- The installed program is reached at one path that stays the same across versions and points at the current one; switching and switching back change where it points. The scheduled jobs name that path, so an update leaves them as they are and macOS has no new background item to announce.
- With the switch off the program still reads the version each evening, to say in the Brief that a newer release is out, and downloads nothing else. The setup page and `SECURITY.md` say so.
- The Python packages an install fetches are pinned by version, not yet by checksum, so the signature doesn't cover them until #931 pins them. `SECURITY.md` says which it is.
- A Household installed before this never saw the setup page's sentence: it learns of the update from the first Brief after one, which says where to turn it off.
- GitHub never gives a secret back, so the key in the release workflow can be lost with the environment that holds it. The program therefore trusts two keys: the workflow's, and a backup that a maintainer holds outside GitHub. Signatures are made under a namespace of Parent Recap's own, so one a key made for anything else isn't a release's. A program trusts only the keys it was installed with, so a new key has to arrive in a release signed by one of them; the backup is what signs that release when the workflow's key is lost or leaked. If both are lost, each Household pastes the install line once.
- `doctor` passing is the whole check. A fault it doesn't see shows the next evening, and going back from there is the family pasting the install line or the maintainers cutting a fix.
- Claude Code keeps its own copy of the plugin, which this doesn't update, so the skills there can be older than the program.
- Windows isn't covered: a background download there is what Defender's rules stop (#591), and it is decided separately.
