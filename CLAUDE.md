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
