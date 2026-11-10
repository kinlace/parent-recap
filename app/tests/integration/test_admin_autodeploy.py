"""`admin autodeploy` (ops/gateway/admin, #872, ADR 0045): what parent-recap-autodeploy.timer runs
each morning. When `stable` points at a release tag the server doesn't run, it deploys it as
`admin deploy` does and mails the maintainers how it went: on a failed check it switches back
first; a release whose changelog says switching back needs the snapshot waits for a deploy by
hand; a refused deploy is tried again the next day and mailed only on the third day running;
paused, it does nothing. The repo is a real git repo; gateway-setup.sh, systemctl, HTTP and
Email Delivery's SMTP are faked as in test_admin_deploy and test_admin."""
from __future__ import annotations

import json
from datetime import timedelta

import pytest
from test_admin_deploy import (  # noqa: F401  (fixtures)
    _symlinks, deploy, git, household, origin, records)

NEEDS_SNAPSHOT = "<!-- switching back needs the snapshot -->"


def release(deploy, version: str, notes: str = "- A fix families notice.",
            stable: bool = True) -> str:
    """A release on main: its changelog section and a change, tagged v`version`; `stable` moves
    to it, as release.yml moves it. Returns its commit."""
    path = deploy.origin.path
    changelog = path / "CHANGELOG.md"
    before = changelog.read_text() if changelog.exists() else "# Changelog\n"
    head, _, rest = before.partition("\n## ")
    section = f"## {version} · 2026-11-09\n\n### Fixed\n\n{notes}\n"
    changelog.write_text(f"{head.rstrip()}\n\n{section}" + (f"\n## {rest}" if rest else ""))
    (path / "ops" / "gateway" / "gateway-setup.sh").write_text(f"# {version}\n")
    git(path, "add", "-A")
    git(path, "commit", "-qm", f"release: {version}")
    git(path, "tag", f"v{version}")
    if stable:
        git(path, "branch", "-f", "stable", f"v{version}")
    return git(path, "rev-parse", "HEAD")


@pytest.fixture
def server(deploy):
    """A server a maintainer deployed v1.0.0 to by hand, with the check Household, `stable` on
    v1.0.0; the morning of the next day, 10:00 in Helsinki."""
    household(deploy)
    git(deploy.origin.path, "branch", "stable", "v1.0.0")
    assert deploy("deploy", "v1.0.0") == 0
    deploy.fake.now = deploy.fake.now.replace(day=5, hour=8)
    deploy.fake.ran.clear()
    return deploy


def mails(deploy) -> list[tuple[str, str]]:
    """Each mail's subject and text."""
    return [(msg["Subject"], msg.get_content()) for msg, _ in deploy.server.smtp.sent]


def runs(deploy) -> str:
    return deploy.server.paths.shared.joinpath("current").readlink().name


def test_a_new_release_on_stable_is_deployed_checked_and_mailed(server, capsys):
    commit = release(server, "1.1.0", "- The Brief comes on time again.")
    assert server("autodeploy", "run") == 0
    assert runs(server) == commit[:12]
    assert [d["tag"] for d in records(server)] == ["v1.0.0", "v1.1.0"]
    [(subject, text)] = mails(server)
    assert subject == "Parent Recap server: v1.1.0 deployed"
    assert f"now runs v1.1.0 ({commit[:12]})" in text
    assert f"before: v1.0.0 ({server.origin.release[:12]})" in text
    assert "ph-30d2afe0's evening run ended ok" in text
    assert "- The Brief comes on time again." in text
    # The changelog's section of this release only.
    assert "## 1.1.0" in text and "## 1.0.0" not in text


def test_the_release_the_server_runs_does_nothing(server):
    assert server("autodeploy", "run") == 0
    assert server.fake.setups() == [] and mails(server) == []
    assert len(records(server)) == 1


def test_a_release_older_than_what_runs_does_nothing(server):
    release(server, "1.1.0", stable=False)
    assert server("deploy", "v1.1.0") == 0
    server.fake.ran.clear()
    assert server("autodeploy", "run") == 0  # stable is still on v1.0.0
    assert server.fake.setups() == [] and mails(server) == []


def test_a_failed_check_switches_back_and_mails_what_failed(server):
    release(server, "1.1.0")
    server.fake.run_ends = "exit-code"
    assert server("autodeploy", "run") == 1
    assert runs(server) == server.origin.release[:12]
    assert records(server)[-1]["rolled_back_from"] == records(server)[1]["commit"]
    [(subject, text)] = mails(server)
    assert subject == "Parent Recap server: v1.1.0 failed a check and was switched back"
    assert "ph-30d2afe0's evening run failed (exit-code, exit 1)" in text
    assert "switched back to v1.0.0" in text and "its own checks passed" in text


def test_a_failed_install_switches_back_and_mails(server):
    release(server, "1.1.0")
    system = server.fake.system

    def first_setup_fails(argv, capture=False, env=None):
        server.fake.setup_fails = argv[0] == "bash" and not server.fake.setups()
        return system(argv, capture, env)
    server.fake.system = first_setup_fails
    assert server("autodeploy", "run") == 1
    [(subject, text)] = mails(server)
    assert "switched back" in subject
    assert "gateway-setup.sh failed partway" in text
    assert runs(server) == server.origin.release[:12]


def test_a_release_switched_back_from_isn_t_deployed_again(server):
    release(server, "1.1.0")
    server.fake.run_ends = "exit-code"
    server("autodeploy", "run")
    server.fake.run_ends = "success"
    server.fake.now += timedelta(days=1)
    server.fake.ran.clear()
    assert server("autodeploy", "run") == 0
    assert server.fake.setups() == [] and len(mails(server)) == 1


def test_a_failed_switch_back_is_said_in_the_mail(server):
    release(server, "1.1.0")
    server.fake.statuses["https://gateway.example/"] = 200  # every check after it fails too
    assert server("autodeploy", "run") == 1
    [(_, text)] = mails(server)
    assert "outside its routes answered 200" in text
    assert "its own checks failed" in text


def test_a_release_needing_the_snapshot_waits_for_a_deploy_by_hand(server):
    release(server, "1.1.0", f"- A change.\n\n{NEEDS_SNAPSHOT}")
    assert server("autodeploy", "run") == 0
    assert server.fake.setups() == [] and len(records(server)) == 1
    [(subject, text)] = mails(server)
    assert subject == "Parent Recap server: v1.1.0 waits for a deploy by hand"
    assert "sudo admin deploy v1.1.0" in text
    # Mailed once, not each morning.
    server.fake.now += timedelta(days=1)
    assert server("autodeploy", "run") == 0
    assert len(mails(server)) == 1 and server.fake.setups() == []


def test_paused_it_checks_nothing_and_mails_nothing(server, capsys):
    release(server, "1.1.0")
    assert server("autodeploy", "pause") == 0
    server.server.paths.repo_url = str(server.origin.path / "gone")  # not even fetched
    assert server("autodeploy", "run") == 0
    assert server.fake.setups() == [] and mails(server) == []
    assert "last" not in json.loads(server.server.paths.autodeploy.read_text())
    server.server.paths.repo_url = str(server.origin.path)
    assert server("autodeploy", "resume") == 0
    assert server("autodeploy", "run") == 0
    assert [d["tag"] for d in records(server)][-1] == "v1.1.0"


def test_a_refused_deploy_is_tried_again_and_mailed_on_the_third_day(server):
    release(server, "1.1.0")
    server.fake.marked = True  # admin households marks a Household
    for day in range(2):
        assert server("autodeploy", "run") == 0
        assert mails(server) == [] and len(records(server)) == 1
        server.fake.now += timedelta(days=1)
    assert server("autodeploy", "run") == 1
    [(subject, text)] = mails(server)
    assert subject == "Parent Recap server: v1.1.0 not deployed for 3 days"
    assert "admin households marks a Household" in text
    server.fake.now += timedelta(days=1)
    server.fake.marked = False
    assert server("autodeploy", "run") == 0
    assert [d["tag"] for d in records(server)][-1] == "v1.1.0"
    assert mails(server)[-1][0] == "Parent Recap server: v1.1.0 deployed"


def test_status_says_paused_and_the_last_attempt(server, capsys):
    release(server, "1.1.0")
    server("autodeploy", "run")
    capsys.readouterr()
    assert server("autodeploy", "status") == 0
    out = capsys.readouterr().out
    assert "automatic deploys are on" in out
    assert "2026-11-05T08:00:00+00:00" in out and "v1.1.0 deployed" in out
    server("autodeploy", "pause")
    capsys.readouterr()
    server("autodeploy", "status")
    assert "automatic deploys are paused since 2026-11-05T08:00:00+00:00" \
        in capsys.readouterr().out


def test_households_says_when_automatic_deploys_are_paused(server, capsys):
    server("households")
    assert "paused" not in capsys.readouterr().out
    server("autodeploy", "pause")
    server("households")
    assert "automatic deploys are paused" in capsys.readouterr().out


def test_the_mails_carry_no_address(server):
    release(server, "1.1.0")
    server("autodeploy", "run")
    [(_, text)] = mails(server)
    assert "@" not in text


@pytest.mark.parametrize("args", [[], ["now"], ["status", "x"]])
def test_autodeploy_takes_one_of_its_verbs(server, args):
    assert server("autodeploy", *args) == 2


def test_autodeploy_is_listed_in_help(server, capsys):
    server("--help")
    assert "  autodeploy pause|resume|status" in capsys.readouterr().out


def test_state_is_kept_in_its_own_file(server):
    server("autodeploy", "pause")
    state = json.loads(server.server.paths.autodeploy.read_text())
    assert state["paused"] is True
