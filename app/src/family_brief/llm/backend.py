"""The interface every adapter implements, and what a call and a check give back."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, NamedTuple, Protocol


@dataclass
class LLMReply:
    """One backend call: the parsed object, the model's reply text as given, the tokens it used
    by kind ({"input", "cache_write", "cached_input", "output"}, each priced apart; None where
    the backend does not report them), whether the object needed json-repair, the seconds it
    paused for a busy model, and the model the backend says answered, if it says."""
    data: dict[str, Any]
    text: str
    usage: dict[str, int] | None = None
    repaired: bool = False
    waited: float = 0.0
    model: str | None = None


class Finding(NamedTuple):
    """One of doctor's checks of a backend: how it went, the check's key in doctor, and what to
    tell the family."""
    level: Literal["ok", "warn", "fail"]
    key: str
    detail: str


class Backend(Protocol):
    """Everything the program needs of a provider."""

    name: str  # as llm.backend in the config says it
    display_name: str  # what the Brief calls it, as in "Claude couldn't read ..."
    probe_host: str  # the host the evening run waits for the network on

    def run(self, prompt: str, system_prompt: str, timeout: int, budget: int) -> LLMReply:
        """Send one prompt and parse the JSON object the model returns. While the model is busy
        it is tried again for up to `budget` seconds (0 for none)."""
        ...

    def program(self) -> str | None:
        """The program it runs, or None when it isn't found or the backend needs none."""
        ...

    def install_hint(self) -> str:
        """What the family does to get it, on this system."""
        ...

    def check(self) -> list[Finding]:
        """doctor's checks: whether it is there and signed in, from one small call."""
        ...
