"""`install.sh --codex` installs the setup and manage skills for Codex under their Parent Recap names.

Runs only the installer's Codex step (the Python between `<<'PY'` and `PY`), against the real
skills and a fake home, so no venv or pip install is needed.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def install_codex_skills(home: Path) -> subprocess.CompletedProcess:
    script = re.search(r"<<'PY'\n(.*?)\nPY\n", (ROOT / "install.sh").read_text(), re.S).group(1)
    return subprocess.run([sys.executable, "-", str(ROOT)], input=script, capture_output=True, text=True,
                          env={**os.environ, "HOME": str(home)})


def test_installs_parent_recap_skills_that_call_each_other_the_codex_way(tmp_path):
    result = install_codex_skills(tmp_path)

    assert result.returncode == 0, result.stderr
    skills = tmp_path / ".agents" / "skills"
    assert sorted(p.name for p in skills.iterdir()) == ["parent-recap-manage", "parent-recap-setup"]
    setup = (skills / "parent-recap-setup" / "SKILL.md").read_text()
    assert "\nname: parent-recap-setup\n" in setup
    assert "$parent-recap-manage" in setup and "/parent-recap:manage" not in setup
    assert f"PLUGIN (the plugin root folder) is `{ROOT}`" in setup


def test_removes_the_skills_installed_before_the_rename(tmp_path):
    for name in ("family-brief-setup", "family-brief-manage"):
        (tmp_path / ".agents" / "skills" / name).mkdir(parents=True)
        (tmp_path / ".agents" / "skills" / name / "SKILL.md").write_text("old\n")
    (tmp_path / ".agents" / "skills" / "someone-elses").mkdir()

    assert install_codex_skills(tmp_path).returncode == 0

    assert sorted(p.name for p in (tmp_path / ".agents" / "skills").iterdir()) == [
        "parent-recap-manage", "parent-recap-setup", "someone-elses"]
