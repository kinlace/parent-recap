"""Prints the names of the packages pip installs by checksum before the program, one a line.

They are what pyproject.toml's build system and dependencies require, and what each extra named
here (`google`, `test`) adds. install.sh, install.ps1 and CI install them with `--require-hashes
-c constraints.txt`, which pins each one, and each package they bring in, to one version and the
checksums of its files; then the program from this folder, which pip can't check by checksum,
with `--no-deps --no-build-isolation`, so it brings in nothing of its own and builds with the
setuptools installed here (CONTRIBUTING.md, "Pinned packages").

Run before the program is installed, by path and with -I, so the standard library only:

    python -I requirements.py [extra ...]
"""
from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path


def names(pyproject: dict, extras: list[str]) -> list[str]:
    """Each package the build system, the dependencies and `extras` name, once, in that order.
    Raises KeyError for an extra pyproject.toml doesn't have."""
    project = pyproject["project"]
    wanted = [*pyproject.get("build-system", {}).get("requires", []), *project.get("dependencies", [])]
    for extra in extras:
        wanted += project.get("optional-dependencies", {})[extra]
    return list(dict.fromkeys(re.match(r"[A-Za-z0-9._-]+", r.strip()).group() for r in wanted))


def main(argv: list[str]) -> None:
    pyproject = tomllib.loads((Path(__file__).resolve().parent / "pyproject.toml").read_text(encoding="utf-8"))
    try:
        found = names(pyproject, argv)
    except KeyError as extra:
        sys.exit(f"pyproject.toml has no extra {extra}")
    # "\n" on Windows too, where a "\r" would stay on each name in a bash `$(...)`.
    sys.stdout.reconfigure(newline="\n")
    print("\n".join(found))


if __name__ == "__main__":
    main(sys.argv[1:])
