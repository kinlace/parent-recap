"""The update's interface for the evening run (ADR 0047): `update.update(retry_failed=False)`
doesn't try again a version that failed its check, which `parent-recap update` does. On the same
fake GitHub and installed program as test_update.py's."""
from __future__ import annotations

import pytest

from family_brief import update
from test_update import github, mac  # noqa: F401  the fixtures

pytestmark = pytest.mark.posix


def test_a_look_that_finds_a_version_that_failed_before_does_not_try_it_again(harness, github, mac):  # noqa: F811
    github.stable = "0.20.0"
    github.publish("0.20.0")
    mac.fails["0.20.0"] = ["Calendar"]
    assert update.update(None).result == "switched-back"
    installs = len(mac.installs)

    outcome = update.update(None, retry_failed=False)

    assert outcome.result == "failed" and "failed its check before" in outcome.message
    assert len(mac.installs) == installs
    assert update.update(None).result == "switched-back"  # asked for by hand, it's tried again
    assert len(mac.installs) == installs + 1
