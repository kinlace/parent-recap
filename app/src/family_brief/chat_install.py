"""What lets a family ask for changes in the chat after setup: Claude Code's plugin, or the two
Codex skills.

The setup page installs one of them once Welcome knows the AI (#98), since the one install line
without a flag installs only the program. `install.sh --codex` installs the Codex skills through
this module too, as `python -m family_brief.chat_install codex-skills <plugin folder>`.

The Claude Code plugin comes from the `kinlace/parent-recap#stable` marketplace, as `get.sh
--claude` installs it: for this Mac user, updated if it's there, and switched to that marketplace
from a folder a release zip was unzipped into. The plugin's name before 0.4.0 is removed first.
`get.sh --claude` does the same in bash, since it runs before the program is installed: change
both together.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import install_record
from .summarize import _sessionless_env

REPO = "kinlace/parent-recap"
MARKETPLACE = "kinlace"
PLUGIN = f"parent-recap@{MARKETPLACE}"
OLD_PLUGIN, OLD_MARKETPLACE = "family-brief@family-brief", "family-brief"  # before 0.4.0
CLAUDE_SECONDS = 180  # adding the marketplace downloads it from GitHub
SKILLS = ("setup", "manage")


def install_claude_plugin(program: str) -> str:
    """Installs or updates the plugin in Claude Code: `installed`, or `install-failed`. The
    plugin and the marketplace go in setup's record when they weren't there in any form before."""
    try:
        plugins = _listed(program, "plugin", "list", "--json")
        marketplaces = _listed(program, "plugin", "marketplace", "list", "--json")
        if any(p.get("id") == OLD_PLUGIN for p in plugins):
            _claude(program, "plugin", "uninstall", OLD_PLUGIN)
        if any(m.get("name") == OLD_MARKETPLACE for m in marketplaces):
            _claude(program, "plugin", "marketplace", "remove", OLD_MARKETPLACE)
        installed = any(p.get("id") == PLUGIN for p in plugins)
        ours = next((m for m in marketplaces if m.get("name") == MARKETPLACE), None)
        new = [*([] if installed else [("claude-plugin", PLUGIN)]),
               *([] if ours else [("claude-marketplace", MARKETPLACE)])]
        # From an unzipped release, updating `kinlace` would only reread that folder.
        if ours is not None and not re.fullmatch(rf"{REPO}(#.*)?", str(ours.get("repo", ""))):
            if installed:
                _claude(program, "plugin", "uninstall", PLUGIN)
            _claude(program, "plugin", "marketplace", "remove", MARKETPLACE)
            ours, installed = None, False
        if ours is not None:
            _claude(program, "plugin", "marketplace", "update", MARKETPLACE)
        else:
            _claude(program, "plugin", "marketplace", "add", f"{REPO}#stable")
        if installed:
            _claude(program, "plugin", "update", PLUGIN)
        else:
            # User scope, so the plugin isn't tied to the folder Claude Code starts in.
            _claude(program, "plugin", "install", PLUGIN, "--scope", "user")
    except (OSError, subprocess.SubprocessError, ValueError):
        return "install-failed"
    for kind, value in new:
        install_record.add(kind, value)
    return "installed"


def claude_installed(program: str) -> tuple[list[str], list[str]]:
    """The plugins (by id) and the marketplaces (by name) installed in Claude Code. Raises if
    Claude Code can't say."""
    plugins = _listed(program, "plugin", "list", "--json")
    marketplaces = _listed(program, "plugin", "marketplace", "list", "--json")
    return [str(p.get("id")) for p in plugins], [str(m.get("name")) for m in marketplaces]


def uninstall_claude_plugin(program: str, plugin: str) -> None:
    _claude(program, "plugin", "uninstall", plugin)


def remove_claude_marketplace(program: str, marketplace: str) -> None:
    _claude(program, "plugin", "marketplace", "remove", marketplace)


def _claude(program: str, *args: str) -> str:
    """Runs `claude <args>` and returns what it printed; raises if it failed."""
    proc = subprocess.run([program, *args], capture_output=True, text=True, timeout=CLAUDE_SECONDS,
                          stdin=subprocess.DEVNULL, env=_sessionless_env())
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, args[:3])
    return proc.stdout


def _listed(program: str, *args: str) -> list[dict[str, Any]]:
    listed = json.loads(_claude(program, *args))
    if not isinstance(listed, list):
        raise ValueError("not a list")
    return [entry for entry in listed if isinstance(entry, dict)]


def install_codex_skills(root: Path) -> list[Path]:
    """Installs the setup and manage skills for Codex, which reads user skills from
    ~/.agents/skills, from the plugin folder `root`, and adds them to setup's record. They get
    unique names, Codex's $skill syntax, and a note saying where the plugin folder is."""
    skills = Path.home() / ".agents" / "skills"
    written = []
    for name in SKILLS:
        shutil.rmtree(skills / f"family-brief-{name}", ignore_errors=True)  # their names before 0.4.0
        text = (root / "skills" / name / "SKILL.md").read_text()
        text = text.replace(f"\nname: {name}\n", f"\nname: parent-recap-{name}\n", 1)
        text = re.sub(r"/parent-recap:(setup|manage)", r"$parent-recap-\1", text)
        head, sep, body = text.partition("\n# ")
        title, _, rest = body.partition("\n")
        note = f"\n\n> This skill is installed in Codex; PLUGIN (the plugin root folder) is `{root}`.\n"
        out = skills / f"parent-recap-{name}" / "SKILL.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(head + sep + title + note + rest)
        install_record.add("codex-skill", str(out.parent))
        written.append(out.parent)
    return written


def plugin_copy() -> Path | None:
    """The plugin folder install.sh ran from, which the Codex skills point to: the copy the install
    line downloaded to ~/FamilyBrief/plugin, as setup's record has it. None without one."""
    for recorded in reversed(install_record.entries("plugin")):
        folder = Path(recorded)
        try:
            name = json.loads((folder / ".claude-plugin" / "plugin.json").read_text()).get("name")
        except (OSError, ValueError, AttributeError):
            continue
        if name == "parent-recap" and (folder / "skills" / "setup" / "SKILL.md").is_file():
            return folder
    return None


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] != "codex-skills":
        print("Usage: python -m family_brief.chat_install codex-skills <plugin folder>",
              file=sys.stderr)
        return 2
    for folder in install_codex_skills(Path(argv[1])):
        print(f"✅ Codex skill installed: ${folder.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
