"""Whether the computer went to sleep, from a clock that stops while it sleeps beside the wall
clock (#887). A laptop closed during the evening run sleeps through it: the model call it was
waiting on is restarted when it wakes, and the Brief says it was finished late."""
from __future__ import annotations

import time

# How far the wall clock may run ahead of the awake clock before it counts as a sleep: well past
# what a clock adjustment moves it, and short enough that any real sleep counts.
SLEEP = 60


def _awake_clock() -> float:
    """Seconds the computer has been awake. macOS has a clock for it; Linux's monotonic clock stops
    in suspend too. Where it doesn't (Windows may count sleep), no sleep is ever seen."""
    uptime = getattr(time, "CLOCK_UPTIME_RAW", None)
    return time.clock_gettime(uptime) if uptime is not None else time.monotonic()


# Seams for the tests, which move one clock without the other.
_awake = _awake_clock
_wall = time.time


class Watch:
    """Started when made: whether the computer has slept since."""

    def __init__(self) -> None:
        self._awake, self._wall = _awake(), _wall()

    def slept(self) -> float:
        """Seconds the computer was asleep since the watch started."""
        return max(0.0, (_wall() - self._wall) - (_awake() - self._awake))

    def asleep(self) -> bool:
        """Whether the computer went to sleep since the watch started."""
        return self.slept() > SLEEP
