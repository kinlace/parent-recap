"""Each deploy's snapshot, `admin deploys` and `admin rollback` (ops/gateway/admin, #744, ADR
0036): a deploy archives the server's state first, root's alone, keeps the last 3, and stops
before changing anything when it can't; `deploys` lists the deploys on disk, marking the current
one; `rollback` switches back to the deploy before the current one by the record, refusing when
there is none, and with --restore asks, stops the services and the Households' timers, and puts
back the snapshot taken before the current deploy. Files are real, under tmp_path, and the repo
is a real git repo; gateway-setup.sh, systemctl and HTTP are faked as in test_admin_deploy."""
from __future__ import annotations

import os
import subprocess
import sys
import tarfile
from datetime import timedelta
from pathlib import Path

import pytest
from test_admin import admin
from test_admin_deploy import (  # noqa: F401  (fixtures)
    _symlinks, deploy, git, household, nothing_changed, origin, records)


def later(deploy, hours: int = 1) -> None:
    deploy.fake.now = deploy.fake.now + timedelta(hours=hours)


def another_commit(deploy, message: str = "three") -> str:
    """A commit on main after the unreleased one, its test check passed."""
    path = deploy.origin.path
    (path / "ops" / "gateway" / "gateway-setup.sh").write_text(f"# {message}\n")
    git(path, "commit", "-qam", message)
    commit = git(path, "rev-parse", "HEAD")
    deploy.fake.checks[commit] = [{"name": "test", "conclusion": "success"}]
    return commit


def three_deploys(deploy) -> tuple[str, str, str]:
    """v1.0.0, then the unreleased commit, then one more, an hour apart."""
    deploy.fake.checks[deploy.origin.unreleased] = [{"name": "test", "conclusion": "success"}]
    assert deploy("deploy", "v1.0.0") == 0
    later(deploy)
    assert deploy("deploy", deploy.origin.unreleased) == 0
    later(deploy)
    third = another_commit(deploy)
    assert deploy("deploy", third) == 0
    later(deploy)
    return deploy.origin.release, deploy.origin.unreleased, third


def current(deploy) -> str:
    return os.readlink(deploy.server.paths.shared / "current")


def snapshots(deploy) -> list[str]:
    folder = deploy.server.paths.snapshots
    return sorted(p.name for p in folder.iterdir()) if folder.exists() else []


def home(deploy, label: str = "ph-30d2afe0") -> Path:
    path = deploy.server.homes / label / "ParentRecap"
    path.mkdir(parents=True, exist_ok=True)
    return path


# ── The snapshot


def test_a_deploy_first_snapshots_the_services_state_and_every_household_s_home(deploy):
    household(deploy)
    (home(deploy) / "2026-11-03.md").write_text("Brief\n")
    assert deploy("deploy", "v1.0.0") == 0
    [name] = snapshots(deploy)
    assert name == f"20261104T120000Z-{deploy.origin.release[:12]}.tar.gz"
    assert records(deploy)[0]["snapshot"] == name
    archive = deploy.server.paths.snapshots / name
    with tarfile.open(archive) as tar:
        names = tar.getnames()
    # Bifrost's state, the relay's config with the maintainers' address, and the Household's
    # home; a service with no state yet isn't there.
    assert "snapshot.json" in names
    assert "0/config.db" in names and "1/maintainers.env" in names
    assert "2/ParentRecap/2026-11-03.md" in names
    if sys.platform != "win32":
        assert archive.stat().st_mode & 0o777 == 0o600
        assert deploy.server.paths.snapshots.stat().st_mode & 0o777 == 0o700


def test_the_last_three_snapshots_are_kept(deploy):
    three_deploys(deploy)
    assert deploy("deploy", another_commit(deploy, "four")) == 0
    names = [d["snapshot"] for d in records(deploy)]
    assert snapshots(deploy) == names[1:]


def test_deploying_the_same_commit_again_keeps_the_snapshot_from_before_it_came(deploy):
    """--restore from it brings back the state before it first came, which the deploy before
    left."""
    deploy.fake.checks[deploy.origin.unreleased] = [{"name": "test", "conclusion": "success"}]
    assert deploy("deploy", "v1.0.0") == 0
    later(deploy)
    assert deploy("deploy", "main") == 0
    first = records(deploy)[-1]["snapshot"]
    for _ in range(3):
        later(deploy)
        assert deploy("deploy", "main") == 0
    assert first in snapshots(deploy) and len(snapshots(deploy)) == 4


def test_the_services_are_stopped_while_their_state_is_archived(deploy):
    assert deploy("deploy", "v1.0.0") == 0
    ran = [argv for argv, _ in deploy.fake.ran]
    stop = ran.index(["systemctl", "stop", *admin.SERVICES])
    start = ran.index(["systemctl", "start", *admin.SERVICES])
    setup = next(i for i, argv in enumerate(ran) if argv[0] == "bash")
    assert stop < start < setup


def test_pruning_keeps_the_snapshot_the_running_deploy_was_made_after(deploy, monkeypatch):
    assert deploy("deploy", "v1.0.0") == 0
    mine = records(deploy)[-1]["snapshot"]
    real_run = admin.subprocess.run

    def no_clone(argv, **kw):
        if argv[:2] == ["git", "clone"]:
            return subprocess.CompletedProcess(argv, 128, "", "fatal: disk full")
        return real_run(argv, **kw)
    monkeypatch.setattr(admin.subprocess, "run", no_clone)
    for _ in range(3):  # deploys refused after their snapshot
        later(deploy)
        assert deploy("deploy", "v1.0.0") == 1
    assert mine in snapshots(deploy) and len(snapshots(deploy)) == 4


def test_no_room_for_the_snapshot_stops_the_deploy_before_anything_changes(deploy, capsys):
    deploy.fake.free = 900 << 20
    assert deploy("deploy", "v1.0.0") == 1
    nothing_changed(deploy)
    assert snapshots(deploy) == []
    err = capsys.readouterr().err
    assert "nothing changed" in err and "there isn't room" in err


def test_a_failed_snapshot_stops_the_deploy_before_anything_changes(deploy, capsys, monkeypatch):
    real_add = tarfile.TarFile.add

    def add(self, name, *args, **kwargs):
        if Path(name).name == "config.db":
            raise PermissionError(13, "Permission denied", str(name))
        return real_add(self, name, *args, **kwargs)
    monkeypatch.setattr(tarfile.TarFile, "add", add)
    assert deploy("deploy", "v1.0.0") == 1
    nothing_changed(deploy)
    assert snapshots(deploy) == []  # nor a half-written one
    assert "the snapshot of the server's state failed" in capsys.readouterr().err


# ── admin deploys


def test_deploys_lists_the_deploys_on_disk_marking_the_current_one(deploy, capsys):
    first, second, third = three_deploys(deploy)
    capsys.readouterr()
    assert deploy("deploys") == 0
    out = capsys.readouterr().out.splitlines()
    assert out[1].startswith(f"* {third[:12]}  unreleased, deployed")
    assert out[2].startswith(f"  {second[:12]}  unreleased, deployed")
    assert out[2].endswith("<- sudo admin rollback switches to this")
    assert out[3].startswith(f"  {first[:12]}  v1.0.0, deployed 2026-11-04T12:00:00+00:00")
    assert f"rollback --restore brings back {records(deploy)[-1]['snapshot']}" in out[-1]


def test_deploys_with_none_on_disk_says_so(deploy, capsys):
    assert deploy("deploys") == 1
    assert "no deploy on disk" in capsys.readouterr().err


# ── Which deploy rollback picks


def test_rollback_switches_back_to_the_deploy_before_and_says_from_what_to_what(deploy, capsys):
    first, second, third = three_deploys(deploy)
    capsys.readouterr()
    assert deploy("rollback") == 0
    assert current(deploy) == second[:12]
    env = deploy.fake.setups()[-1]
    source = deploy.server.paths.sources / second[:12]
    assert env == {"GATEWAY_HOST": "gateway.example", "PARENT_RECAP_RELEASE": str(source),
                   "PARENT_RECAP_DEPLOY": second[:12], "PARENT_RECAP_SWITCH": "yes"}
    assert records(deploy)[-1] == {"commit": second, "tag": "unreleased",
                                   "at": "2026-11-04T15:00:00+00:00", "rolled_back_from": third}
    out = capsys.readouterr().out
    assert f"switched back from {third[:12]} (unreleased) to {second[:12]} (unreleased)" in out
    assert "switched back to 2026-11-04T15:00:00+00:00" in out
    # Nothing was restored: it wasn't asked for.
    assert deploy.fake.questions == []


def test_rollback_again_goes_further_back_until_there_is_nothing_before(deploy, capsys):
    first, second, third = three_deploys(deploy)
    assert deploy("rollback") == 0
    assert deploy("rollback") == 0
    assert current(deploy) == first[:12]
    capsys.readouterr()
    assert deploy("rollback") == 1
    assert current(deploy) == first[:12]
    assert "is the first deploy recorded: there is nothing to switch back to" in \
        capsys.readouterr().err


def test_after_a_rollback_and_a_new_deploy_rollback_goes_to_what_ran_before_it(deploy):
    first, second, third = three_deploys(deploy)
    assert deploy("rollback") == 0  # third -> second
    fourth = another_commit(deploy, "four")
    assert deploy("deploy", fourth) == 0
    assert deploy("rollback") == 0
    assert current(deploy) == second[:12]


def test_deploying_the_same_commit_again_is_no_deploy_to_switch_back_to(deploy):
    assert deploy("deploy", "v1.0.0") == 0
    later(deploy)
    assert deploy("deploy", "v1.0.0") == 0
    assert deploy("rollback") == 1


@pytest.mark.parametrize("current_moved", [False, True])
def test_after_a_failed_install_rollback_puts_the_deploy_before_back_whole(deploy, current_moved):
    deploy.fake.checks[deploy.origin.unreleased] = [{"name": "test", "conclusion": "success"}]
    assert deploy("deploy", "v1.0.0") == 0
    deploy.fake.setup_fails = True
    assert deploy("deploy", "main") == 1
    if current_moved:  # it failed after pointing `current` at the new program
        (deploy.server.paths.shared / "current").unlink()
        (deploy.server.paths.shared / "current").symlink_to(deploy.origin.unreleased[:12])
    deploy.fake.setup_fails = False
    assert deploy("rollback") == 0
    assert deploy.fake.setups()[-1]["PARENT_RECAP_DEPLOY"] == deploy.origin.release[:12]
    assert current(deploy) == deploy.origin.release[:12]
    assert records(deploy)[-1]["rolled_back_from"] == deploy.origin.unreleased


# ── Refusing when there is nothing to roll back to


def refused(deploy, capsys, *args: str) -> str:
    before = (records(deploy), deploy.fake.setups())
    assert deploy("rollback", *args) == 1
    assert (records(deploy), deploy.fake.setups()) == before
    err = capsys.readouterr().err
    assert "not switched back, nothing changed" in err
    return err


def test_rollback_with_no_deploy_is_refused(deploy, capsys):
    assert "isn't installed" in refused(deploy, capsys)


def test_rollback_from_a_deploy_made_by_hand_is_refused(deploy, capsys):
    deploy.fake.checks[deploy.origin.unreleased] = [{"name": "test", "conclusion": "success"}]
    assert deploy("deploy", "v1.0.0") == 0
    assert deploy("deploy", "main") == 0
    (deploy.server.paths.shared / "current").unlink()
    (deploy.server.paths.shared / "current").symlink_to("0.11.1")
    assert "the server runs 0.11.1, which isn't the last deploy recorded" in refused(deploy, capsys)


def test_rollback_to_a_deploy_no_longer_on_disk_is_refused(deploy, capsys):
    deploy.fake.checks[deploy.origin.unreleased] = [{"name": "test", "conclusion": "success"}]
    assert deploy("deploy", "v1.0.0") == 0
    assert deploy("deploy", "main") == 0
    admin._rmtree(deploy.server.paths.sources / deploy.origin.release[:12])
    err = refused(deploy, capsys)
    assert "is no longer on disk" in err and f"admin deploy {deploy.origin.release}" in err


def test_rollback_under_a_running_evening_run_is_refused(deploy, capsys):
    deploy.fake.checks[deploy.origin.unreleased] = [{"name": "test", "conclusion": "success"}]
    assert deploy("deploy", "v1.0.0") == 0
    assert deploy("deploy", "main") == 0
    household(deploy)
    deploy.fake.units["ph-30d2afe0"] = {"ActiveState": "active"}
    assert "ph-30d2afe0's evening run is still running" in refused(deploy, capsys)


def test_rollback_takes_no_other_argument(deploy):
    assert deploy("rollback", "v1.0.0") == 2


# ── rollback --restore


def test_restore_brings_back_the_state_from_before_the_current_deploy(deploy, capsys):
    household(deploy)
    brief = home(deploy) / "2026-11-03.md"
    brief.write_text("Brief as it was\n")
    deploy.fake.checks[deploy.origin.unreleased] = [{"name": "test", "conclusion": "success"}]
    assert deploy("deploy", "v1.0.0") == 0
    later(deploy)
    bifrost = deploy.server.root / "var" / "bifrost" / "config.db"
    bifrost.write_text("keys before the bad deploy\n")
    assert deploy("deploy", "main") == 0
    # The bad deploy changed the Household's data and Bifrost's.
    brief.write_text("rewritten\n")
    (home(deploy) / "new-format.db").write_text("x")
    bifrost.write_text("migrated\n")
    later(deploy)
    capsys.readouterr()
    deploy.fake.ran.clear()

    assert deploy("rollback", "--restore") == 0
    assert brief.read_text() == "Brief as it was\n"
    assert not (home(deploy) / "new-format.db").exists()
    assert bifrost.read_text() == "keys before the bad deploy\n"
    assert current(deploy) == deploy.origin.release[:12]
    assert len(deploy.fake.questions) == 1
    ran = [argv for argv, _ in deploy.fake.ran]
    stop = ran.index(["systemctl", "stop", *admin.SERVICES])
    timers = [i for i, argv in enumerate(ran) if argv[:4] == ["systemctl", "--user", "-M",
                                                               "ph-30d2afe0@"]]
    setup = next(i for i, argv in enumerate(ran) if argv[0] == "bash")
    assert ["systemctl", "--user", "-M", "ph-30d2afe0@", "stop",
            "parent-recap-daily.timer"] in ran
    assert ran[-1] != ["systemctl", "start", *admin.SERVICES]  # gateway-setup.sh restarted them
    assert ["systemctl", "--user", "-M", "ph-30d2afe0@", "start",
            "parent-recap-weekend-events.timer"] in ran
    assert stop < setup < max(timers)
    assert "restored " in capsys.readouterr().out
    # No leftover beside what was put back.
    assert sorted(p.name for p in deploy.server.homes.iterdir()) == ["ph-30d2afe0"]


def test_restore_answered_no_changes_nothing(deploy, capsys):
    deploy.fake.checks[deploy.origin.unreleased] = [{"name": "test", "conclusion": "success"}]
    assert deploy("deploy", "v1.0.0") == 0
    assert deploy("deploy", "main") == 0
    bifrost = deploy.server.root / "var" / "bifrost" / "config.db"
    bifrost.write_text("migrated\n")
    deploy.fake.answer = "n"
    deploy.fake.ran.clear()
    assert "nothing changed" in refused(deploy, capsys, "--restore")
    assert bifrost.read_text() == "migrated\n"
    assert deploy.fake.ran == []


def test_restore_without_its_snapshot_is_refused_before_asking(deploy, capsys):
    deploy.fake.checks[deploy.origin.unreleased] = [{"name": "test", "conclusion": "success"}]
    assert deploy("deploy", "v1.0.0") == 0
    later(deploy)
    assert deploy("deploy", "main") == 0
    (deploy.server.paths.snapshots / records(deploy)[-1]["snapshot"]).unlink()
    err = refused(deploy, capsys, "--restore")
    assert "isn't on disk any more" in err
    assert deploy.fake.questions == []


def test_a_failed_switch_after_a_restore_starts_the_services_again(deploy, capsys):
    household(deploy)
    deploy.fake.checks[deploy.origin.unreleased] = [{"name": "test", "conclusion": "success"}]
    assert deploy("deploy", "v1.0.0") == 0
    later(deploy)
    assert deploy("deploy", "main") == 0
    deploy.fake.setup_fails = True
    deploy.fake.ran.clear()
    assert deploy("rollback", "--restore") == 1
    ran = [argv for argv, _ in deploy.fake.ran]
    assert ["systemctl", "start", *admin.SERVICES] in ran
    assert ["systemctl", "--user", "-M", "ph-30d2afe0@", "start",
            "parent-recap-daily.timer"] in ran
    assert records(deploy)[-1]["commit"] == deploy.origin.unreleased
    assert "gateway-setup.sh of" in capsys.readouterr().err


def test_restore_when_a_service_won_t_stop_restores_nothing_and_starts_all_again(deploy, capsys):
    household(deploy)
    deploy.fake.checks[deploy.origin.unreleased] = [{"name": "test", "conclusion": "success"}]
    assert deploy("deploy", "v1.0.0") == 0
    later(deploy)
    assert deploy("deploy", "main") == 0
    bifrost = deploy.server.root / "var" / "bifrost" / "config.db"
    bifrost.write_text("migrated\n")
    deploy.fake.stop_fails = True
    deploy.fake.ran.clear()
    assert deploy("rollback", "--restore") == 1
    assert bifrost.read_text() == "migrated\n"
    assert current(deploy) == deploy.origin.unreleased[:12]
    ran = [argv for argv, _ in deploy.fake.ran]
    assert ["systemctl", "start", *admin.SERVICES] in ran
    assert ["systemctl", "--user", "-M", "ph-30d2afe0@", "start",
            "parent-recap-daily.timer"] in ran
    assert "nothing was restored" in capsys.readouterr().err


def test_a_folder_an_interrupted_restore_set_aside_is_never_lost(deploy, monkeypatch):
    assert deploy("deploy", "v1.0.0") == 0
    archive = deploy.server.paths.snapshots / records(deploy)[-1]["snapshot"]
    bifrost = deploy.server.root / "var" / "bifrost"
    aside = bifrost.with_name(".bifrost.before-restore")
    bifrost.rename(aside)  # where a restore stopped by a power cut left it

    def fails(*args, **kwargs):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(tarfile.TarFile, "extractall", fails)
    with pytest.raises(OSError):
        admin._restore(archive)
    assert (bifrost / "config.db").read_text() == "keys as deployed\n"


def test_rollback_and_deploys_are_listed_in_help(deploy, capsys):
    assert deploy("--help") == 0
    out = capsys.readouterr().out
    assert "  deploys " in out and "  rollback [--restore]" in out
