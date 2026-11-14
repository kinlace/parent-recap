"""What uninstall removes of eVaka besides its passwords, which its record hooks in as `survey`."""
from __future__ import annotations

from typing import TYPE_CHECKING

from ....config import Config
from ....uninstall import _file
from .collector import devices_path

if TYPE_CHECKING:
    from pathlib import Path

    from ....uninstall import Survey


def survey(s: Survey, programs: list[Path]) -> None:
    """The device cookies eVaka's sign-ins left, kept beside the state so a city doesn't email
    about a new device every evening (ADR 0039). The passwords go with the other secrets."""
    devices = devices_path(Config(kids=[]))
    if devices.exists():
        s.items.append(_file("eVaka's device cookies", devices))
