#!/usr/bin/env bash
# Works through the backlog unattended: runs /implement on each ready issue, opens its PR and
# rebases and merges it once CI is green. No reviewer is added; quality is checked at
# milestones. It only ever touches the PRs it opens itself, and works the same whoever runs it.
#
# ── When to use it
# Use the loop to clear several ready-for-agent tickets while you're away, e.g. the rest of a
# release milestone. Each PR is merged as soon as CI is green, with no review, so only point it
# at tickets you're fine merging unreviewed.
# Use `/implement #N` by hand instead (Claude Code or Codex, see CONTRIBUTING.md) when you want
# to watch or steer the work, want someone to review the PR, or aren't on macOS with Claude Code.
# Several devs can run it at once: each assigns itself before starting, and backs off an issue
# someone else took at the same moment. If another run merges first and this run's PR then
# conflicts with main, this run stops with a draft PR.
#
# The agent only codes, commits and writes files into .loop/; this script does every push,
# PR and merge after the agent exits. Agent permissions and sandbox live in
# issue-loop.settings.json next to this file.
#
# ── Why the agent's permissions look like this (settings in issue-loop.settings.json)
# Claude Code 2.1.280–2.1.284 skip Bash deny rules for commands that run inside the sandbox
# (anything not on the allow list), despite the docs. So deny rules can't stop the agent
# writing to GitHub. Instead, GitHub is left off the sandbox's network allowlist (only
# PyPI), and only the allow-listed gh reads run outside it (gh can't pass macOS TLS
# checks inside Seatbelt anyway). As a result, the agent can't open PRs: it writes the PR
# text into .loop/ and this script opens the PR. Recheck on Claude Code upgrades.
# User hooks stay on (RTK keeps tool output short), but a hook must leave `gh` alone: RTK's
# `gh …` → `rtk gh …` stops the command matching the "gh *" exclusion, so gh runs sandboxed and
# can't reach GitHub. With RTK, set `[hooks] exclude_commands = ["gh"]` in its config.toml;
# the script checks this before starting. Any other hook that rewrites `gh` breaks it the same way.
# The model pair is pinned (Opus main thread, Sonnet subagents) so every machine runs the same.
#
# ── How to use it
# Needs, on macOS: `gh auth login` with write access to the repo, Claude Code logged in on a
# plan with Opus, app/.venv with the test deps (see "For maintainers" in README.md), and a
# clean working tree. The loop owns this checkout while it runs (it switches to main and
# pulls), so do other git work in a separate worktree. Run from anywhere in the repo.
#
#   scripts/issue-loop.sh --dry-run      # what it would do next, and why each issue is skipped
#   scripts/issue-loop.sh --max-jobs 1   # one job while you watch (do this until you trust it)
#   scripts/issue-loop.sh                # up to 5 jobs, then stops; run again to continue
#   scripts/issue-loop.sh --milestone 0.4.0  # only issues in that milestone
#
# --milestone keeps the loop on one release: issues outside the milestone are skipped, so
# tickets filed for later don't pull it away.
#
# It prints one line per job: the PR it merged. It ends by itself when nothing is left: every
# issue is done, blocked, assigned or has an open PR.
#
# When it stops with "stopped at ...":
#   1. Read the reason. The work is on a draft PR and the issue stays assigned to you, so
#      later runs skip it.
#   2. `claude --resume <session>` (from the line) opens the agent's session to see or finish it.
#   3. Fix it by hand or in that session, then mark the PR ready or push the fix yourself.
#      To hand an issue back to the loop, close the draft PR and unassign yourself.
#   4. Run the script again. It skips the failed issue and moves on; issues blocked by it
#      stay blocked.
# Comments left on a loop PR, and loop PRs that later conflict with main, are yours to handle.
#
# Files it writes into .loop/ (git-excluded): logs/<date-time>.log (everything it printed,
# one file per run), last.json (the agent's last result), pytest.log, and the PR text the
# agent hands over.
set -euo pipefail

REPO=zhao-hanbo/family-brief
MAX_JOBS=5
MILESTONE=
AGENT_TIMEOUT=3600 # seconds per claude call
MAX_TURNS=200

ROOT=$(git rev-parse --show-toplevel)
LOOP_DIR=$ROOT/.loop
SETTINGS=$ROOT/scripts/issue-loop.settings.json

# Pulling main after a merge can swap this file and the settings under the running bash (bash
# reads a script as it goes). Run from copies in .loop/ so the whole run uses this version.
if [ "${ISSUE_LOOP_COPY:-}" != 1 ]; then
  mkdir -p "$LOOP_DIR"
  cp "${BASH_SOURCE[0]}" "$LOOP_DIR/issue-loop.sh"
  cp "$SETTINGS" "$LOOP_DIR/issue-loop.settings.json"
  ISSUE_LOOP_COPY=1 exec bash "$LOOP_DIR/issue-loop.sh" "$@"
fi
SETTINGS=$LOOP_DIR/issue-loop.settings.json

DRY_RUN=0
while [ $# -gt 0 ]; do
  case $1 in
    --dry-run) DRY_RUN=1 ;;
    --max-jobs) MAX_JOBS=$2; shift ;;
    --milestone) MILESTONE=$2; shift ;;
    *) echo "usage: $0 [--dry-run] [--max-jobs N] [--milestone NAME]" >&2; exit 2 ;;
  esac
  shift
done

# Colour only on a terminal; the saved log gets the escapes stripped.
if [ -t 2 ]; then
  BOLD=$'\e[1m' DIM=$'\e[2m' RED=$'\e[31m' GREEN=$'\e[32m' CYAN=$'\e[36m' OFF=$'\e[0m'
else
  BOLD= DIM= RED= GREEN= CYAN= OFF=
fi
log() { printf '%s[issue-loop]%s %s\n' "$DIM" "$OFF" "$*" >&2; }
ok() { printf '%s✔ %s%s\n' "$GREEN$BOLD" "$*" "$OFF" >&2; }
bad() { printf '%s✘ %s%s\n' "$RED$BOLD" "$*" "$OFF" >&2; }
header() { printf '\n%s━━ %s ━━%s\n' "$CYAN$BOLD" "$*" "$OFF" >&2; }

# ── State for the failure report
CURRENT= # "#12", later "#12 (<PR url>)"
BRANCH=
SESSION=
ISSUE=
TITLE=

# ── Failure: stop the whole loop, keep the work visible
fail() {
  REPORTED=1
  local reason=$1 line
  line="stopped at $CURRENT: $reason"
  [ -n "$SESSION" ] && line+=" — inspect with: claude --resume $SESSION"
  if [ -n "$BRANCH" ] &&
    [ "$(git rev-list --count origin/main..HEAD 2>/dev/null || echo 0)" -gt 0 ]; then
    git push -q -u origin HEAD || true
    local pr
    pr=$(gh pr list -R "$REPO" --head "$BRANCH" --state open --json number --jq '.[0].number // empty')
    if [ -n "$pr" ]; then
      gh pr ready "$pr" -R "$REPO" --undo >/dev/null 2>&1 || true
      gh pr comment "$pr" -R "$REPO" --body "issue-loop stopped here: $reason" >/dev/null || true
    else
      gh pr create -R "$REPO" --draft --base main --head "$BRANCH" --title "wip: #$ISSUE $TITLE" \
        --body "issue-loop stopped here: $reason"$'\n\n'"Refs #$ISSUE" >/dev/null || true
    fi
    line+=" (draft PR on $BRANCH)"
  fi
  bad "$line"
  [ -z "${LOG_FILE:-}" ] || log "full log: $LOG_FILE"
  exit 1
}
# A setup problem found before any job starts: nothing to clean up.
die() { REPORTED=1; bad "$*"; exit 1; }
# set -e exits on any unexpected error; report it the same way as a planned stop.
REPORTED=
trap 'st=$?; [ "$st" = 0 ] || [ -n "$REPORTED" ] || fail "unexpected error (exit $st)"' EXIT

# ── Running the agent
# Leaves the JSON result in .loop/last.json and the session id in $SESSION.
run_agent() {
  local prompt=$1; shift
  local out=$LOOP_DIR/last.json
  # Pick the id up front so a live run can be watched before it ends.
  if [[ " $* " != *" --resume "* ]]; then
    SESSION=$(uuidgen | tr '[:upper:]' '[:lower:]')
    set -- "$@" --session-id "$SESSION"
    printf '%s▶ agent started:%s %s\n' "$CYAN" "$OFF" "$(printf '%s' "$prompt" | head -1 | cut -c1-80)" >&2
    log "session $SESSION — watch: tail -f ~/.claude/projects/$(pwd -P | sed 's/[^a-zA-Z0-9]/-/g')/$SESSION.jsonl"
  else
    printf '%s▶ agent resumed:%s %s\n' "$CYAN" "$OFF" "$(printf '%s' "$prompt" | head -1 | cut -c1-80)" >&2
  fi
  # macOS has no timeout(1); perl's alarm kills claude when the time is up.
  # The edit rule is absolute (//path) because a relative one follows the agent's `cd`:
  # after `cd app`, Edit(./**) stopped matching .loop/ and the agent's writes there were denied.
  perl -e 'alarm shift; exec @ARGV' "$AGENT_TIMEOUT" \
    claude "$@" -p "$prompt" --output-format stream-json --verbose --settings "$SETTINGS" \
    --allowedTools "Edit(/$ROOT/**)" \
    --permission-mode dontAsk --strict-mcp-config --max-turns "$MAX_TURNS" </dev/null |
    tee "$LOOP_DIR/stream.jsonl" | show_progress || true
  jq -c 'select(.type == "result")' "$LOOP_DIR/stream.jsonl" 2>/dev/null | tail -1 >"$out" || true
  local sid subtype
  sid=$(jq -r '.session_id // empty' "$out" 2>/dev/null || true)
  SESSION=${sid:-$SESSION}
  subtype=$(jq -r '.subtype // empty' "$out" 2>/dev/null || true)
  [ "$subtype" = success ] || fail "agent ended with '${subtype:-timeout or crash}'"
  [ "$(git branch --show-current)" = "$BRANCH" ] || fail "agent left branch $BRANCH"
}

# One line per agent step, so a long run can be followed from the terminal. The agent's final
# message is printed in full: it's where it lists what it couldn't verify.
show_progress() {
  jq -R --unbuffered -r --arg ok "$GREEN$BOLD" --arg bad "$RED$BOLD" --arg off "$OFF" '
    def clip: tostring | gsub("\\s+"; " ") | .[:140];
    def arg: .description // .file_path // .pattern // .skill // .command // .prompt // (. | tostring);
    (fromjson? // empty)
    | (now | localtime | strftime("%H:%M:%S")) as $t
    | if .type == "assistant" then
        .message.content[]?
        | if .type == "tool_use" then "  \($t) → \(.name): \(.input | arg | clip)"
          elif .type == "text" and (.text | test("\\S")) then "  \($t) · \(.text | clip)"
          else empty end
      elif .type == "result" then
        (if .subtype == "success" then $ok else $bad end) as $c
        | "  \($t) \($c)■ agent \(.subtype)\($off), \(.num_turns) turns, \((.duration_ms // 0) / 1000 | floor | "\(. / 60 | floor)m\(. % 60)s"), $\(.total_cost_usd // 0 | . * 100 | round / 100)",
          (.result // "" | select(test("\\S")) | split("\n") | map("    │ " + .) | join("\n"))
      else empty end' >&2
}

run_tests() {
  log "running tests"
  (cd "$ROOT/app" && .venv/bin/python -m pytest -q) >"$LOOP_DIR/pytest.log" 2>&1 ||
    fail "tests red, see .loop/pytest.log"
}

# Checks take a few seconds to show up on a new PR.
wait_ci() {
  local pr=$1 i
  log "waiting for CI on PR #${pr##*/}"  # called with a number or a PR URL
  for i in $(seq 20); do
    case $(gh pr checks "$pr" -R "$REPO" 2>&1 || true) in *'no checks reported'*) sleep 15 ;; *) break ;; esac
  done
  gh pr checks "$pr" -R "$REPO" --watch --fail-fast --interval 30 >/dev/null
}

sync_main() {
  git switch -q main
  git pull -q --ff-only origin main
}

# ── Picking work
# Every open ready-for-agent issue as "<n><TAB>ready" or "<n><TAB>skipped: <reason>", lowest first.
# Ready means in $MILESTONE (when given), no assignee, not a spec, no open PR closing it and
# no open blocker.
issue_statuses() {
  local prs
  prs=$(gh pr list -R "$REPO" --state open --limit 100 --json number,closingIssuesReferences \
    --jq '[.[] | {pr: .number, issues: [.closingIssuesReferences[].number]}]')
  gh issue list -R "$REPO" --label ready-for-agent --state open --limit 100 --json number,title,assignees,milestone |
    jq -r --argjson prs "$prs" --arg ms "$MILESTONE" '
      .[] | .number as $n
      | ([$prs[] | select(.issues | index($n)) | "#\(.pr)"] | join(", ")) as $pr
      | [$n,
         if $ms != "" and .milestone.title != $ms then "skipped: not in milestone \($ms)"
         elif (.title | startswith("Spec:")) then "skipped: spec"
         elif (.assignees | length) > 0 then "skipped: assigned to \([.assignees[].login] | join(", "))"
         elif $pr != "" then "skipped: open PR \($pr)"
         else "" end]
      | @tsv' | sort -n |
    while IFS=$'\t' read -r n status; do
      if [ -z "$status" ]; then
        local blockers
        blockers=$(gh api "repos/$REPO/issues/$n/dependencies/blocked_by" \
          --jq '[.[] | select(.state == "open") | "#\(.number)"] | join(", ")')
        status=${blockers:+skipped: blocked by $blockers}
      fi
      printf '%s\t%s\n' "$n" "${status:-ready}"
    done
}

# Assigns me to the issue. Fails, leaving it to the other person, when someone else assigned
# themselves too: another run listed it as ready at the same moment. When both runs back off,
# the issue waits for the next run.
claim_issue() {
  gh issue edit "$1" -R "$REPO" --add-assignee @me >/dev/null || return 1
  local others
  others=$(gh issue view "$1" -R "$REPO" --json assignees --jq "[.assignees[].login | select(. != \"$ME\")] | join(\", \")") ||
    return 1
  [ -z "$others" ] && return 0
  gh issue edit "$1" -R "$REPO" --remove-assignee @me >/dev/null || true
  log "#$1 was taken by $others at the same time; skipping it"
  return 1
}

ready_issues() {
  issue_statuses | awk -F'\t' '$2 == "ready" { print $1 }'
}

# Only a lone gh command runs outside the sandbox; chained with anything else it can't reach GitHub.
GH_NOTE="Run each gh command as its own Bash call, never chained or piped with other commands, \
or it can't reach GitHub."

# ── Job: new issue
IMPLEMENT_NOTE="work on the current branch and commit there with conventional commits. $GH_NOTE \
Don't push, open PRs or post to GitHub: the calling script does that after you finish. \
Don't run the real-model eval (python -m family_brief.eval) or list it as unverified: it runs once per release, not per PR. \
Nobody is watching this run, so if you need a human decision, stop without committing and say why in your final message."

do_issue() {
  ISSUE=$1 CURRENT="#$1" SESSION=
  TITLE=$(gh issue view "$ISSUE" -R "$REPO" --json title --jq .title)
  local slug
  slug=$(printf '%s' "$TITLE" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '-' | cut -c1-40 | sed 's/-$//')
  BRANCH=issue-$ISSUE-$slug
  header "job $((jobs + 1))/$MAX_JOBS: #$ISSUE $TITLE"
  log "branch $BRANCH"

  git switch -q -c "$BRANCH" origin/main

  run_agent "/implement #$ISSUE — $IMPLEMENT_NOTE"
  [ "$(git rev-list --count origin/main..HEAD)" -gt 0 ] ||
    fail "no commits (the agent may have stopped to ask something): $(jq -r .result "$LOOP_DIR/last.json" | head -c 300)"
  [ -z "$(git status --porcelain)" ] || fail "uncommitted changes left behind"
  run_tests

  rm -f "$LOOP_DIR"/pr-title "$LOOP_DIR"/pr-body.md
  run_agent "Now write the pull request for this work into files; don't run gh or push, the calling script opens the PR.
- .loop/pr-title: one line, conventional commit style (\`type: concise description\`, lowercase, no period).
- .loop/pr-body.md: first line exactly \`What changes for families: <one plain sentence, or 'nothing'>\`, a blank line, \`Closes #$ISSUE\`, a short summary of the change, then a \`## Not verified\` section listing every check you skipped or couldn't run and why (leave it out if there were none)." --resume "$SESSION"

  local pr_title
  pr_title=$(head -1 "$LOOP_DIR/pr-title" 2>/dev/null) || fail "agent wrote no .loop/pr-title"
  [ -s "$LOOP_DIR/pr-body.md" ] || fail "agent wrote no .loop/pr-body.md"

  git push -q -u origin HEAD
  local pr_url
  pr_url=$(gh pr create -R "$REPO" --base main --title "$pr_title" --body-file "$LOOP_DIR/pr-body.md")
  CURRENT="#$ISSUE ($pr_url)"
  wait_ci "$pr_url" || fail "CI red"
  gh pr merge "$pr_url" -R "$REPO" --rebase --delete-branch >/dev/null
  ok "#$ISSUE → $pr_url merged"
  sync_main
}

# ── Main loop
mkdir -p "$LOOP_DIR"

# Checked before --dry-run too, so trying the dry run first also checks the setup.
for cmd in gh jq claude; do
  command -v "$cmd" >/dev/null || die "$cmd not found; see \"How to use it\" at the top of scripts/issue-loop.sh"
done
gh auth status >/dev/null 2>&1 || die "gh is not logged in; run: gh auth login"
[ -x "$ROOT/app/.venv/bin/python" ] ||
  die "no app/.venv; create it and install the test deps (see \"For maintainers\" in README.md)"

if [ "$DRY_RUN" = 1 ]; then
  echo "Issues labelled ready-for-agent${MILESTONE:+, milestone $MILESTONE} (ready ones are taken lowest first):"
  issue_statuses | awk -F'\t' '{ printf "  #%s %s\n", $1, $2 }'
  exit 0
fi

# Everything printed from here on is also saved, without colour, one file per run.
LOG_FILE=$LOOP_DIR/logs/$(date +%Y%m%d-%H%M%S).log
mkdir -p "$LOOP_DIR/logs"
exec 2> >(tee >(perl -pe 'BEGIN { $| = 1 } s/\e\[[0-9;]*m//g' >>"$LOG_FILE") >&2)
log "log: $LOG_FILE"

if command -v rtk >/dev/null && rtk rewrite "gh issue view 1" >/dev/null 2>&1; then
  die "RTK rewrites gh, so the agent can't reach GitHub; add [hooks] exclude_commands = [\"gh\"] to $(rtk config 2>/dev/null | sed -n 's/^Config: //p')"
fi
[ -z "$(git status --porcelain)" ] || die "working tree not clean; commit or stash first"
git fetch -q origin
sync_main
ME=$(gh api user --jq .login)

SEEN=" "
SKIPPED=" " # issues another run took at the same moment; left to them for this run
jobs=0
while [ "$jobs" -lt "$MAX_JOBS" ]; do
  BRANCH= SESSION= ISSUE= TITLE= CURRENT="job picking"
  ready=$(ready_issues)
  next=
  for n in $ready; do
    case $SKIPPED in *" $n "*) ;; *) next=$n; break ;; esac
  done
  [ -n "$next" ] || { ok "done: nothing left to do after $jobs job(s)"; log "full log: $LOG_FILE"; exit 0; }
  # An issue that shows up twice means a step silently didn't stick; stop rather than loop.
  case $SEEN in *" $next "*) CURRENT="#$next"; fail "picked the same issue twice in one run" ;; esac

  # Called in a condition so set -e is off inside; claim_issue handles its own errors.
  claim_issue "$next" || { SKIPPED+="$next "; continue; }
  SEEN+="$next "
  do_issue "$next"
  jobs=$((jobs + 1))
done
ok "done: $MAX_JOBS job(s), the --max-jobs limit; run again to continue"
log "full log: $LOG_FILE"
