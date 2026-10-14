"""The issue loop's last step for a PR: wait for CI, then merge (#130).

Sources the real `scripts/issue-loop.sh` and runs `merge_pr` against a fake `gh`, which answers
`gh pr checks --json` from a file, its `--watch` with the exit code CI would give, and
`gh pr merge` as GitHub would, logging every call.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PR = "https://github.com/kinlace/parent-recap/pull/7"
RULESET_REFUSAL = (
    "X Pull request kinlace/parent-recap#7 is not mergeable: the base branch policy prohibits the merge.\n"
    "Required status check \"test\" is expected."
)


@pytest.fixture
def gh(tmp_path: Path) -> dict:
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    log = tmp_path / "gh.log"
    fake = bin_ / "gh"
    fake.write_text(f"""#!/bin/bash
echo "gh $*" >> "{log}"
case "$*" in
  "pr checks"*--json*)
    late=$(cat "{tmp_path}/test_late_by" 2>/dev/null || echo 0)
    if [ "$late" -gt 0 ]; then
      echo $((late - 1)) > "{tmp_path}/test_late_by"
      echo '[{{"name": "gitleaks", "bucket": "pass"}}]'
    else
      cat "{tmp_path}/checks.json"
    fi ;;
  "pr checks"*--watch*) exit "$(cat "{tmp_path}/watch_exit")" ;;
  "pr merge"*) [ -f "{tmp_path}/merge_refusal" ] || exit 0; cat "{tmp_path}/merge_refusal" >&2; exit 1 ;;
esac
""")
    fake.chmod(0o755)
    (tmp_path / "watch_exit").write_text("0")
    return {"tmp": tmp_path, "bin": bin_, "log": log}


def report(gh: dict, **buckets: str) -> None:
    """The checks GitHub reports on the PR, by name, with their bucket (pass, fail, pending...)."""
    checks = [{"name": name, "bucket": bucket} for name, bucket in buckets.items()]
    (gh["tmp"] / "checks.json").write_text(json.dumps(checks))


def merge_pr(gh: dict) -> subprocess.CompletedProcess:
    env = {**os.environ, "PATH": f"{gh['bin']}:{os.environ['PATH']}", "ISSUE_LOOP_CI_POLL": "0"}
    script = f'source scripts/issue-loop.sh; CURRENT="#5 ({PR})"; merge_pr {PR}'
    return subprocess.run(["bash", "-c", script], cwd=ROOT, capture_output=True, text=True, env=env)


def calls(gh: dict) -> list[str]:
    return gh["log"].read_text().splitlines() if gh["log"].exists() else []


def merged(gh: dict) -> bool:
    return any(c.startswith("gh pr merge") for c in calls(gh))


def test_run_rather_than_sourced_the_script_still_runs_its_main_part(gh):
    # ISSUE_LOOP_COPY=1 skips copying it into .loop/, which a running loop may be reading.
    env = {**os.environ, "PATH": f"{gh['bin']}:{os.environ['PATH']}", "ISSUE_LOOP_COPY": "1"}
    result = subprocess.run(["bash", "scripts/issue-loop.sh", "--no-such-option"], cwd=ROOT,
                            capture_output=True, text=True, env=env)
    assert result.returncode == 2
    assert "usage:" in result.stderr


def test_a_pr_with_only_gitleaks_is_not_merged_and_the_missing_test_check_is_named(gh):
    report(gh, gitleaks="pass")
    result = merge_pr(gh)
    assert result.returncode != 0
    assert not merged(gh)
    assert "stopped at #5" in result.stderr
    assert "test check" in result.stderr and "gitleaks" in result.stderr


def test_a_pr_with_no_checks_at_all_is_not_merged(gh):
    report(gh)
    result = merge_pr(gh)
    assert result.returncode != 0
    assert not merged(gh)
    assert "test check" in result.stderr


def test_a_pr_with_test_passing_is_merged(gh):
    report(gh, gitleaks="pass", test="pass")
    result = merge_pr(gh)
    assert result.returncode == 0, result.stderr
    assert f"gh pr merge {PR} -R kinlace/parent-recap --rebase --delete-branch" in calls(gh)


def test_a_test_check_that_shows_up_after_gitleaks_is_waited_for(gh):
    report(gh, gitleaks="pass", test="pass")
    (gh["tmp"] / "test_late_by").write_text("3")
    result = merge_pr(gh)
    assert result.returncode == 0, result.stderr
    assert merged(gh)


def test_a_failing_test_check_stops_the_loop_without_merging(gh):
    report(gh, gitleaks="pass", test="fail")
    (gh["tmp"] / "watch_exit").write_text("1")
    result = merge_pr(gh)
    assert result.returncode != 0
    assert not merged(gh)
    assert "CI red" in result.stderr


def test_a_test_check_that_ends_other_than_passed_is_not_merged(gh):
    # --watch exits 0 for a skipped or cancelled check too.
    report(gh, gitleaks="pass", test="skipping")
    result = merge_pr(gh)
    assert result.returncode != 0
    assert not merged(gh)
    assert "test check" in result.stderr and "skipping" in result.stderr


def test_a_refused_merge_stops_the_loop_with_github_s_reason(gh):
    report(gh, gitleaks="pass", test="pass")
    (gh["tmp"] / "merge_refusal").write_text(RULESET_REFUSAL)
    result = merge_pr(gh)
    assert result.returncode != 0
    assert "stopped at #5" in result.stderr
    assert "merge refused" in result.stderr
    assert "base branch policy prohibits the merge" in result.stderr


# ── Picking work

@pytest.fixture
def backlog(tmp_path: Path) -> Path:
    """A fake `gh` serving issues from issues.json (by number): no open PRs, no blockers."""
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    fake = bin_ / "gh"
    fake.write_text(f"""#!/bin/bash
issues="{tmp_path}/issues.json"
case "$*" in
  "pr list"*) echo '[]' ;;
  "issue list"*) jq '[.[] | select(.state == "OPEN")]' "$issues" ;;
  "issue view "*) jq -e --argjson n "$3" '.[] | select(.number == $n)' "$issues" || exit 1 ;;
  "api "*) ;;
esac
""")
    fake.chmod(0o755)
    return tmp_path


def issue(number: int, body_len: int, *, title: str = "Do a thing", state: str = "OPEN",
          assignees: list[str] | None = None, milestone: str | None = None) -> dict:
    return {"number": number, "title": title, "state": state, "body": "x" * body_len,
            "assignees": [{"login": a} for a in assignees or []],
            "milestone": {"title": milestone} if milestone else None}


def statuses(backlog: Path, issues: list[dict], args: str = "", *, titles: bool = False) -> list[str]:
    """What the script says about each issue, in the order it would take them."""
    (backlog / "issues.json").write_text(json.dumps(issues))
    env = {**os.environ, "PATH": f"{backlog / 'bin'}:{os.environ['PATH']}"}
    script = f'source scripts/issue-loop.sh; set -- {args}; PICKS="$*"; issue_statuses'
    out = subprocess.run(["bash", "-c", script], cwd=ROOT, capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stderr
    return [line.replace("\t", " ") if titles else " ".join(line.split("\t")[:2])
            for line in out.stdout.splitlines()]


def test_without_numbers_the_smallest_issue_comes_first(backlog):
    got = statuses(backlog, [issue(3, 900), issue(9, 100), issue(5, 100), issue(1, 500)])
    assert got == ["5 ready", "9 ready", "1 ready", "3 ready"]


def test_named_issues_are_taken_in_the_order_given_whatever_their_size(backlog):
    got = statuses(backlog, [issue(3, 900), issue(9, 100)], "3 9")
    assert got == ["3 ready", "9 ready"]


def test_a_named_issue_needs_no_ready_label_and_ignores_the_milestone(backlog):
    # `issue view` doesn't filter by label, and the milestone is only applied to automatic picks.
    env_issue = issue(4, 50, milestone="0.9.0")
    (backlog / "issues.json").write_text(json.dumps([env_issue]))
    env = {**os.environ, "PATH": f"{backlog / 'bin'}:{os.environ['PATH']}"}
    script = 'source scripts/issue-loop.sh; PICKS=4; MILESTONE=0.4.0; issue_statuses'
    out = subprocess.run(["bash", "-c", script], cwd=ROOT, capture_output=True, text=True, env=env)
    assert out.stdout.split("\t")[:2] == ["4", "ready"]


def test_named_issues_that_cannot_be_worked_say_why(backlog):
    got = statuses(backlog, [
        issue(1, 10, title="Spec: big"), issue(2, 10, state="CLOSED"), issue(3, 10, assignees=["sam"]),
    ], "1 2 3 99")
    assert got == ["1 skipped: spec", "2 skipped: closed", "3 skipped: assigned to sam",
                   "99 skipped: no such issue"]


def test_every_issue_comes_with_its_title(backlog):
    got = statuses(backlog, [issue(1, 10, title="Spec: big"), issue(2, 10, title="Fix it")], "1 2 99", titles=True)
    assert got == ["1 skipped: spec Spec: big", "2 ready Fix it", "99 skipped: no such issue "]


def test_a_named_issue_given_twice_or_with_a_hash_is_taken_once_and_sets_the_job_limit():
    # Sourcing with arguments runs the script's option parsing, which stops short of its main part.
    script = 'source scripts/issue-loop.sh 7 "#9" 7; echo "$PICKS|$MAX_JOBS"'
    out = subprocess.run(["bash", "-c", script], cwd=ROOT, capture_output=True, text=True)
    assert out.stdout.strip() == "7 9|2", out.stderr


# ── Checking the work before the PR

@pytest.fixture
def branch(tmp_path: Path) -> Path:
    """A repo with one commit, the implementer's last result in .loop/ and a Claude Code
    config dir holding the implementer's session, whose subagents are two reviews and a search."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(repo)]
    subprocess.run([*git, "init", "-q"], check=True)
    (repo / ".gitignore").write_text(".loop/\n")
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-qm", "work"], check=True)
    (repo / ".loop").mkdir()
    (repo / ".loop" / "last.json").write_text(json.dumps({"result": "Done. Nothing for you to decide."}))
    project = "".join(c if c.isalnum() else "-" for c in str(repo.resolve()))
    subagents = tmp_path / "claude" / "projects" / project / "impl-session" / "subagents"
    subagents.mkdir(parents=True)
    for name, description, texts in [
        ("a1", "Spec review of the diff", ["Reading the diff.", "Spec: the notice is missing."]),
        ("a2", "Standards review of the diff", ["Standards: no hard violations."]),
        ("a3", "Find the gateway module", ["It is in gateway.py."]),
    ]:
        (subagents / f"agent-{name}.meta.json").write_text(json.dumps({"description": description}))
        lines = [{"type": "assistant", "message": {"content": [{"type": "text", "text": t}]}} for t in texts]
        (subagents / f"agent-{name}.jsonl").write_text("\n".join(json.dumps(line) for line in lines))
    return repo


def check(repo: Path, checker: str) -> subprocess.CompletedProcess:
    """Runs check_work with `checker` as the agent's run (bash, in the repo)."""
    env = {**os.environ, "CLAUDE_CONFIG_DIR": str(repo.parent / "claude")}
    script = (f'source "{ROOT}/scripts/issue-loop.sh"; ISSUE=5 CURRENT="#5" SESSION=impl-session; '
              f'run_agent() {{ SESSION=check-session; {checker}; }}; check_work; echo "session $SESSION"')
    return subprocess.run(["bash", "-c", script], cwd=repo, capture_output=True, text=True, env=env)


def test_the_checker_gets_the_report_and_only_the_reviews_last_words(branch):
    result = check(branch, "echo PASS > .loop/check.md")
    assert result.returncode == 0, result.stderr
    assert "check passed" in result.stderr
    assert (branch / ".loop" / "report.md").read_text().strip() == "Done. Nothing for you to decide."
    reviews = (branch / ".loop" / "reviews.md").read_text()
    assert "## Spec review of the diff" in reviews and "Spec: the notice is missing." in reviews
    assert "Standards: no hard violations." in reviews
    assert "Reading the diff." not in reviews and "gateway.py" not in reviews


def test_a_pass_with_a_reason_passes_and_says_it(branch):
    result = check(branch, "echo 'PASS: every criterion is met' > .loop/check.md")
    assert result.returncode == 0, result.stderr
    assert "check passed: every criterion is met" in result.stderr


def test_after_a_pass_the_implementer_s_session_is_resumed_for_the_pr(branch):
    result = check(branch, "echo PASS > .loop/check.md")
    assert "session impl-session" in result.stdout


def test_a_failed_check_is_reported_back_without_stopping_yet(branch):
    result = check(branch, "printf 'FAIL: a question is left for a person\\n- #5 asks for a decision\\n' > .loop/check.md")
    assert result.returncode == 0, result.stderr
    assert "check failed: a question is left for a person" in result.stderr
    assert "session impl-session" in result.stdout


def test_a_checker_that_writes_no_verdict_stops_the_loop(branch):
    result = check(branch, "true")
    assert result.returncode != 0
    assert "the checker wrote no .loop/check.md" in result.stderr


def test_a_verdict_that_is_neither_pass_nor_fail_stops_the_loop(branch):
    result = check(branch, "echo 'Looks good to me' > .loop/check.md")
    assert result.returncode != 0
    assert "isn't PASS or FAIL: Looks good to me" in result.stderr


def test_a_checker_that_changes_the_branch_stops_the_loop(branch):
    result = check(branch, "echo PASS > .loop/check.md; echo x > stray.txt")
    assert result.returncode != 0
    assert "the checker changed the branch" in result.stderr


# ── A fix round after a failed check

FAIL_CHECK = "printf 'FAIL: a fact has no source\\n- docs/x.md:3 states macOS behaviour\\n' > .loop/check.md"


def check_and_fix(repo: Path, checks: list[str], fixer: str) -> subprocess.CompletedProcess:
    """Runs check_and_fix with the checker's runs taken from `checks` in turn and `fixer` as the
    implementer's fix run. Every agent call is logged to agents.log, next to the repo (a file in
    it would count as the agent changing the branch), with whether it got the web."""
    env = {**os.environ, "CLAUDE_CONFIG_DIR": str(repo.parent / "claude")}
    cases = "\n".join(f"    {i}) {c} ;;" for i, c in enumerate(checks))
    script = f'''source "{ROOT}/scripts/issue-loop.sh"; ISSUE=5 CURRENT="#5" SESSION=impl-session
n=0
run_tests() {{ :; }}
run_agent() {{
  printf '%s | web=%s | %s\\n' "$(printf '%s' "$1" | head -1 | cut -c1-40)" "${{AGENT_WEB:-}}" "${{*:2}}" >> ../agents.log
  case $1 in
    "You are checking"*)
      case $n in
{cases}
      esac
      n=$((n + 1)); SESSION=check-session ;;
    *) {fixer} ;;
  esac
}}
check_and_fix; echo "session $SESSION"'''
    return subprocess.run(["bash", "-c", script], cwd=repo, capture_output=True, text=True, env=env)


def commit(file: str) -> str:
    return f"echo fixed > {file}; git -c user.name=t -c user.email=t@t add {file}; git -c user.name=t -c user.email=t@t commit -qm fix"


def test_a_failed_check_gets_one_fix_round_and_passes_when_the_fix_holds(branch):
    result = check_and_fix(branch, [FAIL_CHECK, "echo PASS > .loop/check.md"], commit("fix.txt"))
    assert result.returncode == 0, result.stderr
    assert "check passed" in result.stderr
    assert "session impl-session" in result.stdout
    agents = (branch.parent / "agents.log").read_text().splitlines()
    assert len(agents) == 3
    fix = agents[1]
    assert "--resume impl-session" in fix
    assert "web=1" in fix
    assert "web=1" not in agents[0] and "web=1" not in agents[2]


def test_the_fixer_is_given_the_checker_s_findings(branch):
    script_dir = branch.parent / "prompt.txt"
    fixer = f'printf "%s" "$1" > {script_dir}; {commit("fix.txt")}'
    check_and_fix(branch, [FAIL_CHECK, "echo PASS > .loop/check.md"], fixer)
    prompt = script_dir.read_text()
    assert "docs/x.md:3 states macOS behaviour" in prompt


def test_the_second_check_reads_both_reports(branch):
    fixer = f"echo '{{\"result\": \"Cited Apple.\"}}' > .loop/last.json; {commit('fix.txt')}"
    result = check_and_fix(branch, [FAIL_CHECK, "echo PASS > .loop/check.md"], fixer)
    assert result.returncode == 0, result.stderr
    report = (branch / ".loop" / "report.md").read_text()
    assert "Done. Nothing for you to decide." in report and "Cited Apple." in report


def test_a_check_that_fails_again_after_the_fix_stops_the_job(branch):
    result = check_and_fix(branch, [FAIL_CHECK, FAIL_CHECK], commit("fix.txt"))
    assert result.returncode != 0
    assert "stopped at #5: check failed after a fix round: a fact has no source" in result.stderr
    assert "claude --resume impl-session" in result.stderr


def test_a_fix_round_that_commits_nothing_stops_the_job(branch):
    result = check_and_fix(branch, [FAIL_CHECK, "echo PASS > .loop/check.md"], "true")
    assert result.returncode != 0
    assert "the fix round made no commit" in result.stderr
    assert len((branch.parent / "agents.log").read_text().splitlines()) == 2


# ── A failed job doesn't end the run

def run_jobs(repo: Path, body: str) -> subprocess.CompletedProcess:
    """Sources the script with `do_issue` replaced by `body` (bash), then runs `body`'s jobs."""
    script = (f'source "{ROOT}/scripts/issue-loop.sh"; sync_main() {{ echo "synced" >&2; }}; '
              f'{body}')
    return subprocess.run(["bash", "-c", script], cwd=repo, capture_output=True, text=True)


def test_a_job_that_fails_ends_only_its_own_job(branch):
    result = run_jobs(branch, 'do_issue() { CURRENT="#$1"; fail "tests red"; }; '
                              'run_job 5; echo "status $JOB_STATUS"; after_job 5; echo "after $FAILED"')
    assert "stopped at #5: tests red" in result.stderr
    assert "status 1" in result.stdout
    assert "synced" in result.stderr
    assert "after #5" in result.stdout
    assert result.returncode == 0, result.stderr


def test_an_unexpected_error_in_a_job_is_reported_and_the_run_goes_on(branch):
    result = run_jobs(branch, 'do_issue() { CURRENT="#$1"; false; echo "not reached"; }; '
                              'run_job 5; echo "status $JOB_STATUS"')
    assert "stopped at #5: unexpected error (exit 1)" in result.stderr
    assert "not reached" not in result.stdout
    assert "status 1" in result.stdout
    assert result.returncode == 0, result.stderr


def test_a_job_that_succeeds_resets_the_failure_count(branch):
    result = run_jobs(branch, 'do_issue() { :; }; FAILS_IN_ROW=1; '
                              'run_job 5; after_job 5; echo "status $JOB_STATUS fails $FAILS_IN_ROW"')
    assert "status 0 fails 0" in result.stdout, result.stderr


def test_two_failed_jobs_in_a_row_stop_the_run(branch):
    result = run_jobs(branch, 'do_issue() { CURRENT="#$1"; fail "agent ended with timeout"; }; '
                              'run_job 5; after_job 5; run_job 6; after_job 6; echo "still running"')
    assert result.returncode != 0
    assert "still running" not in result.stdout
    assert "2 jobs in a row failed" in result.stderr


def test_a_failed_job_that_leaves_uncommitted_changes_stops_the_run(branch):
    result = run_jobs(branch, 'do_issue() { CURRENT="#$1"; echo x > stray.txt; fail "uncommitted changes left behind"; }; '
                              'run_job 5; after_job 5; echo "still running"')
    assert result.returncode != 0
    assert "still running" not in result.stdout
    assert "uncommitted changes" in result.stderr and "synced" not in result.stderr
    assert (branch / "stray.txt").exists()
# ── Precheck: an issue only a person can finish is sent back to triage

def precheck(repo: Path, agent: str, gh_bin: Path) -> subprocess.CompletedProcess:
    """Runs precheck_issue on #5 with `agent` as the agent's run (bash, in the repo)."""
    env = {**os.environ, "PATH": f"{gh_bin}:{os.environ['PATH']}"}
    script = (f'source "{ROOT}/scripts/issue-loop.sh"; SESSION=; '
              f'run_agent() {{ SESSION=pre-session; echo "model $3" >&2; {agent}; }}; '
              f'precheck_issue 5 && echo agent || echo person')
    return subprocess.run(["bash", "-c", script], cwd=repo, capture_output=True, text=True, env=env)


def test_an_issue_an_agent_can_finish_goes_on_with_sonnet(branch, gh):
    result = precheck(branch, "echo 'AGENT: all criteria are code' > .loop/precheck.md", gh["bin"])
    assert result.stdout.strip() == "agent", result.stderr
    assert "model sonnet" in result.stderr
    assert "#5 precheck: all criteria are code" in result.stderr
    assert calls(gh) == []


def test_an_issue_that_needs_a_person_is_sent_back_to_triage_and_says_why(branch, gh):
    result = precheck(branch, "echo 'PERSON: \"the eval comparison\" needs the real-model eval' > .loop/precheck.md",
                      gh["bin"])
    assert result.stdout.strip() == "person", result.stderr
    assert "#5 skipped, needs a person: \"the eval comparison\"" in result.stderr
    log = gh["log"].read_text()
    assert "gh issue comment 5" in log and "ready-for-human" in log
    assert "gh issue edit 5 -R kinlace/parent-recap --remove-label ready-for-agent --add-label needs-triage" in log


def test_a_precheck_that_writes_no_verdict_stops_the_loop(branch, gh):
    result = precheck(branch, "true", gh["bin"])
    assert result.returncode != 0
    assert "stopped at #5: the precheck wrote no .loop/precheck.md" in result.stderr


def test_a_precheck_verdict_that_is_neither_agent_nor_person_stops_the_loop(branch, gh):
    result = precheck(branch, "echo 'Probably fine' > .loop/precheck.md", gh["bin"])
    assert result.returncode != 0
    assert "isn't AGENT or PERSON: Probably fine" in result.stderr


def test_a_precheck_that_changes_the_checkout_stops_the_loop(branch, gh):
    result = precheck(branch, "echo 'AGENT: fine' > .loop/precheck.md; echo x > stray.txt", gh["bin"])
    assert result.returncode != 0
    assert "the precheck changed the checkout" in result.stderr


# ── A usage limit moves the run to the next account

LIMIT = "You've hit your session limit · resets 5:30am (Europe/Helsinki)"


def agent_on_accounts(repo: Path, spent: list[str], fallbacks: str, body: str) -> subprocess.CompletedProcess:
    """Runs `body` with a fake `claude` that answers with the usage limit on every account in
    `spent` (a token, or `login` for no token) and logs each call's account and arguments."""
    bin_ = repo.parent / "bin"
    bin_.mkdir(exist_ok=True)
    fake = bin_ / "claude"
    fake.write_text(f"""#!/bin/bash
account=${{CLAUDE_CODE_OAUTH_TOKEN:-login}}
echo "$account | $*" >> "{repo.parent}/claude.log"
case " {' '.join(spent)} " in
  *" $account "*) printf '%s\\n' '{json.dumps({"type": "result", "subtype": "success", "is_error": True, "num_turns": 1, "session_id": "s1", "result": LIMIT}).replace("'", "'\\''")}' ;;
  *) echo '{{"type": "result", "subtype": "success", "is_error": false, "num_turns": 3, "session_id": "s1", "result": "Done."}}' ;;
esac
""")
    fake.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}", "CLAUDE_CODE_OAUTH_TOKEN": "start",
           "ISSUE_LOOP_FALLBACK_TOKENS": fallbacks}
    script = (f'source "{ROOT}/scripts/issue-loop.sh"; CURRENT="#5" BRANCH=$(git branch --show-current); '
              f'sync_main() {{ echo "synced" >&2; }}; {body}')
    return subprocess.run(["bash", "-c", script], cwd=repo, capture_output=True, text=True, env=env)


def token(repo: Path, name: str, value: str) -> str:
    path = repo.parent / name
    path.write_text(value)
    return str(path)


def claude_calls(repo: Path) -> list[str]:
    return (repo.parent / "claude.log").read_text().splitlines()


def test_a_usage_limit_resumes_the_session_on_the_next_account(branch):
    spare = token(branch, "spare", "spare-token")
    result = agent_on_accounts(branch, ["start"], f"{spare} login", 'run_agent "/implement #5" --model sonnet')
    assert result.returncode == 0, result.stderr
    first, second = claude_calls(branch)
    assert first.startswith("start | --model sonnet --session-id ")
    assert second.startswith("spare-token | --model sonnet --resume s1 -p A usage limit cut you off")
    assert "going on with account spare" in result.stderr


def test_the_run_stays_on_the_account_it_moved_to(branch):
    spare = token(branch, "spare", "spare-token")
    result = agent_on_accounts(branch, ["start"], spare,
                               'run_agent "/implement #5"; (run_agent "Now write the pull request" --resume s1)')
    assert result.returncode == 0, result.stderr
    assert [c.split(" | ")[0] for c in claude_calls(branch)] == ["start", "spare-token", "spare-token"]


def test_login_goes_on_with_the_logged_in_account(branch):
    spare = token(branch, "spare", "spare-token")
    result = agent_on_accounts(branch, ["start", "spare-token"], f"{spare} login", 'run_agent "/implement #5"')
    assert result.returncode == 0, result.stderr
    assert [c.split(" | ")[0] for c in claude_calls(branch)] == ["start", "spare-token", "login"]


def test_a_usage_limit_with_no_account_left_stops_the_job_and_says_so(branch):
    result = agent_on_accounts(branch, ["start"], "", 'run_agent "/implement #5"')
    assert result.returncode != 0
    assert f"stopped at #5: usage limit on every account: {LIMIT}" in result.stderr
    assert (branch / ".loop" / "quota-exhausted").exists()
    assert len(claude_calls(branch)) == 1


def test_a_job_that_ran_out_of_accounts_stops_the_run_and_keeps_its_work(branch):
    result = agent_on_accounts(branch, ["start"], "",
                               'do_issue() { CURRENT="#$1"; echo x > half-done.txt; run_agent "/implement #5"; }; '
                               'run_job 5; after_job 5; echo "still running"')
    assert result.returncode != 0
    assert "still running" not in result.stdout
    assert "#5 hit the usage limit on every account" in result.stderr and "synced" not in result.stderr
    assert (branch / "half-done.txt").exists()
