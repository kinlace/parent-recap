"""The Linked Device's Node program (ADR 0025), `baileys/linked_device.mjs`, which the pinned Node
runs on the Baileys tree `baileys_install` puts in Parent Recap's folder. Its modes, what it
prints and what it writes are described at the top of the program.
"""
from __future__ import annotations

from pathlib import Path

PROGRAM = Path(__file__).parent / "baileys" / "linked_device.mjs"

# How the program exits, besides 0 once it has printed the groups, or once follow was stopped.
FAILED = 1
USAGE = 2
TIMED_OUT = 3  # pair: the phone didn't pair in time; groups: WhatsApp didn't connect in time
# No auth state, or WhatsApp ended the link; follow has marked it ended in its status file, and
# the OS job doesn't start it again.
NEEDS_PAIRING = 4
