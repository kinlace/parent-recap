"""A backend in memory, for tests: it answers from the replies it is given and records each call."""
from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Union

from . import parse
from .backend import Finding, LLMReply

# One reply: the model's text, an object it writes as JSON, an exception the call raises, or a
# function that makes one of those from the prompt.
Reply = Union[str, dict, Exception, Callable[[str], Any]]


@dataclass
class Call:
    prompt: str
    system_prompt: str
    timeout: int
    budget: int


@dataclass
class Memory:
    """Answers every call with `replies`, or with the next of them when it is a list. `findings`
    are what doctor's check of it finds."""
    replies: Reply | list[Reply] = field(default_factory=dict)
    name: str = "memory"
    display_name: str = "Claude"
    probe_host: str = "model.invalid"
    findings: list[Finding] = field(default_factory=list)
    calls: list[Call] = field(default_factory=list)

    def run(self, prompt: str, system_prompt: str, timeout: int, budget: int) -> LLMReply:
        self.calls.append(Call(prompt, system_prompt, timeout, budget))
        reply = self.replies
        if isinstance(reply, list):
            if not reply:
                raise AssertionError("more model calls than replies")
            reply = reply.pop(0)
        if callable(reply):
            reply = reply(prompt)
        if isinstance(reply, Exception):
            raise reply
        text = reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)
        data, repaired = parse.model_json(text)
        return LLMReply(data, text, repaired=repaired)

    def program(self) -> str | None:
        return None

    def install_hint(self) -> str:
        return "nothing to install"

    def install_line(self) -> str | None:
        return None

    def program_env(self) -> dict[str, str]:
        return dict(os.environ)

    def test_token(self, token: str) -> str | None:
        return None

    def check(self) -> list[Finding]:
        return list(self.findings)
