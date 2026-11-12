#!/usr/bin/env python3
"""Write app/constraints.txt from `pip freeze --all` output: each package at its version, with the
SHA-256 checksum of every wheel PyPI has for that version, so the installs, which take wheels
only and check each against these (`--require-hashes`), find one for every Mac, Linux server and
Windows PC. The comments at the top of the file stay. CONTRIBUTING.md, "Pinned packages", says
when and how to run it.

Usage: python3 scripts/pin_hashes.py FREEZE_FILE [constraints file]   # default: app/constraints.txt

Reads PyPI's JSON API (https://pypi.org/pypi/<name>/<version>/json), standard library only.
"""
from __future__ import annotations

import http.client
import json
import sys
import urllib.request
from collections.abc import Callable
from itertools import takewhile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def pypi_wheels(name: str, version: str) -> list[str]:
    """The SHA-256 checksums of every wheel PyPI has for this version. A read cut off is tried
    again, twice."""
    for attempt in range(3):
        try:
            with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{version}/json", timeout=60) as answer:
                files = json.load(answer)["urls"]
            return [f["digests"]["sha256"] for f in files if f["packagetype"] == "bdist_wheel"]
        except (OSError, http.client.HTTPException):
            if attempt == 2:
                raise
    raise AssertionError("unreachable")


def pinned(header: str, freeze: str, wheels: Callable[[str, str], list[str]]) -> str:
    """The constraints file: `header`, then each `name==version` line of `freeze` (sorted, as pip
    freeze sorts) with its wheels' checksums. Exits naming a package with no wheel."""
    lines = [line.strip() for line in freeze.splitlines()]
    pins = sorted((line.split("==", 1) for line in lines if "==" in line and not line.startswith("#")),
                  key=lambda pin: pin[0].lower())
    out = [header.rstrip("\n")] if header.strip() else []
    for name, version in pins:
        hashes = sorted(set(wheels(name, version)))
        if not hashes:
            sys.exit(f"❌ {name} {version} has no wheel on PyPI, and the installs take wheels only.")
        out.append(f"{name}=={version} \\\n" + " \\\n".join(f"    --hash=sha256:{h}" for h in hashes))
    return "\n".join(out) + "\n"


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    freeze = Path(sys.argv[1]).read_text()
    constraints = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "app" / "constraints.txt"
    old = constraints.read_text().splitlines(keepends=True) if constraints.exists() else []
    header = "".join(takewhile(lambda line: line.startswith("#"), old))
    constraints.write_text(pinned(header, freeze, pypi_wheels))
    print(f"✅ Wrote {constraints}")


if __name__ == "__main__":
    main()
