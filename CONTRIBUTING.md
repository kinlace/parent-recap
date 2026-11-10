# Contributing

## Outside contributors

Fixes and ideas are welcome from anyone.

1. For anything bigger than a typo, [open an issue](https://github.com/kinlace/parent-recap/issues) first and say what you want to change, so we can agree on the approach before you write code.
2. Fork `kinlace/parent-recap` on GitHub, clone your fork and create a branch from `main`.
3. Make the change with its tests, and run the tests (see [`app/tests/README.md`](app/tests/README.md)).
4. Push the branch to your fork and open a PR against `main` of `kinlace/parent-recap`. Fill in the PR template, including the `What changes for families:` line.

A maintainer reviews and merges it once CI is green. Security problems go through [SECURITY.md](SECURITY.md), not a public issue or PR.

## Setup

1. Clone the repo (or your fork) and run `gh auth login` (issues and PRs go through the `gh` CLI).
2. Open the folder in Claude Code or Codex. The dev-workflow skills (`/implement`, `/tdd`, `/grill-with-docs`, ...) load automatically from `.agents/skills/`; nothing to install.
3. Build the test environment and run the tests, before and after changing code (in the `app/` folder):

   ```bash
   python -m pip install --only-binary :all: -c constraints.txt -e '.[test,google]'
   python -m pytest
   ```

   The tests compare the Brief and the prompt word for word against `app/tests/golden/`; [`app/tests/README.md`](app/tests/README.md) says how to update them on purpose.

## How work flows

Work is issue-driven ([GitHub Issues](https://github.com/kinlace/parent-recap/issues)).

- **Pick up a ticket**: open issues labelled `ready-for-agent`, not blocked and unassigned. Start with `/implement #N` and open a PR to `main`.
- **New idea**: start at `/grill-with-docs`.
- **Bug**: `/triage` to file it, or `/diagnosing-bugs` to dig in.

Unsure what comes next? Ask `/ask-matt`; it knows the whole flow and stays current with the skills.

To work through issues labelled `ready-for-agent` unattended, one PR at a time, use `scripts/issue-loop.sh` (macOS, Claude Code). What it needs and how to run it are at the top of that file; try `scripts/issue-loop.sh --dry-run` first.

## Outside the repository

- The Google app file shared with pilot families is not in the repository or the package: the Parent Recap team sends it separately to pilot families who want Google Calendar mode, and `/parent-recap:manage` saves it to `~/.family/calendar_credentials.json`.
- The homepage at [parentrecap.com](https://parentrecap.com) lives in [kinlace/parent-recap-site](https://github.com/kinlace/parent-recap-site), which deploys it and holds the domain's DNS script. It links to the README for installing and privacy, so keep the README's `#install`, `#privacy` and `#what-the-ai-sees` headings or update its links. Every Brief from a family's own computer links `#change-settings` in the README of its language on `stable` (`settings_help` in `app/src/family_brief/brief_text.py`), so keep that anchor in all three READMEs.

## Conventions

- Conventional commits: `type: concise description` (`feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:`, `ci:`).
- `README.fi.md` and `README.zh-CN.md` translate the README for Finnish and Chinese families: change them together with it. The demo video is in English and Chinese, so the Finnish README links to the English one.
- Tests and golden regeneration: see [`app/tests/README.md`](app/tests/README.md).
- Coverage, as CI's macOS job reports it (lines and branches per module, no threshold): from `app/`, `python -m pytest --cov --cov-report=term`.

## Reviews

Maintainers' own PRs don't wait for a review. Once CI is green, the author **rebases and merges** it, so `main` keeps moving fast. CI and the tests are the safety net; quality gets a proper check at each milestone. If something needs the other person's eyes, ask them directly (add them as Reviewer or bring it up in a call). `main`'s ruleset asks for no review, only that the `test` and `gitleaks` checks pass before a merge.

**Urgent hotfix.** Repository admins may bypass the ruleset, but only when merging a PR: `gh pr merge N --rebase --admin` merges before the PR's CI finishes. It doesn't skip the server's gate: `sudo admin deploy` still deploys an unreleased commit only once its `test` check passed (`--anyway` skips only the evening-run and status checks, see `ops/gateway/README.md`). A merged PR whose code matches its tested head (the head was rebased onto the current `main` before merging, and its `test` passed) deploys at once, without waiting for `test` to run again on `main`; otherwise the deploy waits for that run. Use it only for a small fix that can't wait, such as wrong text on the sign-up page; anything else waits for CI. If `main`'s CI then fails, fix it at once. Tell the other maintainer when you used it.

**Keeping each other informed:** the first line of every PR description is `What changes for families:` plus one plain sentence, or `nothing` (the PR template prompts for it). Before a release, whoever releases collects these sentences into [`CHANGELOG.md`](CHANGELOG.md), so all changes can be read in one go, and runs the [Brief quality eval](app/tests/README.md#brief-quality-eval) on the commit to tag. A metric worse than the previous release's run by more than its spread holds the release until it's understood. It also rereads the [Commercial Terms](https://www.anthropic.com/legal/commercial-terms), section B, to check that Anthropic still may not train on Customer Content: SECURITY.md and the setup page tell Households on the gateway that it may not, and SECURITY.md quotes the clause as of the terms effective 2025-06-17. If it changed, fix both before the release.

## Releasing

Add the release's `What changes for families:` lines to `CHANGELOG.md` and bump the version in `.claude-plugin/plugin.json` and `app/pyproject.toml` on `main`, then tag that commit and push the tag:

```bash
git tag v0.4.0 && git push origin v0.4.0
```

The tag push runs `.github/workflows/release.yml` (see [ADR 0002](docs/adr/0002-tags-are-releases-main-may-be-unstable.md)): the tests on macOS, then `scripts/release.py` builds `parent-recap-0.4.0.zip` and attaches it to a GitHub Release, then the `stable` branch moves to the tag if it is the highest version so far. Never push `stable` by hand. PRs and `main` run the same macOS tests, except a change that only touches docs no test reads (`docs/adr/`, `docs/agents/`, `CONTEXT.md`, `CONTRIBUTING.md`, `CLAUDE.md`, `SECURITY.md`, `THIRD_PARTY_NOTICES.md`), which skips them; every push and PR also runs a gitleaks secret scan (`.github/workflows/secret-scan.yml`) that fails on any finding. The next morning the hosted server deploys the release `stable` points at by itself, checks it, switches back if a check fails, and mails the maintainers how it went (ADR 0045, `ops/gateway/README.md` "The morning's automatic deploy").

**A release that needs the snapshot to switch back.** A release must not leave a Household's data unreadable to the release before it (ADR 0036). One that has to, such as one that changes a stored file's format, carries this line in its section of `CHANGELOG.md`, under its `## X.Y.Z` heading:

```markdown
<!-- switching back needs the snapshot -->
```

It is an HTML comment, so families reading the changelog don't see it. The server then doesn't deploy that release by itself, since its automatic switch-back can't restore data: it mails the maintainers that the release waits, and a maintainer deploys it with `sudo admin deploy vX.Y.Z` while able to switch back with `sudo admin rollback --restore`.

The zip is built from the tag with `git archive`, so local edits never get in. `.gitattributes` export-ignore rules keep developer-only files out, and the script refuses to build if the versions disagree or if a venv, cache or `.env` was committed. To build the same zip locally: `python3 scripts/release.py v0.4.0`.

## Pinned packages

`install.sh` and CI install only prebuilt packages (wheels), at the versions in `app/constraints.txt`, so every family gets what CI tested and no install compiles anything. The file also pins the `google` extra (Google's packages, and `cryptography` with them), which `install.sh` adds only when the config's calendar mode is google. CI's `scripts/check_wheels.py` fails if a pinned package has no wheel for Apple Silicon or Intel Macs on Python 3.11 to 3.14.

To move the pins (a new dependency, or newer versions), regenerate the file from a fresh environment that passes the tests, from `app/`:

```bash
python3 -m venv "$TMPDIR/pins"
"$TMPDIR/pins/bin/pip" install --only-binary :all: -e '.[test,google]' 'cryptography<49'
"$TMPDIR/pins/bin/python" -m pytest
"$TMPDIR/pins/bin/pip" freeze --exclude-editable   # replaces every line below the comments in constraints.txt
python3 ../scripts/check_wheels.py
```

`cryptography<49` stays while Intel Macs are supported: 49 and later have no Intel wheel. Drop it there and in the file's comment once they aren't.

## Pinned Python

`install.sh` downloads its own Python, a python-build-standalone `install_only` build, and builds the venv on it (ADR 0011). To move it to a newer Python or build, take one [release](https://github.com/astral-sh/python-build-standalone/releases) and change the six lines under `Parent Recap's own Python` in `install.sh`: `PYTHON_VERSION` and `PYTHON_BUILD` (the release's date tag), then `PYTHON_SHA256_AARCH64` and `PYTHON_SHA256_X86_64`, the checksums of the two `cpython-<version>+<build>-<arch>-apple-darwin-install_only.tar.gz` files, and `PYTHON_SHA256_LINUX_AARCH64` and `PYTHON_SHA256_LINUX_X86_64`, those of the two `-unknown-linux-gnu-` ones the hosted version's server installs (ADR 0029), from the release's `SHA256SUMS`:

```bash
curl -fsSL https://releases.astral.sh/github/python-build-standalone/releases/download/<build>/SHA256SUMS \
  | grep -E 'cpython-<version>\+<build>-(aarch64|x86_64)-(apple-darwin|unknown-linux-gnu)-install_only\.tar\.gz$'
```

That's Astral's mirror of the release, which uv downloads from: both installs try it first and then GitHub's `https://github.com/astral-sh/python-build-standalone/releases/download/<build>/`, which serves the same files (#843), so only the checksums decide what gets installed.

`install.ps1`, the Windows install, pins the same Python: change `$PythonVersion` and `$PythonBuild` there too, and `$PythonSha256` to the checksum of `cpython-<version>+<build>-x86_64-pc-windows-msvc-install_only.tar.gz` from the same `SHA256SUMS`. CI's windows-install and linux-install jobs check the pinned checksums against the published lists and prints the published line when one doesn't match; `tests/scripts/test_install_ps1.py` checks both scripts pin the same versions.

A new minor Python also goes into `PYTHONS` in `scripts/check_wheels.py`. Each version goes into its own folder.

## Pinned Node

`install.sh` also downloads its own Node, which the wilma CLI runs on (ADR 0011). To move it to a newer Node, take an LTS [release](https://nodejs.org/en/about/previous-releases) and change the five lines under `Parent Recap's own Node` in `install.sh`: `NODE_VERSION`, then `NODE_SHA256_ARM64` and `NODE_SHA256_X64`, the checksums of the two `node-v<version>-darwin-<arch>.tar.xz` files, and `NODE_SHA256_LINUX_ARM64` and `NODE_SHA256_LINUX_X64`, those of the two `node-v<version>-linux-<arch>.tar.xz` ones, from the release's `SHASUMS256.txt`:

```bash
curl -fsSL https://nodejs.org/dist/v<version>/SHASUMS256.txt | grep -E '(darwin|linux)-(arm64|x64)\.tar\.xz$'
```

In `install.ps1`, change `$NodeVersion` too, and `$NodeSha256` to the checksum of `node-v<version>-win-x64.zip` from the same `SHASUMS256.txt`.

The wilma CLI already installed keeps working on a new Node: the next install replaces the old one.

If the new Node needs a newer macOS than 13.5 (see its release's supported platforms), raise the minimum in `refuse_old_macos` in both `install.sh` and `get.sh`, and under "What you need" in the README.

## Pinned Baileys

The Linked Device runs on Baileys (ADR 0025), whose whole npm tree is pinned in `app/src/family_brief/baileys/`: `package.json` names the Baileys version, and `package-lock.json` pins every package with its integrity hash. The installers install exactly that tree with `npm ci`. To move Baileys, change its version in `package.json` and `BAILEYS_VERSION` in `family_brief/baileys_install.py`, then rebuild the lockfile in that folder with the pinned Node's npm and review its diff:

```bash
npm install --package-lock-only --ignore-scripts
```

The next install of each family replaces the tree, since its lockfile no longer matches.

The Linked Device's own program, `linked_device.mjs` in the same folder, is tested on a fake Baileys (`app/tests/fake_baileys/`), not the real one. So read the new version's changelog for what the program uses (`makeWASocket`'s options, `connection.update`, `creds.update`, `messages.upsert`, `messaging-history.set`, `groups.update`, `groupFetchAllParticipating`, `groupMetadata`, the shape of a message (`key`, `pushName`, `messageTimestamp`) and the `normalizeMessageContent` and `getContentType` that read its content, the auth state's `BufferJSON`, `initAuthCreds` and `proto`), and change the fake with the program when it moves.

## Two kinds of skills

- `skills/` is the product: the `setup` and `manage` skills users install. Edit these freely.
- `.agents/skills/` (linked from `.claude/skills/`) holds Matt Pocock's skills, vendored from [mattpocock/skills](https://github.com/mattpocock/skills) and pinned in `skills-lock.json` (each skill's `computedHash` is its version; upstream has no version numbers). **Never edit them.** To steer a single run, add the instruction to the slash command's arguments, e.g. `/implement #12 — show each test failing before the fix`.

## Updating the vendored skills

Update the global and project copies together:

```bash
npx skills update -g   # your global copies, if you keep them
npx skills update -p   # the repo's copies and skills-lock.json
```

A routine update is just a PR with the diff. If an update changes the workflow (a skill renamed or removed, the flow changes, docs need to follow), open an issue first.

**Global copies win.** A personal skill in `~/.claude/skills/` takes priority over the project copy with the same name, so if you keep global copies, keep them in sync. Drift check (prints nothing when in sync):

```bash
bash -c 'for s in $(jq -r ".skills | keys[]" skills-lock.json); do [ -e ~/.claude/skills/$s ] && diff -rq ~/.claude/skills/$s/ .agents/skills/$s/; done'
```
