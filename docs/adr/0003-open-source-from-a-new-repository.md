# Open-source from a new repository; the shared Google client leaves the repo

This repo never goes public. We publish from a new repository, owned by a GitHub organization the two of us share, whose history starts with a single commit. Squashing or `git filter-repo` here wouldn't be enough: GitHub keeps `refs/pull/N/head` for every PR, anyone can fetch those on a public repo, and they and the old tags still hold `app/assets/google_oauth_client.json`, both of our personal emails and the pilot discussion. The new repo becomes the only development repo; this one is archived as private history.

## Considered Options

- **Make this repo public after deleting the client in Google Cloud.** A dead secret in history is harmless, but the acceptance bar is a clean secret scan, and every issue and PR about pilot families would go public with it.
- **Keep this repo for development and push a public mirror.** Clean, but we would sync two repos forever.
- **Delete the shared OAuth client** (the earlier plan). Every pilot family using Google Calendar mode would lose sync, and an installed-app secret isn't confidential by Google's own rules.
- **Rotate the secret.** Every copy that exists is in this private repo or in zips we gave to people we know, and pilot families must be handed the file anyway, so a new secret would spread to the same people and every Google-mode family would have to authorize again.
- **Keep the shared client in the public repo.** An unverified app with the sensitive `calendar.events` scope has a lifetime cap of 100 users, every consent shows the unverified warning, and strangers' use would count against Hanbo's project.

## Consequences

- We keep the shared client and its secret. The app keeps its consent screen, its "In production" status (no 7-day token expiry) and its user count. If a copy ever reaches people we didn't give it to, we rotate then: strangers would count against the 100-user cap.
- The client file is no longer in the repo or the zip. Operators hand it to new pilot families next to the feedback snippet, and setup saves it to `~/.family/calendar_credentials.json`, which the authorization script already prefers. Families already in Google mode have it there from the bundled copy and don't authorize again.
- Public users get the `.ics` attachment by default. Google Calendar mode is an advanced option with their own Desktop OAuth client, and the guide must include **Publish app** on the consent screen: an external app left in "Testing" gets refresh tokens that expire after 7 days. If public demand for Google mode appears, the fix is verifying a shared app, not asking families to build their own.
- Issues can't be transferred from a private repo to a public one, so open issues are recreated by hand; old issues, PRs, tags and Releases stay here. Unmerged branches still in use move as one squashed commit each.
- The first commit is not tagged: `v0.4.0` is the first public release, the marketplace command names the new repository, and pilot families reinstall from it.
