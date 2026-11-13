"""The evening run updates Parent Recap by itself (ADR 0047, #915): once the scheduler's evening run
has sent the Brief, or decided to send nothing, it installs a newer release this Mac has seen on
`stable` for a day, as `parent-recap update` does, in the background and under the run's lock.

Driven through main() (`parent-recap run` as the evening job starts it) with the harness's clock
(time_machine) and test_update.py's fake GitHub and installed program. The release's install.sh
is that file's fake; here it also runs, once it has switched, what the real one runs then: the
Linked Device's restart and `schedule_update`, the step that would move the wake."""
from __future__ import annotations

import contextlib
import io
import json
import os
from datetime import timedelta

import pytest
import requests
import time_machine

from conftest import NOW
from fake_system import WINDOWS
from family_brief import schedule_update, update, versions
from family_brief.system import jobs
from nights import normal_night
from test_update import INSTALLED, github, mac, update_log  # noqa: F401  the fixtures

# The Mac's wake dialog and install.sh's layout.
pytestmark = pytest.mark.macos

DAY = timedelta(days=1)


def at(when):
    return time_machine.travel(when, tick=False)


@pytest.fixture
def evening(harness, mac, monkeypatch):  # noqa: F811
    """A Household scheduled on a Mac whose wake an older version set 5 minutes before the Brief,
    with the Linked Device running and paired, and the evening job starting the run."""
    monkeypatch.setenv(jobs.SCHEDULED, "1")
    harness.jobs.add(jobs.DAILY)
    harness.jobs.install_always(["node", str(mac.folder / "app" / "linked-device.mjs"), "follow"],
                                harness.home / "logs")
    harness.wake.wakes = [harness.wake.old_line(21, 0)]
    pairing = mac.folder / "linked-device" / "auth" / "creds.json"
    pairing.parent.mkdir(parents=True)
    pairing.write_text('{"me": "paired"}')

    def install_sh_after_switch(env: dict[str, str]) -> str:
        """install.sh's steps once it has switched: `linked_device restart` restarts a running
        Linked Device (its own tests cover how), then `schedule_update` with install.sh's env."""
        jobs.restart_always()
        out = io.StringIO()
        with pytest.MonkeyPatch.context() as mp, contextlib.redirect_stdout(out):
            for name in (schedule_update.UPDATING, "PARENT_RECAP_HOME"):
                mp.setenv(name, env[name])
            schedule_update.update(None)
        return out.getvalue()
    mac.after_switch = install_sh_after_switch
    last_run(harness, 24)
    return harness


def last_run(h, hours_before_now: float) -> None:
    when = (NOW - timedelta(hours=hours_before_now)).isoformat()
    h.state_path.parent.mkdir(parents=True, exist_ok=True)
    h.state_path.write_text(json.dumps({"last_run_at": when, "caught_up_at": when}))


def exit_recorded(h) -> int:
    return json.loads(h.state_path.with_name("last-run.json").read_text())["exit"]


def downloads(github, version: str) -> list[str]:  # noqa: F811
    return [r["url"] for r in github.requests
            if r["host"] == "github.com" and r["url"].endswith(f"parent-recap-{version}.zip")]


def release_out(github, version: str) -> None:  # noqa: F811
    github.stable = version
    github.publish(version)


# ---------------------------------------------------------------- a release a day old

def test_a_release_seen_on_one_evening_is_installed_the_next_and_runs_the_evening_after(
        evening, github, mac):  # noqa: F811
    release_out(github, "0.20.0")
    normal_night(evening, "en")
    plists = dict(evening.jobs.programs), list(evening.jobs.always)

    assert evening.run() == 0  # tonight: the release is new to this Mac, so it's left alone

    assert len(evening.sent) == 1
    assert mac.version() == INSTALLED and mac.installs == []
    assert update.state(mac.folder)["seen"]["version"] == "0.20.0"

    # The next evening's run starts a moment sooner after the Brief's time than tonight's look.
    with at(NOW + DAY - timedelta(seconds=30)):
        assert evening.run() == 0
    assert mac.version() == "0.20.0"
    assert versions.version(versions.previous(mac.folder)) == INSTALLED
    # The jobs are as they were: the evening job starts `app`, which now points at 0.20.0.
    assert (dict(evening.jobs.programs), list(evening.jobs.always)) == plists
    assert evening.jobs.installed() == {jobs.DAILY}


def test_a_release_this_mac_first_saw_less_than_a_day_ago_is_left_for_a_later_evening(
        evening, github, mac):  # noqa: F811
    release_out(github, "0.20.0")
    normal_night(evening, "en")
    last_run(evening, 48)  # last evening's run was missed
    with at(NOW - timedelta(hours=15)):  # this morning's catch-up of it sends its Brief
        assert evening.run() == 0
    assert len(evening.sent) == 1 and update.state(mac.folder)["seen"]["version"] == "0.20.0"
    first = update.state(mac.folder)["seen"]["at"]

    assert evening.run() == 0  # tonight's evening run, 15 hours later

    assert mac.installs == [] and update.state(mac.folder)["seen"]["at"] == first
    with at(NOW + DAY):
        assert evening.run() == 0
    assert mac.version() == "0.20.0"


def test_a_newer_release_on_stable_while_one_waits_starts_its_own_day(evening, github, mac):  # noqa: F811
    release_out(github, "0.20.0")
    assert evening.run() == 0
    release_out(github, "0.20.1")

    with at(NOW + DAY):
        assert evening.run() == 0
    assert mac.installs == []  # 0.20.1 has been out a moment

    with at(NOW + 2 * DAY):
        assert evening.run() == 0
    assert mac.version() == "0.20.1"


# ---------------------------------------------------------------- after the Brief, never instead of it

def test_the_update_starts_only_once_the_brief_is_sent(evening, github, mac, monkeypatch):  # noqa: F811
    release_out(github, "0.20.0")
    normal_night(evening, "en")
    sent_at_first_request = []
    request = requests.Session.request

    def noting(session, method, url, **k):
        if not sent_at_first_request:
            sent_at_first_request.append(len(evening.sent))
        return request(session, method, url, **k)
    monkeypatch.setattr(requests.Session, "request", noting)

    assert evening.run() == 0

    assert sent_at_first_request == [1]


def test_a_quiet_evening_updates_too(evening, github, mac):  # noqa: F811
    release_out(github, "0.20.0")
    assert evening.run() == 0
    with at(NOW + DAY):
        assert evening.run() == 0

    assert evening.sent == []
    assert mac.version() == "0.20.0"


@pytest.mark.parametrize("failure", ["installer", "check", "crash"])
def test_an_update_that_fails_leaves_the_run_s_brief_and_exit_as_they_were(
        evening, github, mac, monkeypatch, failure):  # noqa: F811
    release_out(github, "0.20.0")
    assert evening.run() == 0
    normal_night(evening, "en")
    if failure == "installer":
        mac.installer_fails = True
    elif failure == "check":
        mac.fails["0.20.0"] = ["Calendar"]
    else:
        def crash(*_a, **_k):
            raise RuntimeError("disk full")
        monkeypatch.setattr(update, "_update_to", crash)

    with at(NOW + DAY):
        assert evening.run() == 0

    assert len(evening.sent) == 1
    assert exit_recorded(evening) == 0
    assert mac.version() == INSTALLED


def test_with_the_network_down_the_run_ends_as_usual_and_the_next_evening_looks_again(
        evening, github, mac, monkeypatch):  # noqa: F811
    release_out(github, "0.20.0")
    normal_night(evening, "en")
    request = requests.Session.request

    def down(*_a, **_k):
        raise requests.ConnectionError("network is unreachable")
    monkeypatch.setattr(requests.Session, "request", down)

    assert evening.run() == 0

    assert len(evening.sent) == 1 and exit_recorded(evening) == 0
    assert "couldn't be reached" in update.state(mac.folder)["last_result"]
    monkeypatch.setattr(requests.Session, "request", request)
    with at(NOW + DAY):
        assert evening.run() == 0
    assert github.requests and update.state(mac.folder)["seen"]["version"] == "0.20.0"


# ---------------------------------------------------------------- with updates turned off (#917)

def test_with_updates_off_an_evening_run_reads_only_the_version_and_downloads_nothing(
        evening, github, mac):  # noqa: F811
    evening.config["updates"] = {"enabled": False}
    release_out(github, "0.20.0")
    normal_night(evening, "en")

    assert evening.run() == 0
    for day in (1, 2):  # a day old and more: with updates on, it would be installed by now
        with at(NOW + day * DAY):
            assert evening.run() == 0

    assert len(evening.sent) == 3  # the Brief went out as on any evening
    assert mac.version() == INSTALLED and mac.installs == []
    # One request an evening, for the version `stable` holds: no zip, no signature.
    assert [r["url"] for r in github.requests] == [update.STABLE_VERSION] * 3
    assert not list((mac.folder / update.UPDATE).glob(f"{update.DOWNLOAD}*"))
    state = update.state(mac.folder)
    assert state["last_look"] is not None and "0.20.0 is out" in state["last_result"]
    assert "updates are off, so nothing was downloaded" in update_log(mac)


def test_with_updates_off_and_nothing_newer_out_the_look_says_up_to_date(evening, github, mac):  # noqa: F811
    evening.config["updates"] = {"enabled": False}

    assert evening.run() == 0

    assert [r["url"] for r in github.requests] == [update.STABLE_VERSION]
    assert update.state(mac.folder)["last_result"] == f"up to date at {INSTALLED}"


def test_updates_turned_on_again_install_the_release_this_mac_has_seen_a_day(evening, github, mac):  # noqa: F811
    evening.config["updates"] = {"enabled": False}
    release_out(github, "0.20.0")
    assert evening.run() == 0
    evening.config["updates"] = {"enabled": True}  # as the switch and `update on` save it

    with at(NOW + DAY):
        assert evening.run() == 0

    assert mac.version() == "0.20.0"


# ---------------------------------------------------------------- runs that don't update

def no_look(github, mac) -> bool:  # noqa: F811
    return github.requests == [] and mac.installs == [] and update.state(mac.folder)["last_look"] is None


def test_a_run_by_hand_does_not_update(harness, github, mac, monkeypatch):  # noqa: F811
    release_out(github, "0.20.0")
    normal_night(harness, "en")

    assert harness.run() == 0

    assert len(harness.sent) == 1 and no_look(github, mac)


def test_the_evening_job_started_by_hand_does_not_update(evening, github, mac):  # noqa: F811
    release_out(github, "0.20.0")
    assert evening.cli("schedule", "run-now") == 0

    assert evening.run() == 0

    assert no_look(github, mac)


def test_a_dry_run_does_not_update(evening, github, mac):  # noqa: F811
    release_out(github, "0.20.0")
    normal_night(evening, "en")

    assert evening.run("--dry-run") == 0

    assert no_look(github, mac)


def test_weekend_picks_do_not_update(evening, github, mac):  # noqa: F811
    release_out(github, "0.20.0")

    evening.cli("weekend-events")

    assert no_look(github, mac)


def test_a_catch_up_that_sends_nothing_does_not_update(evening, github, mac):  # noqa: F811
    release_out(github, "0.20.0")
    last_run(evening, 0.5)  # the evening ran: a late start finds nothing to catch up
    with at(NOW + timedelta(hours=3)):
        assert evening.run() == 0
    last_run(evening, 34)  # missed by more than 10 hours: left to the next evening
    with at(NOW + timedelta(hours=10)):
        assert evening.run() == 0
    last_run(evening, 27)  # caught up, on a night with nothing to send
    with at(NOW + timedelta(hours=3)):
        assert evening.run() == 0

    assert evening.sent == [] and no_look(github, mac)


def test_a_catch_up_that_sends_the_brief_updates(evening, github, mac):  # noqa: F811
    release_out(github, "0.20.0")
    normal_night(evening, "en")
    last_run(evening, 27)

    with at(NOW + timedelta(hours=3)):
        assert evening.run() == 0

    assert len(evening.sent) == 1 and update.state(mac.folder)["seen"]["version"] == "0.20.0"


def test_a_hosted_household_does_not_update(evening, github, mac):  # noqa: F811
    release_out(github, "0.20.0")
    evening.config["hosted"] = True
    normal_night(evening, "en")

    assert evening.run() == 0

    assert len(evening.sent) == 1 and no_look(github, mac)


@pytest.mark.parametrize("updates", [None, {"enabled": False}])
def test_windows_does_not_update(evening, github, mac, updates):  # noqa: F811
    # No host is asked for a version either, whatever a config brought from a Mac says (#917).
    evening.system = WINDOWS
    if updates is not None:
        evening.config["updates"] = updates
    release_out(github, "0.20.0")

    with at(NOW + DAY):
        evening.run()

    assert no_look(github, mac)


def test_a_program_not_run_from_an_installed_release_does_not_update(harness, github, monkeypatch):  # noqa: F811
    monkeypatch.setenv(jobs.SCHEDULED, "1")
    last_run(harness, 24)
    release_out(github, "0.20.0")
    normal_night(harness, "en")

    assert harness.run() == 0

    assert len(harness.sent) == 1 and github.requests == []
    assert not (harness.home / "ParentRecap" / update.UPDATE).exists()


# ---------------------------------------------------------------- in the background

def test_the_background_install_opens_no_dialog_and_the_update_log_says_what_it_skipped(
        evening, github, mac):  # noqa: F811
    release_out(github, "0.20.0")
    old_wake = list(evening.wake.wakes)
    assert evening.run() == 0
    with at(NOW + DAY):
        assert evening.run() == 0

    assert mac.version() == "0.20.0"
    assert evening.wake.asked == [] and evening.wake.wakes == old_wake
    assert evening.dialog.shown == []
    assert mac.install_envs[0][schedule_update.UPDATING] == "1"
    log = update_log(mac)
    assert "wasn't moved to 21:00" in log and "asks for your Mac password" in log
    assert "left as it is" in log  # the evening job, which the update runs inside


def test_the_linked_device_runs_the_new_version_and_after_a_switch_back_the_old_with_its_pairing(
        evening, github, mac):  # noqa: F811
    pairing = mac.folder / "linked-device" / "auth" / "creds.json"
    always = list(evening.jobs.always)
    release_out(github, "0.20.0")
    assert evening.run() == 0
    with at(NOW + DAY):
        assert evening.run() == 0

    # Restarted once switched, naming `app`, which points at the new version.
    assert mac.version() == "0.20.0" and evening.jobs.always_restarts == 1
    assert evening.jobs.always == always and evening.jobs.always_running
    assert pairing.read_text() == '{"me": "paired"}'

    release_out(github, "0.21.0")
    mac.fails["0.21.0"] = ["WhatsApp"]
    with at(NOW + 2 * DAY):
        assert evening.run() == 0
    with at(NOW + 3 * DAY):
        assert evening.run() == 0

    assert mac.version() == "0.20.0"  # switched back, and the Linked Device restarted on it
    assert evening.jobs.always_restarts == 3
    assert evening.jobs.always == always and pairing.read_text() == '{"me": "paired"}'


def test_a_version_that_failed_is_not_downloaded_again_and_a_newer_one_is_tried(
        evening, github, mac):  # noqa: F811
    release_out(github, "0.20.0")
    mac.fails["0.20.0"] = ["Calendar"]
    assert evening.run() == 0
    with at(NOW + DAY):
        assert evening.run() == 0
    assert mac.version() == INSTALLED and len(downloads(github, "0.20.0")) == 1

    with at(NOW + 2 * DAY):
        assert evening.run() == 0
    assert len(downloads(github, "0.20.0")) == 1 and len(mac.installs) == 1

    release_out(github, "0.20.1")
    with at(NOW + 3 * DAY):
        assert evening.run() == 0
    with at(NOW + 4 * DAY):
        assert evening.run() == 0
    assert mac.version() == "0.20.1"


def test_no_request_of_the_evening_update_goes_to_the_maintainers_gateway(evening, github, mac):  # noqa: F811
    release_out(github, "0.20.0")
    assert evening.run() == 0
    with at(NOW + DAY):  # a quiet evening: whatever reaches the gateway is the update's
        assert evening.run() == 0

    assert mac.version() == "0.20.0"
    assert github.hosts() <= set(update.HOSTS)
    outbox = evening.outbox
    assert (outbox.relay_keys, outbox.key_requests, outbox.binds) == ([], [], [])
    assert evening.model_calls == []
    assert os.environ.get(schedule_update.UPDATING) is None  # set only for the installer
