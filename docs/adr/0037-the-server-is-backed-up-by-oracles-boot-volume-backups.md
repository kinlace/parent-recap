# The server is backed up by Oracle's boot volume backups

Nothing on the server was backed up: losing the VM meant inviting every Hosted Household again, and losing every Gateway Key, the Recipients bound to keys made on request, and each Household's Wilma sign-in and archive. We back up the whole boot volume with a weekly scheduled backup policy in Oracle Cloud, kept within the tenancy's quota of 5 backups (`oci-quota.json`), and restore by making a new VM from a backup. The backups are encrypted with Oracle's keys in the same tenancy, not our own: anyone who can read them can already read the running VM's disk from the console, which is behind MFA and held by one maintainer. Rolling back a bad deploy uses the deploy's own snapshot (ADR 0036), not these. Decided by Xi on 2026-11-04.

## Considered Options

- **`restic` to Object Storage, encrypted with our own key.** Rejected for now: a script and a key to keep, and in the same tenancy it guards against no one the console doesn't already let in.
- **A copy outside Oracle.** Rejected for now; it is the only option that survives losing the Oracle account itself. Revisit before a public launch (#666).

## Consequences

- A restore loses what changed since the last weekly backup: Households that signed up in between are invited again, and Briefs archived in between are gone.
- The backups hold the same secrets as the disk (Wilma sign-ins, Gateway Keys, the provider key, the SMTP credentials), so who can open the console is who can read them.
