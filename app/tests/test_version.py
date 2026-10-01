"""The plugin manifest and the Python package must ship the same version."""
from __future__ import annotations

import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_plugin_manifest_version_matches_python_package():
    manifest = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())["version"]
    package = tomllib.loads((ROOT / "app" / "pyproject.toml").read_text())["project"]["version"]
    assert manifest == package, f"plugin.json says {manifest}, app/pyproject.toml says {package}"
