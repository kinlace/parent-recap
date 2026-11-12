"""Every GitHub Actions workflow parses, so a broken one fails here instead of silently running
no jobs. A tests workflow that doesn't parse reports no check at all, and PRs then merge on the
other checks alone (#128)."""
from __future__ import annotations

import os
import re
import subprocess
import tomllib
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_DIR = ROOT / ".github" / "workflows"
WORKFLOWS = sorted(WORKFLOW_DIR.glob("*.yml"))
# What the windows-install and linux-install jobs run, one check a step (#720, #721).
WINDOWS_CHECKS = ROOT / "scripts" / "windows-install-checks.ps1"
LINUX_CHECKS = ROOT / "scripts" / "linux-install-checks.sh"


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


def test_each_windows_install_step_runs_one_check_of_the_checks_script():
    # The expected values about the program live in the script, which asks the installed program
    # where it can, so a program change edits no file under .github/ (#720).
    steps = yaml.safe_load((WORKFLOW_DIR / "tests.yml").read_text())["jobs"]["windows-install"]["steps"]
    script = WINDOWS_CHECKS.read_text()
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


def test_each_linux_install_step_runs_one_check_of_the_checks_script():
    # As the windows-install job: the expected values about the program live in the script, so a
    # program change edits no file under .github/ (#721).
    steps = yaml.safe_load((WORKFLOW_DIR / "tests.yml").read_text())["jobs"]["linux-install"]["steps"]
    script = LINUX_CHECKS.read_text()
    known = set(re.findall(r"(?m)^\s+([a-z-]+)\) check_", script))
    runs = [step for step in steps if "run" in step]
    called = [re.fullmatch(r"scripts/linux-install-checks\.sh ([a-z-]+)", step["run"].strip())
              for step in runs]

    assert known
    assert all(called), [step["run"] for step, call in zip(runs, called) if not call]
    assert all(step.get("name") for step in runs)
    names = [call.group(1) for call in called if call]
    assert len(names) == len(set(names)) and set(names) == known
    assert os.access(LINUX_CHECKS, os.X_OK)


@pytest.mark.posix
def test_a_failed_linux_install_check_says_which_check_and_what_it_saw(tmp_path: Path):
    # Nothing installed in this home, so the installed program can't run.
    env = {**os.environ, "HOME": str(tmp_path)}
    result = subprocess.run(["bash", str(LINUX_CHECKS), "own-runtimes"], env=env,
                            capture_output=True, text=True)

    assert result.returncode == 1
    annotation = next(line for line in result.stdout.splitlines() if line.startswith("::error"))
    assert annotation.startswith("::error title=linux-install own-runtimes::")
    assert "parent-recap --help" in annotation


@pytest.mark.posix
def test_an_unknown_linux_install_check_fails_and_lists_the_checks():
    result = subprocess.run(["bash", str(LINUX_CHECKS), "no-such-check"],
                            capture_output=True, text=True)

    assert result.returncode == 2
    assert "own-runtimes" in result.stderr


def test_the_windows_install_checks_are_ascii_for_windows_powershell():
    # Windows PowerShell 5.1 reads a file without a BOM in the PC's code page.
    WINDOWS_CHECKS.read_bytes().decode("ascii")


def test_a_change_to_a_script_still_runs_the_tests():
    # The `changes` job skips the tests only for the docs its grep leaves out.
    steps = yaml.safe_load((WORKFLOW_DIR / "tests.yml").read_text())["jobs"]["changes"]["steps"]
    run = next(step["run"] for step in steps if step.get("id") == "diff")
    docs_only = re.search(r"grep -v -E \\\n\s*'([^']*)'", run).group(1)
    assert re.search(docs_only, "docs/adr/0001-x.md")
    assert not re.search(docs_only, "scripts/windows-install-checks.ps1")
    assert not re.search(docs_only, "scripts/linux-install-checks.sh")


# The release zip's signature (#912, ADR 0047). The signing and verifying steps run here as
# written, with a key pair of the test's own; no private key is in the repository.

RELEASE = WORKFLOW_DIR / "release.yml"


def release_jobs() -> dict:
    return yaml.safe_load(RELEASE.read_text())["jobs"]


def release_step(job: str, name: str) -> dict:
    return next(step for step in release_jobs()[job]["steps"] if step.get("name") == name)


def run_release_step(job: str, name: str, tree: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    """Runs a step's script as GitHub does (`bash -e`), with `tree` as the checkout."""
    script = tree.parent / f"{job}-{name.replace(' ', '-')}.sh"
    script.write_text(release_step(job, name)["run"])
    job_env = {key: value for key, value in release_jobs()[job]["env"].items() if "${{" not in value}
    return subprocess.run(["bash", "-e", str(script)], cwd=tree, capture_output=True, text=True,
                          env={**os.environ, **job_env, "TAG": "v1.2.3", **env})


def outputs(path: Path) -> dict[str, str]:
    """The `name<<DELIMITER` values a step wrote to its GITHUB_OUTPUT file."""
    found, lines = {}, path.read_text().splitlines()
    while lines:
        name, delimiter = lines.pop(0).split("<<")
        end = lines.index(delimiter)
        found[name], lines = "\n".join(lines[:end]), lines[end + 1:]
    return found


@pytest.fixture
def release_tree(tmp_path: Path) -> dict:
    """A checkout with a built zip, whose tree holds a key of the test's own as the workflow's."""
    tree = tmp_path / "tree"
    (tree / "dist").mkdir(parents=True)
    (tree / "dist" / "parent-recap-1.2.3.zip").write_bytes(b"PK\x03\x04 the release" * 100)
    key = tmp_path / "workflow-key"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "", "-f", str(key)],
                   check=True)
    signers = tree / "app" / "src" / "family_brief" / "release_signers"
    signers.parent.mkdir(parents=True)
    signers.write_text(f'workflow namespaces="{RELEASE_NAMESPACE}" '
                       f'{key.with_suffix(".pub").read_text()}')
    runner_temp = tmp_path / "runner-temp"
    runner_temp.mkdir()
    return {"tree": tree, "key": key.read_text(), "runner_temp": runner_temp,
            "output": tmp_path / "github-output", "signers": signers,
            "zip": tree / "dist" / "parent-recap-1.2.3.zip"}


def sign_in(release_tree: dict, key: str) -> subprocess.CompletedProcess:
    return run_release_step("sign", "Sign zip", release_tree["tree"], {
        "RELEASE_SIGNING_KEY": key, "RUNNER_TEMP": str(release_tree["runner_temp"]),
        "GITHUB_OUTPUT": str(release_tree["output"])})


def signed_in(release_tree: dict) -> str:
    """Signs the zip with the test's key and returns the signature the sign job hands on."""
    result = sign_in(release_tree, release_tree["key"])
    assert result.returncode == 0, result.stdout + result.stderr
    # As on the publish job's runner, which has only the signature the sign job passed it.
    release_tree["zip"].with_name(release_tree["zip"].name + ".sig").unlink()
    return outputs(release_tree["output"])["signature"]


def verify_in(release_tree: dict, signature: str) -> subprocess.CompletedProcess:
    return run_release_step("publish", "Verify the signature", release_tree["tree"],
                            {"SIGNATURE": signature})


RELEASE_NAMESPACE = "release@parentrecap.com"


def test_the_workflow_signs_under_the_namespace_and_keys_the_program_verifies_with():
    from family_brief.release_signature import NAMESPACE, SIGNERS

    assert NAMESPACE == RELEASE_NAMESPACE
    jobs = release_jobs()
    assert jobs["sign"]["env"]["NAMESPACE"] == jobs["publish"]["env"]["NAMESPACE"] == NAMESPACE
    assert SIGNERS.relative_to(ROOT).as_posix() in release_step("publish", "Verify the signature")["run"]


def test_only_the_signing_step_gets_the_key_in_a_protected_environment_with_read_only_access():
    jobs = release_jobs()
    sign = jobs["sign"]
    assert sign["environment"] == "release-signing"
    assert sign["permissions"] == {"contents": "read"}
    assert sign["steps"][0]["with"]["persist-credentials"] is False
    # The only mention of a secret in the workflow is the signing step's env.
    assert RELEASE.read_text().count("secrets.") == 1
    assert release_step("sign", "Sign zip")["env"] == {
        "RELEASE_SIGNING_KEY": "${{ secrets.RELEASE_SIGNING_KEY }}"}
    assert all("environment" not in job for name, job in jobs.items() if name != "sign")


def test_the_signature_is_verified_before_anything_is_published_and_published_beside_the_zip():
    jobs = release_jobs()
    assert jobs["sign"]["needs"] == "test" and jobs["publish"]["needs"] == "sign"
    names = [step.get("name") for step in jobs["publish"]["steps"]]
    assert names.index("Verify the signature") < names.index("Publish GitHub Release") \
        < names.index("Move stable to the tag")
    publish = release_step("publish", "Publish GitHub Release")["run"]
    assert '"dist/parent-recap-${TAG#v}.zip" "dist/parent-recap-${TAG#v}.zip.sig"' in publish


@pytest.mark.posix
def test_the_signing_step_signs_the_zip_and_the_verify_step_accepts_it(release_tree):
    signature = signed_in(release_tree)
    # The key file is gone once the step ends.
    assert list(release_tree["runner_temp"].iterdir()) == []
    assert signature.startswith("-----BEGIN SSH SIGNATURE-----")

    verified = verify_in(release_tree, signature)

    assert verified.returncode == 0, verified.stdout + verified.stderr
    sig_file = release_tree["zip"].with_name(release_tree["zip"].name + ".sig")
    assert sig_file.read_text().strip() == signature.strip()


@pytest.mark.posix
def test_the_signing_step_fails_when_the_secret_is_missing(release_tree):
    signed = sign_in(release_tree, "")

    assert signed.returncode == 1
    assert "::error::The secret RELEASE_SIGNING_KEY" in signed.stdout
    assert not release_tree["zip"].with_name(release_tree["zip"].name + ".sig").exists()


@pytest.mark.posix
def test_the_verify_step_fails_for_a_signature_the_tree_key_did_not_make(release_tree):
    signature = signed_in(release_tree)
    # The tree's real keys, not the one this test signed with.
    release_tree["signers"].write_text((ROOT / "app/src/family_brief/release_signers").read_text())

    verified = verify_in(release_tree, signature)

    assert verified.returncode == 1
    assert "::error::The signature of dist/parent-recap-1.2.3.zip doesn't verify" in verified.stdout


@pytest.mark.posix
def test_the_verify_step_fails_for_a_zip_other_than_the_one_signed(release_tree):
    signature = signed_in(release_tree)
    release_tree["zip"].write_bytes(release_tree["zip"].read_bytes() + b"changed")

    assert verify_in(release_tree, signature).returncode == 1
