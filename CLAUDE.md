## Agent skills

### Issue tracker

Issues live in GitHub Issues for `kinlace/parent-recap`, managed via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Default five-role vocabulary (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `CONTEXT.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.

Before publishing a spec or tickets, record in `docs/adr/` (via `/domain-modeling`) every decision in them that clears that skill's ADR bar and has no ADR yet.

### Landing changes

`main` is protected: every change lands through a PR from its own branch, rebase-merged once the `test` and `gitleaks` checks pass. The PR body starts with `What changes for families:` and closes its issue (`Closes #N`). See [CONTRIBUTING.md](CONTRIBUTING.md#reviews).

### Gateway admin requests

A request to act on the hosted server (a Gateway Key, an invite, a family's status, a deploy) is answered with the exact `sudo admin …` command from the "What you want" table at the top of "Runbook" in `ops/gateway/README.md`. Xi runs it after `ssh oci-a1-admin`, which works only from Xi's own devices (hyprland, thinkpad), and pastes the output back when it needs reading. With each command, say whether its output can be pasted back as it is, per "What can be pasted" under that table: if not, say to read it on the server, or to take the addresses and key out first. The Mac mini is a tagged Tailscale node whose `mini` login on `oci-a1` reads logs and `systemctl status` only.
