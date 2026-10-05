"""Every GitHub Actions workflow parses, so a broken one fails here instead of silently running
no jobs. A tests workflow that doesn't parse reports no check at all, and PRs then merge on the
other checks alone (#128)."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

WORKFLOWS = sorted((Path(__file__).resolve().parents[2] / ".github" / "workflows").glob("*.yml"))


def test_there_are_workflows():
    assert {p.name for p in WORKFLOWS} >= {"tests.yml", "release.yml"}


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_the_workflow_parses_and_has_jobs(path: Path):
    workflow = yaml.safe_load(path.read_text())
    assert isinstance(workflow, dict) and workflow.get("jobs"), f"{path.name} has no jobs"
    for name, job in workflow["jobs"].items():
        assert job.get("steps") or job.get("uses"), f"{path.name}: job {name} has no steps"
