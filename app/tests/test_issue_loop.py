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
  "pr checks"*--json*) cat "{tmp_path}/checks.json" ;;
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
