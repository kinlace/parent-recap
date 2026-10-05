#!/usr/bin/env python3
"""Check that every package in app/constraints.txt has a wheel (a prebuilt package) for both
kinds of Mac, on every Python version setup may use. install.sh installs only wheels, so when a
version bump drops one, CI fails here before a family's install does.

Usage: python scripts/check_wheels.py [constraints file]   # default: app/constraints.txt

Only downloads, with pip, so it runs on any machine. pip's usual settings apply (PIP_INDEX_URL…).
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Apple Silicon is recommended, and Intel works for now on a best-effort basis (README). Every
# wheel must install on macOS 13, so a wheel made only for macOS 14 or later fails the check.
MACS = {"Apple Silicon": "macosx_13_0_arm64", "Intel": "macosx_13_0_x86_64"}
# install.sh takes Python 3.11 or later, and setup installs Homebrew's, which is the newest.
# Add each new Python here once Homebrew's `python` moves to it.
PYTHONS = ["3.11", "3.12", "3.13", "3.14"]


def missing(constraints: Path, platform: str, python: str, dest: str) -> str | None:
    """pip's error if a package has no wheel for this Mac and Python, or None."""
    result = subprocess.run(
        [sys.executable, "-m", "pip", "download", "--quiet", "--disable-pip-version-check",
         "--only-binary", ":all:", "--no-deps", "--platform", platform, "--python-version", python,
         "--dest", dest, "-r", str(constraints)],
        capture_output=True, text=True)
    return None if result.returncode == 0 else (result.stderr.strip() or result.stdout.strip())


def main() -> None:
    constraints = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "app" / "constraints.txt"
    failures = []
    with tempfile.TemporaryDirectory() as dest:
        for mac, platform in MACS.items():
            for python in PYTHONS:
                if error := missing(constraints, platform, python, dest):
                    failures.append(f"{mac} Mac ({platform}), Python {python}:\n   {error}")
    if failures:
        sys.exit("❌ A pinned package has no prebuilt package for:\n" + "\n".join(failures))
    print(f"✅ Every package in {constraints.name} has a wheel for {' and '.join(MACS)} Macs, "
          f"Python {', '.join(PYTHONS)}")


if __name__ == "__main__":
    main()
