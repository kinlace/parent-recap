# Tags are releases; main may be unstable; families install from `stable`

Pilot fixes and the issue loop land on `main` fast, so `main` is allowed to be unstable and a release is a manually pushed `vX.Y.Z` tag on a commit we vetted (after the usual reviewed PR that fills in `CHANGELOG.md` and bumps the version). The tag push runs a workflow that runs the macOS tests, builds the zip with `scripts/release.py`, publishes it as a GitHub Release, and moves the `stable` branch to the tag. Families never see an untagged commit: the zip comes from the Releases page, and marketplace installs use `/plugin marketplace add kinlace/parent-recap#stable`, so `/plugin update` picks up each new version without a new command.

## Considered Options

- **`main` is always releasable, tag anything.** Rejected: it would slow down the pilot fixes that land on `main`.
- **Families pin the marketplace to a tag (`#v0.4.0`).** Stable, but every update would need the add command again.
- **`marketplace.json` pins `"ref"` to the release tag.** The release PR would have to name a tag that doesn't exist yet, and the catalog on `main` can't serve a hotfix cut from a release branch.
- **Drop the marketplace, zip only.** Once the repo is public, the marketplace install is one command with one-command updates; the zip stays for Codex and for families who don't want GitHub.

## Consequences

- A hotfix can't go on `main`, because it would ship with unreleased work. Branch `release/X.Y` from the latest tag, fix and tag there (`vX.Y.Z+1`), then cherry-pick the fix (not the version bump) back to `main`. Delete the branch once the next minor ships.
- Pushes to `main` are no longer releases. While the repo is private, the macOS test job moves from `main` pushes to tag pushes and PRs and `main` run on Linux, to save the 10x macOS minutes. Once it is public (ADR 0003) the minutes are free and every run is on macOS.
- `stable` is written only by the release workflow, and only when the new tag is the highest version so far. Moving it back to an older line would downgrade every marketplace family, because Claude Code updates on any version change, not only on newer ones. It still force-pushes, because a hotfix tag on `release/X.Y` is not a descendant of the previous tag's commit on `main`.
