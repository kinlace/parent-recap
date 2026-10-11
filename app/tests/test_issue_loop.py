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


def statuses(backlog: Path, issues: list[dict], args: str = "") -> list[str]:
    """What the script says about each issue, in the order it would take them."""
    (backlog / "issues.json").write_text(json.dumps(issues))
    env = {**os.environ, "PATH": f"{backlog / 'bin'}:{os.environ['PATH']}"}
    script = f'source scripts/issue-loop.sh; set -- {args}; PICKS="$*"; issue_statuses'
    out = subprocess.run(["bash", "-c", script], cwd=ROOT, capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stderr
    return [line.replace("\t", " ") for line in out.stdout.splitlines()]


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
    assert out.stdout.split() == ["4", "ready"]


def test_named_issues_that_cannot_be_worked_say_why(backlog):
    got = statuses(backlog, [
        issue(1, 10, title="Spec: big"), issue(2, 10, state="CLOSED"), issue(3, 10, assignees=["sam"]),
    ], "1 2 3 99")
    assert got == ["1 skipped: spec", "2 skipped: closed", "3 skipped: assigned to sam",
                   "99 skipped: no such issue"]


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


def test_a_failed_check_stops_the_loop_and_points_at_the_implementer_s_session(branch):
    result = check(branch, "printf 'FAIL: a question is left for a person\\n- #5 asks for a decision\\n' > .loop/check.md")
    assert result.returncode != 0
    assert "stopped at #5: check failed: a question is left for a person" in result.stderr
    assert "claude --resume impl-session" in result.stderr


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
