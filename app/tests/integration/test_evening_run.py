"""The evening run called through its interface, `run(cfg, options, io)`, rather than the CLI."""
from __future__ import annotations

import json
from datetime import timedelta

import pytest

from conftest import NOW
from nights import normal_night

from family_brief import run
from family_brief.config import Config


@pytest.mark.parametrize("options", [run.Options(), run.Options(lookback_hours=72)],
                         ids=["caught up", "lookback given"])
def test_the_run_leaves_the_config_it_is_given_as_it_was(harness, options):
    normal_night(harness)
    # Three nights behind, so the run reads each Source further back than its config says.
    harness.state_path.parent.mkdir(parents=True, exist_ok=True)
    harness.state_path.write_text(json.dumps({"caught_up_at": (NOW - timedelta(days=3)).isoformat()}))
    harness.set_up_sources()
    cfg = Config.model_validate(harness.config)
    before = cfg.model_dump()

    assert run.run(cfg, options, run.IO.real()) == 0

    assert harness.sent
    assert cfg.model_dump() == before
