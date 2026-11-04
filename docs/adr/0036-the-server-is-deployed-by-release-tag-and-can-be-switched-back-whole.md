# The server is deployed by release tag and can be switched back whole

Until now a deploy copied a maintainer's working tree to the server (`scp`) and ran `gateway-setup.sh` from it, with the program as a second copy beside it, since the release zip leaves `ops/` out. Nothing on the server recorded which commit it ran, and only the program and Bifrost could be switched back: the Brief relay, the Caddyfile, the sudoers file and the systemd units were overwritten in place. We now deploy one `vX.Y.Z` release tag (ADR 0002) for both: the server clones that tag of the public repo itself, keeps each deployed tag on disk, and records each deploy. Before changing anything a deploy takes a snapshot of the server's state, and switching back puts the previous tag's program, gateway files and units back together. Deploys and switch-backs are started only from a maintainer's personal device through the admin login; the always-on assistant host keeps its read-only login and only checks the result. Decided by Xi on 2026-11-04.

## Considered Options

- **Keep copying a working tree.** Rejected: what runs depends on the state of one laptop, and nothing says which commit it was.
- **Deploy any commit on `main`.** Rejected: `main` may be unstable (ADR 0002), and every Hosted Household runs whatever is deployed from its next run (ADR 0035).
- **Deploy the release zip.** Rejected: it leaves out `ops/` on purpose, because families don't need it, so the gateway's files would still come from somewhere else.

## Consequences

- A release must not leave a Household's data unreadable to the release before it. One that has to says so in its changelog, and switching back from it means restoring the snapshot, not only switching the program.
- A change to `ops/` alone reaches the server only with a release: the next one, or a patch release cut as in ADR 0002, which marketplace families also receive with nothing changed for them.
- The server needs outbound HTTPS to `github.com`.
- Deploys stay out of 20:30–22:30, when the evening runs are, and are checked with one Household's evening run, as ADR 0035 requires.
