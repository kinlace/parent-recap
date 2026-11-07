"""Puts Parent Recap Settings and Parent Recap Check where the system lists programs (ADR 0042),
so a family finds its way back to the setup page, or to doctor's output for a screenshot, without
remembering a command. Every install makes them again, so an update brings them to a family that
installed before they existed, and as this version has them. `system/entries.py` says what each
system gets; a Hosted Household's server gets none.

It never fails the install: a family without them still has the install line and the chat. On a
Mac, an app with their name that the install didn't make is left as it is; on Windows they're in
a Start menu folder of Parent Recap's own, so what has their names there is the install's.

install.sh (on a Mac) and install.ps1 call it as
`python -m family_brief.program_entries install <install folder>`.
"""
from __future__ import annotations

import sys
from pathlib import Path

from .system import entries


def install(target: Path) -> int:
    if not entries.offered():
        return 0
    for name in entries.NAMES:
        try:
            entries.place(name, target)
        except OSError as e:
            print(f"{name} wasn't put in {entries.where()}: {e}", file=sys.stderr)
    return 0


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] != "install":
        print("Usage: python -m family_brief.program_entries install <install folder>",
              file=sys.stderr)
        return 2
    return install(Path(argv[1]))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
