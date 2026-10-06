"""The record of what setup created on this Mac, so uninstall removes exactly that.

Each step that creates something adds it here: `install.sh` the program, its logs folder, the
folder of its pinned Python and Node, the Codex skills and the plugin copy it runs from; `schedule
install` the launchd jobs; storing a Gmail App Password its Keychain account; installing the
wilma CLI and saving its sign-in the CLI's folder and the profile's id; installing Claude Code's
plugin, and its marketplace, their names. Only what setup installed goes in: a Wilma profile,
plugin or marketplace that was there before stays when Parent Recap is uninstalled. Uninstall
still checks each recorded item is this install's before removing it. Installs from before the
record (0.4.1 and older) have none, and uninstall recognises their items in the places setup
uses.

install.sh calls it as `python -m family_brief.install_record <kind> <value> [<kind> <value> ...]`.
`get.sh --claude` installs the plugin before the program, so it writes its two kinds into the
file itself: change both together.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

KINDS = ("program", "logs", "runtime", "plugin", "codex-skill", "launchd", "keychain", "wilma-cli",
         "wilma-profile", "claude-plugin", "claude-marketplace")


def path() -> Path:
    return Path.home() / ".family" / "install-record.json"


def exists() -> bool:
    return path().exists()


def entries(kind: str) -> list[str]:
    return list(_read().get(kind, []))


def add(kind: str, value: str) -> None:
    if kind not in KINDS:
        raise ValueError(f"unknown kind {kind!r}, expected one of {', '.join(KINDS)}")
    data = _read()
    if value in data.setdefault(kind, []):
        return
    data[kind].append(value)
    p = path()
    p.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    # Opened owner-only here, since install.sh runs this without the program's umask.
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)
    tmp.replace(p)


def _read() -> dict[str, list[str]]:
    try:
        data = json.loads(path().read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def main(argv: list[str]) -> int:
    if not argv or len(argv) % 2:
        print("Usage: python -m family_brief.install_record <kind> <value> [<kind> <value> ...]",
              file=sys.stderr)
        return 2
    for kind, value in zip(argv[::2], argv[1::2]):
        add(kind, value)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
