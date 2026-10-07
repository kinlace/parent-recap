"""The Python macOS allowed to read WhatsApp, as setup's WhatsApp step found it (ADR 0011).

App Management names a Python by its real path. Once Parent Recap's pinned Python changes, the
evening job runs on another file, which macOS hasn't allowed, and WhatsApp quietly stops being
read. Setup records the real path once WhatsApp has been read, so doctor can say when it's no
longer the evening job's. Installs from before the record have none, and aren't compared.
"""
from __future__ import annotations

import json
import os
from pathlib import Path


def path() -> Path:
    return Path.home() / ".family" / "whatsapp-python.json"


def granted() -> str | None:
    try:
        data = json.loads(path().read_text())
    except (OSError, ValueError):
        return None
    python = data.get("python") if isinstance(data, dict) else None
    return python if isinstance(python, str) else None


def record(python: str) -> None:
    """Best effort: without it, doctor only can't say the Python changed."""
    file = path()
    try:
        file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp = file.with_suffix(".tmp")
        tmp.write_text(json.dumps({"python": os.path.realpath(python)}, indent=2) + "\n")
        tmp.chmod(0o600)
        tmp.replace(file)
    except OSError:
        pass
