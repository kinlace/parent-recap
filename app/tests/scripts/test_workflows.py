"""Every GitHub Actions workflow parses, so a broken one fails here instead of silently running
no jobs. A tests workflow that doesn't parse reports no check at all, and PRs then merge on the
other checks alone (#128)."""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest
import yaml

WORKFLOW_DIR = Path(__file__).resolve().parents[3] / ".github" / "workflows"
WORKFLOWS = sorted(WORKFLOW_DIR.glob("*.yml"))


def test_there_are_workflows():
    assert {p.name for p in WORKFLOWS} >= {"tests.yml", "release.yml"}


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_the_workflow_parses_and_has_jobs(path: Path):
    workflow = yaml.safe_load(path.read_text())
    assert isinstance(workflow, dict) and workflow.get("jobs"), f"{path.name} has no jobs"
    for name, job in workflow["jobs"].items():
        assert job.get("steps") or job.get("uses"), f"{path.name}: job {name} has no steps"


def test_a_hung_test_fails_each_tests_job_within_40_minutes():
    # Without a limit, GitHub cancels it only after 6 hours (#216). The windows job needs about
    # 18 minutes when green (#348), so 20 was too tight for it.
    workflow = yaml.safe_load((WORKFLOW_DIR / "tests.yml").read_text())
    for job in workflow["jobs"].values():
        assert job["timeout-minutes"] <= 40


def test_the_tests_run_on_macos_windows_and_linux_on_both_chips():
    # `changes` only decides whether they run, on a cheaper runner.
    workflow = yaml.safe_load((WORKFLOW_DIR / "tests.yml").read_text())
    jobs = {name: job for name, job in workflow["jobs"].items() if name != "changes"}
    runners = {runner for job in jobs.values()
               for runner in job.get("strategy", dict()).get("matrix", dict()).get("runner", [])
               or [job["runs-on"]]}
    assert runners == {"macos-latest", "windows-latest", "ubuntu-latest", "ubuntu-24.04",
                       "ubuntu-24.04-arm"}
    assert all(job["needs"] == "changes" for job in jobs.values())


def test_the_macos_job_reports_coverage_with_no_threshold_and_windows_doesnt():
    # Numbers to go on when end-to-end tests give way to unit tests (#403), not a gate.
    jobs = yaml.safe_load((WORKFLOW_DIR / "tests.yml").read_text())["jobs"]
    macos_runs = " ".join(step.get("run", "") for step in jobs["test"]["steps"])
    windows_runs = " ".join(step.get("run", "") for step in jobs["windows"]["steps"])

    assert "--cov" in macos_runs and "--cov-report=term" in macos_runs
    assert "fail-under" not in macos_runs
    assert any(step.get("uses", "").startswith("actions/upload-artifact@")
               and step["with"]["path"] == "app/htmlcov" for step in jobs["test"]["steps"])
    assert "--cov" not in windows_runs


def test_the_macos_job_runs_its_slow_layers_on_every_core():
    # They took most of its 7 to 8 minutes in one process on a 3-core runner (#509).
    steps = yaml.safe_load((WORKFLOW_DIR / "tests.yml").read_text())["jobs"]["test"]["steps"]
    runs = {step["name"]: step["run"] for step in steps if "pytest" in step.get("run", "")}
    for name in ("End-to-end tests", "Script tests", "Browser tests"):
        assert re.search(r" -n (auto|\d+)\b", runs[name]), name


def test_coverage_measures_lines_and_branches_of_the_package_with_no_threshold():
    pyproject = tomllib.loads((WORKFLOW_DIR.parents[1] / "app" / "pyproject.toml").read_text())
    coverage = pyproject["tool"]["coverage"]
    assert coverage["run"]["source"] == ["family_brief"] and coverage["run"]["branch"] is True
    assert "fail_under" not in coverage.get("report", {})


def test_the_tests_workflow_gets_a_read_only_token_of_its_own_accord():
    # Its PR jobs run repo scripts an unattended agent may change (#720), which is safe only while
    # they get no write token; the repo's default says so today, the workflow says it itself.
    workflow = yaml.safe_load((WORKFLOW_DIR / "tests.yml").read_text())
    assert workflow["permissions"] == {"contents": "read"}


CHECKS = WORKFLOW_DIR.parents[1] / "scripts" / "windows-install-checks.ps1"


def test_each_windows_install_step_runs_one_of_the_script_s_checks():
    # The expected values about the program live in the script, which asks the installed program
    # where it can, so a program change edits no file under .github/ (#720).
    steps = yaml.safe_load((WORKFLOW_DIR / "tests.yml").read_text())["jobs"]["windows-install"]["steps"]
    script = CHECKS.read_text()
    offered = re.search(r"ValidateSet\(([^)]*)\)", script).group(1)
    known = set(re.findall(r"'([a-z-]+)'", offered))
    runs = [step for step in steps if "run" in step]
    called = [re.fullmatch(r"scripts\\windows-install-checks\.ps1 ([a-z-]+)", step["run"].strip())
              for step in runs]

    assert all(called), [step["run"] for step, call in zip(runs, called) if not call]
    assert all(step.get("name") for step in runs)
    names = [call.group(1) for call in called if call]
    assert len(names) == len(set(names)) and set(names) == known
    for check in known:
        assert f"'{check}' = {{" in script, f"{check} runs nothing"


def test_the_windows_install_checks_are_ascii_for_windows_powershell():
    # Windows PowerShell 5.1 reads a file without a BOM in the PC's code page.
    CHECKS.read_bytes().decode("ascii")


def test_a_change_to_a_script_still_runs_the_tests():
    # The `changes` job skips the tests only for the docs its grep leaves out.
    run = yaml.safe_load((WORKFLOW_DIR / "tests.yml").read_text())["jobs"]["changes"]["steps"][1]["run"]
    docs_only = re.search(r"grep -v -E \\\n\s*'([^']*)'", run).group(1)
    assert re.search(docs_only, "docs/adr/0001-x.md")
    assert not re.search(docs_only, "scripts/windows-install-checks.ps1")
