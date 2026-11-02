# Hosted Households run one shared copy of the program

ADR 0031 has the sign-up service install the program under each Hosted Household's own user with the release's `install.sh`, so each Household runs the copy it was installed with, and a redeploy of the server upgrades only the sign-up service. That suits a family's own computer, where the program is theirs to update, but on the maintainers' server it means each fix reaches only the Households signed up after it. We now install the program once on the server, owned by root and read-only to everyone, and every Hosted Household's evening job runs that copy; the Household's user keeps only its own data (its config, the wilma CLI's sign-in, the Gateway Key file, the archive and the logs). A redeploy upgrades every Hosted Household from its next run. Decided by Xi on 2026-11-02, when inviting the second Household.

## Considered Options

- **Keep a copy per Household and add an upgrade step that reruns `install.sh` as each user.** Rejected: it reaches the same Briefs with N installs to keep in step, a sign-up that spends most of its time installing, and Households left behind whenever one user's upgrade fails.

## Consequences

- A bad deploy breaks every Hosted Household's next Brief at once, not only new ones. After each deploy a maintainer runs one Household's evening run (their own) before 21:00.
- The pinned Python and Node (ADR 0011) and the wilma CLI (ADR 0008) are part of the shared copy, so they are upgraded with it too.
- A Household's user can no longer change its program, which it never needed to.
