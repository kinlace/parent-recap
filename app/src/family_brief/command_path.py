"""Puts `parent-recap` on the user's PATH, so a new Terminal or PowerShell window runs
`parent-recap doctor` without the venv's full path (#560).

The command goes in a folder of its own beside the program, `<install folder>/bin`, which holds
nothing else: the venv's own `bin` (`Scripts` on Windows) also holds its `python`, which would
take the place of the family's own. It is a link to the venv's command, or on Windows a
`parent-recap.cmd` that runs the venv's Python. The folder and the PATH entry go into setup's
record first, so uninstall takes both back out.

install.sh and install.ps1 call it as `python -m family_brief.command_path install <install folder>`.
"""
from __future__ import annotations

import sys
from pathlib import Path, PureWindowsPath

from . import install_record
from .system import profile, user_path

NAME = "parent-recap"

# On Windows, not pip's `parent-recap.exe`: pip makes that launcher on the PC, so it is new and the
# only file of its kind, which Defender's attack surface reduction rule for programs that aren't
# prevalent, old or trusted blocks on a PC its company manages (#591). The venv's python.exe is the
# same file everywhere. Its path is from the script's own folder, as cmd reads the file in the
# console's code page, which the home folder's name may not fit. Its last line is Python's, so the
# script exits with Python's exit code.
_SCRIPT = '@"%~dp0..\\app\\.venv\\{python}" -m family_brief %*\r\n'


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
    if system.links_command:
        placed = bin_folder / program.name
        if not (placed.is_symlink() and placed.readlink() == program):
            placed.unlink(missing_ok=True)
            placed.symlink_to(program)
    else:
        _write_script(bin_folder, system.path_command)
    user_path.add(bin_folder)
    return 0


def _write_script(bin_folder: Path, python: Path) -> None:
    script = bin_folder / f"{NAME}.cmd"
    text = _SCRIPT.format(python=str(PureWindowsPath(python))).encode("ascii")
    if not (script.is_file() and script.read_bytes() == text):
        script.write_bytes(text)
    # 0.9.0's copy of pip's launcher, which Windows' PATHEXT would try before the script.
    try:
        (bin_folder / f"{NAME}.exe").unlink(missing_ok=True)
    except OSError:  # it runs, as when the update was started from it; the next install removes it
        pass


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] != "install":
        print("Usage: python -m family_brief.command_path install <install folder>", file=sys.stderr)
        return 2
    return install(Path(argv[1]))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
