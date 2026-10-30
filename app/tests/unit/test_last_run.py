"""A run's peak memory, from the maximum resident sizes `resource.getrusage` gives for the run and
for its child processes, in the unit each system's profile says it gives them in."""
from __future__ import annotations

import pytest

from family_brief.last_run import peak_mb
from family_brief.system import profile_linux, profile_macos, profile_windows

LINUX, MACOS = profile_linux.PROFILE.maxrss_unit, profile_macos.PROFILE.maxrss_unit


@pytest.mark.parametrize("own, children, unit, mb", [
    (180 * 1024, 40 * 1024, LINUX, 180),          # Linux counts in kilobytes
    (40 * 1024, 180 * 1024, LINUX, 180),          # a child (the wilma CLI, Node) peaked higher
    (180 * 2**20, 40 * 2**20, MACOS, 180),        # macOS counts in bytes
    (int(179.6 * 2**20), 0, MACOS, 180),          # rounded to the nearest MB
    (0, 0, LINUX, 0),
])
def test_the_peak_is_the_larger_of_the_run_s_and_its_children_s_in_mb(own, children, unit, mb):
    assert peak_mb(own, children, unit) == mb


def test_windows_gives_no_peak():
    assert profile_windows.PROFILE.maxrss_unit is None
