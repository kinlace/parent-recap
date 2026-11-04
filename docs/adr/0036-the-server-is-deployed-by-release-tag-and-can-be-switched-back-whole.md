# The server is deployed by release tag and can be switched back whole

Until now a deploy copied a maintainer's working tree to the server (`scp`) and ran `gateway-setup.sh` from it, with the program as a second copy beside it, since the release zip leaves `ops/` out. Nothing on the server recorded which commit it ran, and only the program and Bifrost could be switched back: the Brief relay, the Caddyfile, the sudoers file and the systemd units were overwritten in place. We now deploy one commit for both, normally a `vX.Y.Z` release tag (ADR 0002): the server clones that commit of the public repo itself, keeps each deployed commit on disk, and records each deploy. When a fix can't wait for a release, a maintainer may deploy an unreleased commit on `main` instead, by asking for it by name; the deploy refuses one whose `test` check hasn't passed, and the server says it runs an unreleased commit until the next tag replaces it. Before changing anything a deploy takes a snapshot of the server's state, and switching back puts the previous deploy's program, gateway files and units back together. Deploys and switch-backs are started only from a maintainer's personal device through the admin login; the always-on assistant host keeps its read-only login and only checks the result. Decided by Xi on 2026-11-04.

## Considered Options

- **Keep copying a working tree.** Rejected: what runs depends on the state of one laptop, and nothing says which commit it was.
- **Only release tags.** Rejected: a fix to the server, or to every Hosted Household, would wait for a release, and a change to `ops/` alone would need one, which marketplace families also receive with nothing changed for them.
- **Deploy the release zip.** Rejected: it leaves out `ops/` on purpose, because families don't need it, so the gateway's files would still come from somewhere else.

## Consequences

- An unreleased commit brings everything on `main` with it, finished or not (ADR 0002), to every Hosted Household from its next run (ADR 0035). It is the exception: asked for by name, checked as any deploy, and replaced by the next tag.
- Deploys are kept on disk by commit, not by version: an unreleased commit carries the last release's version number, and keying by version would overwrite the deploy to switch back to.
- A release must not leave a Household's data unreadable to the release before it. One that has to says so in its changelog, and switching back from it means restoring the snapshot, not only switching the program.
- The server needs outbound HTTPS to `github.com`.
- Deploys stay out of 20:30–22:30, when the evening runs are, and are checked with one Household's evening run, as ADR 0035 requires.
