"""The operating system's services, each behind its own interface with an adapter per system
(ADR 0020). Callers never run an OS tool such as `open` themselves."""
from __future__ import annotations

import importlib
import sys
from types import ModuleType

# The systems the program runs on, by Python's name for each, as their adapters' modules end:
# secrets_macos.py, secrets_windows.py, secrets_linux.py.
SYSTEMS = dict(darwin="macos", win32="windows", linux="linux")


def adapter(concern: str) -> ModuleType:
    """The adapter of the interface `concern`, such as "secrets", for the system the program runs
    on. Raises RuntimeError on any other system."""
    system = SYSTEMS.get(sys.platform)
    if system is None:
        raise RuntimeError(f"Parent Recap runs on macOS, Windows and Linux, not on {sys.platform}")
    return importlib.import_module(f"{__name__}.{concern}_{system}")
