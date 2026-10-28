"""Puts `parent-recap` on the user's PATH, so a new Terminal or PowerShell window runs
`parent-recap doctor` without the venv's full path (#560).

The command goes in a folder of its own beside the program, `<install folder>/bin`, which holds
nothing else: the venv's own `bin` (`Scripts` on Windows) also holds its `python`, which would
take the place of the family's own. It is a link to the venv's command, or a copy where the
system's profile says a link won't do. The folder and the PATH entry go into setup's record
first, so uninstall takes both back out.

install.sh and install.ps1 call it as `python -m family_brief.command_path install <install folder>`.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

from . import install_record
from .system import profile, user_path

NAME = "parent-recap"


def folder(target: Path) -> Path:
    """The folder the command goes in, for the install folder `target`."""
    return target / "bin"


def install(target: Path) -> int:
    system = profile.current()
    program = target / "app" / ".venv" / system.path_command
    if not program.is_file():
        print(f"There is no program at {program}, so {NAME} wasn't put on the PATH.",
              file=sys.stderr)
        return 1
    bin_folder = folder(target)
    install_record.add("command", str(bin_folder))
    install_record.add("user-path", str(bin_folder))
    bin_folder.mkdir(exist_ok=True)
    placed = bin_folder / program.name
    if not system.links_command:
        try:
            shutil.copyfile(program, placed)
        except OSError:  # it runs, as when the update was started from it; the next one copies it
            if not placed.exists():
                raise
    elif not (placed.is_symlink() and placed.readlink() == program):
        placed.unlink(missing_ok=True)
        placed.symlink_to(program)
    user_path.add(bin_folder)
    return 0


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] != "install":
        print("Usage: python -m family_brief.command_path install <install folder>", file=sys.stderr)
        return 2
    return install(Path(argv[1]))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
