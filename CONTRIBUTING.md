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

## How work flows

Work is issue-driven ([GitHub Issues](https://github.com/kinlace/parent-recap/issues)).

- **Pick up a ticket**: open issues labelled `ready-for-agent`, not blocked and unassigned. Start with `/implement #N` and open a PR to `main`.
- **New idea**: start at `/grill-with-docs`.
- **Bug**: `/triage` to file it, or `/diagnosing-bugs` to dig in.

Unsure what comes next? Ask `/ask-matt`; it knows the whole flow and stays current with the skills.

## Conventions

- Conventional commits: `type: concise description` (`feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:`, `ci:`).
- Tests and golden regeneration: see [`app/tests/README.md`](app/tests/README.md).

## Reviews

Maintainers' own PRs don't wait for a review. Once CI is green, the author **rebases and merges** it, so `main` keeps moving fast. CI and the tests are the safety net; quality gets a proper check at each milestone. If something needs the other person's eyes, ask them directly (add them as Reviewer or bring it up in a call). `main` has no branch protection.

**Keeping each other informed:** the first line of every PR description is `What changes for families:` plus one plain sentence, or `nothing` (the PR template prompts for it). Before a release, whoever releases collects these sentences into [`CHANGELOG.md`](CHANGELOG.md), so all changes can be read in one go, and runs the [Brief quality eval](app/tests/README.md#brief-quality-eval) on the commit to tag. A metric worse than the previous release's run by more than its spread holds the release until it's understood.

## Releasing

Add the release's `What changes for families:` lines to `CHANGELOG.md` and bump the version in `.claude-plugin/plugin.json` and `app/pyproject.toml` on `main`, then tag that commit and push the tag:

```bash
git tag v0.4.0 && git push origin v0.4.0
```

The tag push runs `.github/workflows/release.yml` (see [ADR 0002](docs/adr/0002-tags-are-releases-main-may-be-unstable.md)): the tests on macOS, then `scripts/release.py` builds `parent-recap-0.4.0.zip` and attaches it to a GitHub Release, then the `stable` branch moves to the tag if it is the highest version so far. Never push `stable` by hand. PRs and `main` run the same macOS tests, and every push and PR also runs a gitleaks secret scan (`.github/workflows/secret-scan.yml`) that fails on any finding.

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
