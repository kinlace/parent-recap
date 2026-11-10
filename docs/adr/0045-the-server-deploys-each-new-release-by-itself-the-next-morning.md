# The server deploys each new release by itself the next morning

ADR 0036 had every deploy started by a maintainer from a personal device, so each one had a person behind it; in practice that left the Hosted Households on an older release until someone found the time. Now the server deploys each new release itself: a root timer on the server checks the public repo once a day, at 10:00 Helsinki time, and when `stable` (which the release workflow moves only to a tag whose tests passed and that is the highest version so far, ADR 0002) points at a release the server doesn't run, it runs the same `admin deploy` with its snapshot and checks. When a check fails it runs `admin rollback` and mails the maintainers what failed; when they pass it mails them the version it now runs, the check's result and that release's changelog. A maintainer can pause it on the server, and deploys started by hand (`deploy main`, a hotfix) still come only from a maintainer's device. Decided by Xi on 2026-11-10.

## Considered Options

- **Keep every deploy by hand.** Rejected: Hosted Households lag behind the releases families on their own computers already get.
- **Deploy from GitHub Actions.** Rejected: CI would hold a login to the server, which holds the provider key and every Household's messages. The server pulling the public repo needs no credential anywhere else.
- **Deploy as soon as the release is out.** Rejected: a release often comes in the evening, near the 20:30–22:30 runs. The next morning leaves time to pause a bad release and a whole day to fix one.

## Consequences

- Pushing a `v*` tag now puts code on the server as root by the next morning, with no person in between. A tag ruleset lets only the repo's admins create `v*` tags, so neither another collaborator nor a workflow's token can.
- A release whose changelog says switching back needs the snapshot (ADR 0036) isn't deployed by itself: the server mails the maintainers and waits for a deploy by hand, since its automatic switch-back can't restore data.
- The automatic check is one Household's evening run, as for any deploy. A problem only another Household has shows up in the nightly check's mail, and switching back from it stays a maintainer's call.
- The server runs a release unattended, and the maintainers learn of it by mail; `admin deploys` still records every deploy, whoever started it.
