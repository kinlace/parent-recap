"""`admin deploy` (ops/gateway/admin, #743, ADR 0036): a release tag, main or a commit on main is
fetched from the repo, which is a real git repo under tmp_path here, and anything else is refused
before anything changes; an unreleased commit only once its `test` check passed on GitHub, faked
here, or, while its own is missing or running, that of its PR's head with the same code built on
main as it was when the PR landed (#818); never while an evening run is due or running, or `admin households` marks a Household,
unless --anyway; each deploy kept under its commit and recorded, which `admin households` says;
and checked after it is installed. gateway-setup.sh, systemctl, sudo and HTTP are faked."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from test_admin import Server, admin

GIT_ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
# 12:00 UTC is 14:00 in Helsinki, where the evening times are.
NOON = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
# Who owns the files the tests make, as a snapshot records it: a Household's user here.
OWNER = os.getuid() if hasattr(os, "getuid") else 0


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
        # The PR `unreleased` came from: built on v1.0.0, which was main then, with the same code.
        # A rebase merge gave its commit another hash on main.
        git(self.path, "checkout", "-q", "-b", "pr", self.release)
        (self.path / "ops" / "gateway" / "gateway-setup.sh").write_text("# v1 and a fix\n")
        git(self.path, "commit", "-qam", "two, on the PR")
        self.pr_head = git(self.path, "rev-parse", "HEAD")
        git(self.path, "checkout", "-q", "main")

    def tree(self, commit: str) -> str:
        return git(self.path, "rev-parse", f"{commit}^{{tree}}")


class FakeEdge:
    """The server around `deploy`: gateway-setup.sh points `current` at the deploy it was given,
    each Household's evening job is a systemd unit, run-now runs it, and GitHub's check runs."""

    def __init__(self, server: Server) -> None:
        self.server = server
        self.ran: list[tuple[list[str], dict]] = []
        self.checks: dict[str, list[dict]] = {}
        self.pulls: dict[str, list[dict]] = {}  # by commit, the PRs it came from
        self.down: list[str] = []  # parts of the API URLs that fail
        self.statuses: dict[str, int] = {}
        self.marked = False  # admin households marks a Household
        self.setup_fails = False
        self.run_ends = "success"
        self.units: dict[str, dict[str, str]] = {}  # by user, the evening job's properties
        self.clock = 0.0
        self.asked: list[str] = []
        self.free = 100 << 30  # bytes free where the snapshots go
        self.answer = "yes"  # to rollback --restore's question
        self.questions: list[str] = []
        self.timers_on = True  # each Household's evening timers are active
        self.stop_fails = False  # systemctl can't stop one of the gateway's services
        self.users: dict[str, int] = {}  # the server's users and their uids

    def edge(self) -> admin.Edge:
        return admin.Edge(system=self.system, github=self.github, http_status=self.http_status,
                          now=lambda: self.now, monotonic=lambda: self.clock, sleep=self.sleep,
                          free=lambda path: self.free, ask=self.ask, uid=self.users.get)

    def ask(self, prompt: str) -> str:
        self.questions.append(prompt)
        return self.answer

    now = NOON

    def system(self, argv: list[str], capture: bool = False,
               env: dict | None = None) -> subprocess.CompletedProcess:
        self.ran.append((argv, env or {}))
        out, code = "", 0
        if argv[0] == "bash":
            code = 1 if self.setup_fails else 0
            if not self.setup_fails:
                current = self.server.paths.shared / "current"
                (self.server.paths.shared / env["PARENT_RECAP_DEPLOY"]).mkdir(parents=True,
                                                                             exist_ok=True)
                if current.is_symlink():
                    current.unlink()
                current.symlink_to(env["PARENT_RECAP_DEPLOY"])
        elif argv[0] == "systemctl" and argv[1] == "--user" and argv[4] == "show":
            user = argv[3].rstrip("@")
            unit = self.units.get(user, {"ActiveState": "inactive", "Result": "success",
                                         "ExecMainStatus": "0",
                                         "ExecMainStartTimestampMonotonic": "100"})
            if argv[-1].endswith(".timer"):
                unit = {"ActiveState": "active" if self.timers_on else "inactive"}
            elif argv[-1] != admin.EVENING_JOB:
                unit = {"ActiveState": "inactive"}
            out = "\n".join(f"{k}={v}" for k, v in unit.items())
        elif argv[:2] == ["systemctl", "stop"] and self.stop_fails:
            code = 1
        elif argv[-2:] == ["signup", "status"]:
            code = 1 if self.marked else 0
        elif argv[-2:] == ["schedule", "run-now"]:
            user = argv[2]
            # It starts, later than any run before, and has ended by the next look but one.
            began = int(self.units.get(user, {}).get("ExecMainStartTimestampMonotonic", 100))
            self.units[user] = {"ActiveState": "activating", "Result": "success",
                                "ExecMainStatus": "0",
                                "ExecMainStartTimestampMonotonic": str(began + 100)}
        return subprocess.CompletedProcess(argv, code, out, "")

    def sleep(self, seconds: float) -> None:
        self.clock += seconds
        for unit in self.units.values():
            if unit["ActiveState"] == "activating":
                unit.update(ActiveState="failed" if self.run_ends != "success" else "inactive",
                            Result=self.run_ends, ExecMainStatus="0" if self.run_ends ==
                            "success" else "1")

    def github(self, url: str) -> Any:
        """GitHub's API for the origin repo: check runs and the PRs a commit came from as set,
        commits and comparisons from the repo itself. A URL containing a part of `down` fails."""
        self.asked.append(url)
        if any(part in url for part in self.down):
            raise OSError("HTTP Error 500: Internal Server Error")
        path = url.removeprefix(self.server.paths.github_api).split("?")[0]
        origin = Path(self.server.paths.repo_url)
        if path.startswith("/git/commits/"):
            commit = path.split("/")[-1]
            return {"sha": commit, "tree": {"sha": git(origin, "rev-parse", f"{commit}^{{tree}}")}}
        if path.startswith("/compare/"):
            base, head = path.split("/")[-1].split("...")
            return {"ahead_by": int(git(origin, "rev-list", "--count", f"{base}..{head}")),
                    "behind_by": int(git(origin, "rev-list", "--count", f"{head}..{base}"))}
        commit = path.split("/commits/")[1].split("/")[0]
        if path.endswith("/pulls"):
            return self.pulls.get(commit, [])
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
    server.paths.autodeploy = tmp_path / "state" / "autodeploy.json"
    server.paths.sources = tmp_path / "sources"
    server.paths.deploy_env = tmp_path / "etc" / "deploy.env"
    server.paths.shared = tmp_path / "shared"
    server.paths.snapshots = tmp_path / "state" / "snapshots"
    # The services' state and config, of which a snapshot takes what is there.
    server.paths.state = (tmp_path / "var" / "bifrost", tmp_path / "var" / "signup",
                          tmp_path / "etc" / "brief-relay")
    (tmp_path / "var" / "bifrost").mkdir(parents=True)
    (tmp_path / "var" / "bifrost" / "config.db").write_text("keys as deployed\n")
    fake = FakeEdge(server)

    def run(*args: str) -> int:
        try:
            return admin.main(list(args), paths=server.paths, is_root=lambda: True,
                              exec_as=lambda argv, env: 0, edge=fake.edge(),
                              smtp=server.smtp)
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
    deploy.fake.users[label] = OWNER
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
                                "at": "2026-11-04T12:00:00+00:00",
                                "snapshot": f"20261104T120000Z-{short}.tar.gz"}]
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


# ── A commit whose code passed `test` on its PR (#818)


def from_pr(deploy, head: str | None = None, base: str = "main") -> str:
    """`unreleased` came from a PR whose head is `head`, the PR head by default."""
    head = head or deploy.origin.pr_head
    deploy.fake.pulls[deploy.origin.unreleased] = [
        {"number": 816, "base": {"ref": base}, "head": {"sha": head}}]
    return head


@pytest.mark.parametrize("own", [[], [{"name": "test", "conclusion": None}]])
def test_a_commit_whose_pr_head_had_the_same_code_and_passed_deploys_at_once(deploy, capsys,
                                                                             own):
    head = from_pr(deploy)
    deploy.fake.checks[deploy.origin.unreleased] = own
    deploy.fake.checks[head] = [{"name": "test", "conclusion": "success"}]
    assert deploy.origin.tree(head) == deploy.origin.tree(deploy.origin.unreleased)
    assert deploy("deploy", "main") == 0
    assert records(deploy)[0]["commit"] == deploy.origin.unreleased
    out = capsys.readouterr().out
    assert (f"✅ {deploy.origin.unreleased[:12]} has the same code as {head[:12]}, whose test "
            "check passed") in out
    assert "#816" in out


@pytest.mark.parametrize("ends", [[None], ["failure"], ["success", "cancelled"], []])
def test_a_same_code_pr_head_whose_test_hasn_t_passed_is_refused(deploy, capsys, ends):
    head = from_pr(deploy)
    deploy.fake.checks[head] = [{"name": "test", "conclusion": e} for e in ends]
    assert deploy("deploy", "main") == 1
    nothing_changed(deploy)
    err = capsys.readouterr().err
    assert "test check" in err and head[:12] in err


def test_a_pr_head_with_other_code_is_refused_until_the_commit_s_own_check_passed(deploy, capsys):
    head = from_pr(deploy, deploy.origin.release)
    deploy.fake.checks[head] = [{"name": "test", "conclusion": "success"}]
    assert deploy("deploy", "main") == 1
    nothing_changed(deploy)
    err = capsys.readouterr().err
    assert f"{deploy.origin.unreleased[:12]} has no test check on GitHub yet" in err
    assert "other code" in err


def test_a_commit_whose_own_check_failed_is_refused_though_its_pr_head_passed(deploy, capsys):
    head = from_pr(deploy)
    deploy.fake.checks[deploy.origin.unreleased] = [{"name": "test", "conclusion": "failure"}]
    deploy.fake.checks[head] = [{"name": "test", "conclusion": "success"}]
    assert deploy("deploy", "main") == 1
    nothing_changed(deploy)
    assert "failure" in capsys.readouterr().err
    # A failure on main is never overridden: the PR isn't even asked about.
    assert not any("/pulls" in url for url in deploy.fake.asked)


def test_a_pr_head_built_on_an_older_main_is_refused_though_its_code_is_the_same(deploy, capsys):
    # main moved on after the PR branched off, and the PR took the same change in its own
    # commit: its test ran on its head merged into an older main, which isn't proven to be this
    # code.
    origin = deploy.origin.path
    git(origin, "checkout", "-q", "main")
    (origin / "other").write_text("on main\n")
    git(origin, "add", "-A")
    git(origin, "commit", "-qm", "main moves on")
    (origin / "ops" / "gateway" / "gateway-setup.sh").write_text("# v1, the PR's fix\n")
    git(origin, "commit", "-qam", "the PR's fix, rebased")
    landed = git(origin, "rev-parse", "HEAD")
    git(origin, "checkout", "-q", "-b", "stale", deploy.origin.unreleased)
    (origin / "other").write_text("on main\n")
    (origin / "ops" / "gateway" / "gateway-setup.sh").write_text("# v1, the PR's fix\n")
    git(origin, "add", "-A")
    git(origin, "commit", "-qm", "the PR's fix with main's change in it")
    head = git(origin, "rev-parse", "HEAD")
    git(origin, "checkout", "-q", "main")
    assert deploy.origin.tree(head) == deploy.origin.tree(landed)
    deploy.fake.pulls[landed] = [{"number": 816, "base": {"ref": "main"}, "head": {"sha": head}}]
    deploy.fake.checks[head] = [{"name": "test", "conclusion": "success"}]
    assert deploy("deploy", "main") == 1
    nothing_changed(deploy)
    assert "older main" in capsys.readouterr().err


def test_a_pr_into_another_branch_isn_t_relied_on(deploy, capsys):
    head = from_pr(deploy, base="refactor")
    deploy.fake.checks[head] = [{"name": "test", "conclusion": "success"}]
    assert deploy("deploy", "main") == 1
    nothing_changed(deploy)
    assert "has no test check on GitHub yet" in capsys.readouterr().err


@pytest.mark.parametrize("down", ["/pulls", "/git/commits/", "/compare/", "PR_HEAD/check-runs"])
def test_github_failing_on_a_same_code_question_refuses_and_says_so(deploy, capsys, down):
    head = from_pr(deploy)
    deploy.fake.checks[head] = [{"name": "test", "conclusion": "success"}]
    deploy.fake.down = [down.replace("PR_HEAD", head)]
    assert deploy("deploy", "main") == 1
    nothing_changed(deploy)
    err = capsys.readouterr().err
    assert "couldn't ask GitHub" in err and "500" in err


def test_an_answer_github_shouldn_t_give_refuses_and_says_so(deploy, capsys):
    deploy.fake.pulls[deploy.origin.unreleased] = {"message": "Not Found"}  # type: ignore
    assert deploy("deploy", "main") == 1
    nothing_changed(deploy)
    assert "couldn't ask GitHub" in capsys.readouterr().err


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


def own_evening(deploy, label: str, schedule: dict | None, zone: str | None = None) -> None:
    """The Household's config, which its timer follows: its own time, moved by hand as the
    Runbook says, while households.json keeps the slot the sign-up handed out."""
    family = deploy.server.homes / label / ".family"
    family.mkdir(parents=True, exist_ok=True)
    config = {"hosted": True, "kids": [{"name": "Mia Virtanen"}],
              **({"schedule": schedule} if schedule is not None else {}),
              **({"timezone": zone} if zone else {})}
    (family / "config.yaml").write_text(json.dumps(config))  # JSON is YAML


def test_the_household_s_own_time_from_its_config_refuses_the_deploy(deploy, capsys):
    household(deploy, evening="21:00")
    own_evening(deploy, "ph-30d2afe0", {"daily_hour": 14, "daily_minute": 20})
    assert deploy("deploy", "v1.0.0") == 1
    nothing_changed(deploy)
    assert "ph-30d2afe0's evening run is due at 14:20" in capsys.readouterr().err


def test_households_json_s_slot_doesn_t_refuse_when_its_config_moved_away(deploy):
    household(deploy, evening="14:20")
    own_evening(deploy, "ph-30d2afe0", {"daily_hour": 16, "daily_minute": 0})
    assert deploy("deploy", "v1.0.0") == 0


def test_the_household_s_time_is_read_in_its_own_timezone(deploy, capsys):
    # 12:00 UTC is 13:00 in Stockholm, where its 13:15 run is due soon.
    household(deploy, evening="21:00")
    own_evening(deploy, "ph-30d2afe0", {"daily_hour": 13, "daily_minute": 15},
                "Europe/Stockholm")
    assert deploy("deploy", "v1.0.0") == 1
    assert "due at 13:15 Europe/Stockholm" in capsys.readouterr().err


@pytest.mark.parametrize("config", ["email: [unclosed", '{"schedule": {"daily_hour": "x"}}',
                                    '{"timezone": "Mars/Olympus"}', None])
def test_without_the_household_s_own_time_households_json_s_is_used(deploy, capsys, config):
    household(deploy, evening="14:20")
    if config is not None:
        family = deploy.server.homes / "ph-30d2afe0" / ".family"
        family.mkdir(parents=True)
        (family / "config.yaml").write_text(config)
    assert deploy("deploy", "v1.0.0") == 1
    nothing_changed(deploy)
    assert "ph-30d2afe0's evening run is due at 14:20" in capsys.readouterr().err


def test_without_the_shared_program_households_json_s_times_are_used(deploy, capsys):
    household(deploy, evening="14:20")
    own_evening(deploy, "ph-30d2afe0", {"daily_hour": 16, "daily_minute": 0})
    deploy.server.paths.python = str(deploy.server.root / "no-such-python")
    assert deploy("deploy", "v1.0.0") == 1
    err = capsys.readouterr().err
    assert "ph-30d2afe0's evening run is due at 14:20" in err


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


def test_a_failed_install_is_recorded_as_failed(deploy, capsys):
    deploy.fake.setup_fails = True
    assert deploy("deploy", "v1.0.0") == 1
    assert [d.get("failed") for d in records(deploy)] == [True]
    assert "recorded as failed" in capsys.readouterr().err


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
