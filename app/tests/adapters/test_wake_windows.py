"""The Windows adapter of `family_brief.system.wake`: the evening job's task wakes the PC, once the
power plan's "Allow wake timers" lets it (ADR 0021). Callers' tests use fake_system.FakeWake.

The outside edge is faked on any system: `powercfg /query`, which prints the power plan's
settings in the PC's own language. On Windows the real one is read too."""
from __future__ import annotations

import subprocess
from typing import Any

import pytest

from family_brief.system import wake, wake_windows

ENABLE, DISABLE = 1, 0


def query(ac: int, dc: int, numeric: bool = False) -> str:
    """`powercfg /query` for one setting, as a Finnish Windows words it."""
    possible = ("      Pienin mahdollinen asetus: 0x00000000\n"
                "      Suurin mahdollinen asetus: 0xffffffff\n"
                "      Mahdollisten asetusten lisäys: 0x00000001\n" if numeric else
                "      Mahdollinen asetusindeksi: 000\n      Mahdollinen asetusindeksi: 001\n")
    return ("Virrankäyttömallin GUID: 381b4222-f694-41f0-9685-ff5bb260df2e  (Tasapainotettu)\n"
            "  Aliryhmän GUID: 238c9fa8-0aad-41ed-83f4-97be242c8f20  (Lepotila)\n"
            f"{possible}"
            f"    Nykyinen verkkovirta-asetusindeksi: 0x{ac:08x}\n"
            f"    Nykyinen akkuvirta-asetusindeksi: 0x{dc:08x}\n")


@pytest.fixture
def powercfg(monkeypatch) -> dict[str, tuple[int, int]]:
    """The power plan's Sleep settings, plugged in and on battery: a PC that sleeps after 30
    minutes and hibernates after 3 hours, with wake timers off."""
    settings = {"STANDBYIDLE": (1800, 900), "HIBERNATEIDLE": (10800, 10800),
                "RTCWAKE": (DISABLE, DISABLE)}

    def run(cmd: list[str], *_a: Any, **_k: Any) -> subprocess.CompletedProcess:
        assert cmd[:4] == ["powercfg", "/query", "SCHEME_CURRENT", "SUB_SLEEP"], cmd
        ac, dc = settings[cmd[4]]
        return subprocess.CompletedProcess(cmd, 0, query(ac, dc, cmd[4] != "RTCWAKE"), "")
    monkeypatch.setattr(wake, "_adapter", wake_windows)
    monkeypatch.setattr(subprocess, "run", run)
    return settings


def test_with_wake_timers_off_the_wake_isnt_set_and_the_family_is_told_where_to_turn_them_on(
        powercfg):
    assert wake.set(21, 0, False) == "not-set"
    assert wake.status(21, 0) == wake.WakeStatus(None, [], False)
    assert "Allow wake timers" in wake.manual_step(21, 0)


def test_with_wake_timers_on_plugged_in_the_task_wakes_the_pc(powercfg):
    powercfg["RTCWAKE"] = (ENABLE, DISABLE)

    assert wake.set(21, 0, False) == "already-set"
    assert wake.status(21, 0).ours == wake_windows.ALLOWED


def test_important_wake_timers_only_dont_wake_the_pc_for_the_brief(powercfg):
    powercfg["RTCWAKE"] = (2, 2)

    assert wake.set(21, 0, False) == "not-set"


def test_a_pc_that_never_sleeps_or_hibernates_needs_no_wake(powercfg):
    powercfg["STANDBYIDLE"] = powercfg["HIBERNATEIDLE"] = (0, 0)

    assert wake.set(21, 0, False) == "never-sleeps"
    assert wake.status(21, 0).never_sleeps


def test_a_pc_that_never_sleeps_but_hibernates_needs_the_wake(powercfg):
    powercfg["STANDBYIDLE"] = (0, 0)

    assert not wake.status(21, 0).never_sleeps


def test_a_setting_powercfg_can_t_read_counts_as_off(powercfg, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda cmd, *a, **k: subprocess.CompletedProcess(
        cmd, 1, "", "The power scheme, subgroup or setting specified does not exist."))

    assert wake.status(21, 0) == wake.WakeStatus(None, [], False)


def test_cancelling_leaves_the_familys_own_setting_alone(powercfg, monkeypatch):
    ran: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, *a, **k: ran.append(cmd))

    wake.cancel()

    assert ran == []


@pytest.mark.windows
def test_this_pcs_own_power_plan_is_read(monkeypatch):
    monkeypatch.setattr(wake, "_adapter", wake_windows)

    assert wake.set(21, 0, False) in ("never-sleeps", "already-set", "not-set")
    assert wake.status(21, 0).others == []
