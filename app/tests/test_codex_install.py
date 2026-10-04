"""`install.sh --codex` installs the setup and manage skills for Codex under their Parent Recap names.

Runs only the installer's Codex step, `python -m family_brief.chat_install codex-skills`, against
the real skills and a fake home, so no venv or pip install is needed.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def install_codex_skills(home: Path) -> subprocess.CompletedProcess:
    step = re.search(r'--codex" \]; then\n\s*"\$APP/.venv/bin/python" (-m [\w.]+ [\w-]+) "\$PLUGIN_ROOT"',
                     (ROOT / "install.sh").read_text()).group(1)
    return subprocess.run([sys.executable, *step.split(), str(ROOT)], capture_output=True, text=True,
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


def test_the_skills_go_into_setup_s_record_so_uninstall_removes_them(tmp_path):
    assert install_codex_skills(tmp_path).returncode == 0

    record = json.loads((tmp_path / ".family" / "install-record.json").read_text())
    skills = tmp_path / ".agents" / "skills"
    assert record["codex-skill"] == [str(skills / "parent-recap-setup"),
                                     str(skills / "parent-recap-manage")]
