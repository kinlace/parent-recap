"""Sending an iMessage through Messages.app, which only macOS has (ADR 0020). It isn't an
interface with an adapter per system; its code stays macOS code."""
from __future__ import annotations

import subprocess


def _escape(text: str) -> str:
    """Escape text for AppleScript double-quoted string."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


def send(phone: str, text: str) -> str | None:
    """Sends `text` to `phone`; what Messages said when it failed, None when it sent."""
    # Simplified form: no service lookup (macOS Tahoe+ deprecated that).
    # Messages.app picks the iMessage-capable route automatically.
    script = (
        'tell application "Messages"\n'
        f'  send "{_escape(text)}" to buddy "{phone}"\n'
        'end tell'
    )
    proc = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    return proc.stderr.strip() if proc.returncode != 0 else None
