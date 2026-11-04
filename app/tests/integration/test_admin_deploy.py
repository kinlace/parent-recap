"""`admin deploy` (ops/gateway/admin, #743, ADR 0036): a release tag, main or a commit on main is
fetched from the repo, which is a real git repo under tmp_path here, and anything else is refused
before anything changes; an unreleased commit only once its `test` check passed on GitHub, faked
here; never while an evening run is due or running, or `admin households` marks a Household,
unless --anyway; each deploy kept under its commit and recorded, which `admin households` says;
and checked after it is installed. gateway-setup.sh, systemctl, sudo and HTTP are faked."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from test_admin import Server, admin

GIT_ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
# 12:00 UTC is 14:00 in Helsinki, where the evening times are.
NOON = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", *args],
                          cwd=repo, env=GIT_ENV, check=True, capture_output=True,
                          text=True).stdout.strip()


class Origin:
    """The public repo: v1.0.0 on main, an unreleased commit after it, and a branch."""

    def __init__(self, root: Path) -> None:
        self.path = root / "origin"
        self.path.mkdir()
        git(self.path, "init", "-q", "-b", "main")
        # As in the real repo: the release zip leaves ops/ out, and a deploy still needs it.
        (self.path / ".gitattributes").write_text("/ops export-ignore\n")
        (self.path / "ops" / "gateway").mkdir(parents=True)
        (self.path / "ops" / "gateway" / "gateway-setup.sh").write_text("# v1\n")
        git(self.path, "add", "-A")
        git(self.path, "commit", "-qm", "one")
        git(self.path, "tag", "v1.0.0")
        self.release = git(self.path, "rev-parse", "HEAD")
        (self.path / "ops" / "gateway" / "gateway-setup.sh").write_text("# v1 and a fix\n")
        git(self.path, "commit", "-qam", "two")
        self.unreleased = git(self.path, "rev-parse", "HEAD")
        git(self.path, "checkout", "-q", "-b", "feature")
        git(self.path, "commit", "-q", "--allow-empty", "-m", "three")
        self.branch = git(self.path, "rev-parse", "HEAD")
        git(self.path, "checkout", "-q", "main")


class FakeEdge:
    """The server around `deploy`: gateway-setup.sh points `current` at the deploy it was given,
    each Household's evening job is a systemd unit, run-now runs it, and GitHub's check runs."""

    def __init__(self, server: Server) -> None:
        self.server = server
        self.ran: list[tuple[list[str], dict]] = []
        self.checks: dict[str, list[dict]] = {}
        self.statuses: dict[str, int] = {}
        self.marked = False  # admin households marks a Household
        self.setup_fails = False
        self.run_ends = "success"
        self.units: dict[str, dict[str, str]] = {}  # by user, the evening job's properties
        self.clock = 0.0
        self.asked: list[str] = []

    def edge(self) -> admin.Edge:
        return admin.Edge(system=self.system, github=self.github, http_status=self.http_status,
                          now=lambda: self.now, monotonic=lambda: self.clock, sleep=self.sleep)

    now = NOON

    def system(self, argv: list[str], capture: bool = False,
               env: dict | None = None) -> subprocess.CompletedProcess:
        self.ran.append((argv, env or {}))
        out, code = "", 0
        if argv[0] == "bash":
            code = 1 if self.setup_fails else 0
            if not self.setup_fails:
                current = self.server.paths.shared / "current"
                self.server.paths.shared.mkdir(parents=True, exist_ok=True)
                if current.is_symlink():
                    current.unlink()
                current.symlink_to(env["PARENT_RECAP_DEPLOY"])
        elif argv[0] == "systemctl":
            user = argv[3].rstrip("@")
            unit = self.units.get(user, {"ActiveState": "inactive", "Result": "success",
                                         "ExecMainStatus": "0",
                                         "ExecMainStartTimestampMonotonic": "100"})
            if argv[-1] != admin.EVENING_JOB:
                unit = {"ActiveState": "inactive"}
            out = "\n".join(f"{k}={v}" for k, v in unit.items())
        elif argv[-2:] == ["signup", "status"]:
            code = 1 if self.marked else 0
        elif argv[-2:] == ["schedule", "run-now"]:
            user = argv[2]
            # It starts, and has ended by the next look but one.
            self.units[user] = {"ActiveState": "activating", "Result": "success",
                                "ExecMainStatus": "0", "ExecMainStartTimestampMonotonic": "200"}
        return subprocess.CompletedProcess(argv, code, out, "")

    def sleep(self, seconds: float) -> None:
        self.clock += seconds
        for unit in self.units.values():
            if unit["ActiveState"] == "activating":
                unit.update(ActiveState="failed" if self.run_ends != "success" else "inactive",
                            Result=self.run_ends, ExecMainStatus="0" if self.run_ends ==
                            "success" else "1")

    def github(self, url: str) -> dict:
        self.asked.append(url)
        commit = url.split("/commits/")[1].split("/")[0]
        return {"check_runs": self.checks.get(commit, [])}

    def http_status(self, url: str) -> int:
        return self.statuses.get(url, 404 if url.startswith("https://") else 200)

    def setups(self) -> list[dict]:
        return [env for argv, env in self.ran if argv[0] == "bash"]


@pytest.fixture
def origin(tmp_path: Path) -> Origin:
    return Origin(tmp_path)


@pytest.fixture
def deploy(tmp_path: Path, origin: Origin):
    server = Server(tmp_path)
    server.paths.repo_url = str(origin.path)
    server.paths.github_api = "https://api.github.example/repos/kinlace/parent-recap"
    server.paths.repo = tmp_path / "state" / "repo.git"
    server.paths.record = tmp_path / "state" / "deploys.json"
    server.paths.sources = tmp_path / "sources"
    server.paths.deploy_env = tmp_path / "etc" / "deploy.env"
    server.paths.shared = tmp_path / "shared"
    fake = FakeEdge(server)

    def run(*args: str) -> int:
        try:
            return admin.main(list(args), paths=server.paths, is_root=lambda: True,
                              exec_as=lambda argv, env: 0, edge=fake.edge())
        except SystemExit as e:
            return e.code if isinstance(e.code, int) else 1
    run.server, run.fake, run.origin = server, fake, origin
    return run


def household(deploy, label: str = "ph-30d2afe0", evening: str = "21:00",
              check: bool = True) -> None:
    store = deploy.server.store / "households.json"
    listed = json.loads(store.read_text()) if store.exists() else []
    listed.append({"user": label, "evening": evening, "created": "2026-11-01"})
    store.write_text(json.dumps(listed))
    if check:
        deploy.server.paths.deploy_env.parent.mkdir(parents=True, exist_ok=True)
        deploy.server.paths.deploy_env.write_text(f"CHECK_HOUSEHOLD={label}\n")


def records(deploy) -> list[dict]:
    path = deploy.server.paths.record
    return json.loads(path.read_text()) if path.exists() else []


def nothing_changed(deploy) -> None:
    assert deploy.fake.setups() == [] and records(deploy) == []
    assert not any(deploy.server.paths.sources.glob("*")) \
        if deploy.server.paths.sources.exists() else True


@pytest.fixture(autouse=True)
def _symlinks(tmp_path: Path) -> None:
    try:
        (tmp_path / "link").symlink_to("x")
    except OSError:
        pytest.skip("this OS won't make a symlink here, which `current` is")
    (tmp_path / "link").unlink()


# ── What is deployed


def test_a_release_tag_is_checked_out_whole_installed_by_its_commit_and_recorded(deploy, capsys):
    assert deploy("deploy", "v1.0.0") == 0
    short = deploy.origin.release[:12]
    [env] = deploy.fake.setups()
    source = deploy.server.paths.sources / short
    assert env == {"GATEWAY_HOST": "gateway.example", "PARENT_RECAP_RELEASE": str(source),
                   "PARENT_RECAP_DEPLOY": short}
    # ops/ comes with it, though the release zip leaves it out.
    assert (source / "ops" / "gateway" / "gateway-setup.sh").read_text() == "# v1\n"
    assert records(deploy) == [{"commit": deploy.origin.release, "tag": "v1.0.0",
                                "at": "2026-11-04T12:00:00+00:00"}]
    # A release's tests ran before it was published: GitHub isn't asked.
    assert deploy.fake.asked == []
    out = capsys.readouterr().out
    assert f"the server runs {deploy.origin.release} (v1.0.0)" in out


def test_main_deploys_its_unreleased_commit_once_its_test_check_passed(deploy):
    deploy.fake.checks[deploy.origin.unreleased] = [{"name": "test", "conclusion": "success"}]
    assert deploy("deploy", "main") == 0
    assert records(deploy)[0]["commit"] == deploy.origin.unreleased
    assert records(deploy)[0]["tag"] == "unreleased"
    assert deploy.fake.asked == [
        f"https://api.github.example/repos/kinlace/parent-recap/commits/"
        f"{deploy.origin.unreleased}/check-runs?check_name=test&filter=latest"]


def test_a_commit_on_main_by_its_hash_is_deployed(deploy):
    deploy.fake.checks[deploy.origin.unreleased] = [{"name": "test", "conclusion": "skipped"}]
    assert deploy("deploy", deploy.origin.unreleased[:9]) == 0
    assert records(deploy)[0]["commit"] == deploy.origin.unreleased


def test_a_tagged_commit_by_its_hash_is_its_release(deploy):
    assert deploy("deploy", deploy.origin.release) == 0
    assert records(deploy)[0]["tag"] == "v1.0.0" and deploy.fake.asked == []


@pytest.mark.parametrize("ref", ["feature", "BRANCH", "v9.9.9", "deadbeef00", "../main",
                                 "--anyway"])
def test_anything_but_a_tag_or_a_commit_on_main_is_refused_before_anything_changes(
        deploy, capsys, ref):
    assert deploy("deploy", ref) in (1, 2)
    nothing_changed(deploy)
    assert capsys.readouterr().err


def test_a_commit_only_on_another_branch_is_refused(deploy, capsys):
    deploy.fake.checks[deploy.origin.branch] = [{"name": "test", "conclusion": "success"}]
    assert deploy("deploy", deploy.origin.branch) == 1
    nothing_changed(deploy)
    assert "nothing changed" in capsys.readouterr().err


@pytest.mark.parametrize("runs", [[], [{"name": "test", "conclusion": "failure"}],
                                  [{"name": "test", "conclusion": None}],
                                  [{"name": "test", "conclusion": "success"},
                                   {"name": "test", "conclusion": "cancelled"}]])
def test_an_unreleased_commit_without_a_passed_test_check_is_refused(deploy, capsys, runs):
    deploy.fake.checks[deploy.origin.unreleased] = runs
    assert deploy("deploy", "main") == 1
    nothing_changed(deploy)
    assert "test check" in capsys.readouterr().err


def test_github_not_answering_refuses_an_unreleased_commit(deploy, capsys):
    def down(url: str) -> dict:
        raise OSError("no route to host")
    deploy.fake.github = down
    assert deploy("deploy", "main") == 1
    nothing_changed(deploy)
    assert "no route to host" in capsys.readouterr().err


def test_a_server_never_set_up_is_refused(deploy, capsys):
    deploy.server.paths.signup_env.unlink()
    assert deploy("deploy", "v1.0.0") == 1
    nothing_changed(deploy)
    assert "gateway-setup.sh" in capsys.readouterr().err


# ── The evening runs and the Households' status


@pytest.mark.parametrize("evening,now", [("14:20", NOON), ("14:30", NOON),
                                         ("00:10", datetime(2026, 11, 4, 21, 50, tzinfo=UTC))])
def test_an_evening_run_due_within_30_minutes_refuses_the_deploy(deploy, capsys, evening, now):
    household(deploy, evening=evening)
    deploy.fake.now = now
    assert deploy("deploy", "v1.0.0") == 1
    nothing_changed(deploy)
    assert f"due at {evening}" in capsys.readouterr().err


@pytest.mark.parametrize("evening", ["14:31", "13:59", "21:00"])
def test_an_evening_run_further_off_or_just_past_lets_it_go_on(deploy, evening):
    household(deploy, evening=evening)
    assert deploy("deploy", "v1.0.0") == 0


@pytest.mark.parametrize("anyway", [(), ("--anyway",)])
def test_an_evening_run_still_running_refuses_the_deploy_even_anyway(deploy, capsys, anyway):
    household(deploy)
    household(deploy, "ph-0123abcd", check=False)
    deploy.fake.units["ph-0123abcd"] = {"ActiveState": "activating"}
    deploy.server.paths.deploy_env.write_text("CHECK_HOUSEHOLD=ph-30d2afe0\n")
    assert deploy("deploy", "v1.0.0", *anyway) == 1
    nothing_changed(deploy)
    assert "ph-0123abcd's evening run is still running" in capsys.readouterr().err


def test_a_household_admin_households_marks_refuses_the_deploy(deploy, capsys):
    household(deploy)
    deploy.fake.marked = True
    assert deploy("deploy", "v1.0.0") == 1
    nothing_changed(deploy)
    assert "--anyway" in capsys.readouterr().err
    [(argv, _)] = [r for r in deploy.fake.ran if r[0][-2:] == ["signup", "status"]]
    assert argv[:3] == ["sudo", "-u", "parent-recap-signup"]


def test_anyway_deploys_the_fix_for_what_is_marked_and_in_the_evening(deploy, capsys):
    household(deploy, evening="14:10")
    deploy.fake.marked = True
    assert deploy("deploy", "v1.0.0", "--anyway") == 0
    assert len(records(deploy)) == 1
    assert "deploying anyway" in capsys.readouterr().out


def test_households_without_a_check_household_named_are_refused(deploy, capsys):
    household(deploy, check=False)
    assert deploy("deploy", "v1.0.0") == 1
    nothing_changed(deploy)
    assert "CHECK_HOUSEHOLD" in capsys.readouterr().err


# ── Kept by commit, recorded, checked


def test_each_deploy_stays_on_disk_under_its_commit_and_is_recorded(deploy, capsys):
    deploy.fake.checks[deploy.origin.unreleased] = [{"name": "test", "conclusion": "success"}]
    assert deploy("deploy", "v1.0.0") == 0
    assert deploy("deploy", "main") == 0
    # The unreleased commit carries v1.0.0's version, and the release is still there.
    names = sorted(p.name for p in deploy.server.paths.sources.iterdir())
    assert names == sorted([deploy.origin.release[:12], deploy.origin.unreleased[:12]])
    assert [d["tag"] for d in records(deploy)] == ["v1.0.0", "unreleased"]
    assert [env["PARENT_RECAP_DEPLOY"] for env in deploy.fake.setups()] == [
        deploy.origin.release[:12], deploy.origin.unreleased[:12]]
    capsys.readouterr()
    assert deploy("households") == 0
    out = capsys.readouterr().out
    assert out.startswith(f"the server runs {deploy.origin.unreleased} (unreleased")
    assert "until a release tag is deployed" in out


def test_households_says_when_what_runs_isn_t_recorded(deploy, capsys):
    deploy.server.paths.shared.mkdir()
    (deploy.server.paths.shared / "current").symlink_to("0.11.1")
    assert deploy("households") == 0
    assert capsys.readouterr().out.startswith("the server runs 0.11.1, which isn't recorded")


def test_a_failed_checkout_of_a_commit_deployed_before_keeps_its_folder(deploy, monkeypatch):
    assert deploy("deploy", "v1.0.0") == 0
    source = deploy.server.paths.sources / deploy.origin.release[:12]
    real_run = admin.subprocess.run

    def no_clone(argv, **kw):
        if argv[:2] == ["git", "clone"]:
            return subprocess.CompletedProcess(argv, 128, "", "fatal: disk full")
        return real_run(argv, **kw)
    monkeypatch.setattr(admin.subprocess, "run", no_clone)
    assert deploy("deploy", "v1.0.0") == 1
    assert (source / "ops" / "gateway" / "gateway-setup.sh").exists()
    assert len(records(deploy)) == 1


def test_a_failed_install_is_not_recorded(deploy, capsys):
    deploy.fake.setup_fails = True
    assert deploy("deploy", "v1.0.0") == 1
    assert records(deploy) == []
    assert "isn't recorded" in capsys.readouterr().err


def test_it_runs_the_check_household_s_evening_run_and_says_it_ended_ok(deploy, capsys):
    household(deploy)
    assert deploy("deploy", "v1.0.0") == 0
    [(argv, _)] = [r for r in deploy.fake.ran if r[0][-1] == "run-now"]
    assert argv == ["sudo", "-u", "ph-30d2afe0", "-H", "--", deploy.server.paths.program,
                    "schedule", "run-now"]
    out = capsys.readouterr().out
    assert "✅ ph-30d2afe0's evening run ended ok" in out
    assert "https://gateway.example/ outside its routes: 404" in out


def test_a_failed_evening_run_fails_the_deploy_and_says_how_to_switch_back(deploy, capsys):
    household(deploy)
    deploy.fake.run_ends = "exit-code"
    assert deploy("deploy", "v1.0.0") == 1
    assert len(records(deploy)) == 1  # it is what runs now
    captured = capsys.readouterr()
    assert "❌ ph-30d2afe0's evening run failed (exit-code, exit 1)" in captured.out
    assert "switch back" in captured.err


@pytest.mark.parametrize("url,status", [("https://gateway.example/", 200),
                                        ("http://127.0.0.1:8082/health", 0)])
def test_a_public_address_answering_or_a_service_down_fails_the_deploy(deploy, capsys, url,
                                                                       status):
    deploy.fake.statuses[url] = status
    assert deploy("deploy", "v1.0.0") == 1
    assert "❌" in capsys.readouterr().out


def test_an_evening_run_that_never_ends_is_said_after_the_wait(deploy, capsys):
    household(deploy)
    deploy.fake.sleep = lambda s: setattr(deploy.fake, "clock", deploy.fake.clock + s)
    assert deploy("deploy", "v1.0.0") == 1
    assert "hasn't ended after 20 minutes" in capsys.readouterr().out


def test_deploy_is_listed_in_help(deploy, capsys):
    assert deploy("--help") == 0
    assert "  deploy vX.Y.Z|main|COMMIT" in capsys.readouterr().out
