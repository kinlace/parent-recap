"""What the setup page's phases share: the context the server gives each of them, the base every
phase is made from, and the few answers more than one phase gives.

A phase holds what's its own, such as a sign-in under way; what more than one needs, such as the
saving lock, the install threads, the clock and the timeouts, is the context's, so a test sets a
clock or a timeout on the context instead of patching a module.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from http import HTTPStatus
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from .. import chat_install, llm, setup_connect, setup_save, setup_whatsapp
from ..config import Config
from ..setup_connect import Reply
from ..system import profile

# A call the page makes: its answers, as parsed from JSON, to the reply.
Action = Callable[[Any], Reply]
# A file a phase serves besides the page's own: its body, type and headers, or None for not found.
Served = tuple[bytes, str, dict[str, str]]
Page = Callable[[], "Served | None"]
# The page's languages, in the order it offers them, each named in itself.
LANGUAGES = {"fi": "Suomi", "en": "English", "zh": "中文"}
# The AIs Welcome knows, Claude in Claude Code, ChatGPT in Codex and, for an invited Household,
# the maintainers' gateway (ADR 0014). Which it offers, in which order, is the system profile's.
AIS = ("claude", "codex", "gateway")
# The setup skill as each AI's chat starts it. Claude Code opens in a Terminal window; a Codex
# family is told what to type in Codex.
CHAT_SKILLS = {"claude": "/parent-recap:setup", "codex": "$parent-recap-setup"}


@dataclass
class Timeouts:
    """How long the server and its phases wait, in seconds."""
    idle: float = 30 * 60  # the page stops after this long without a request
    poll: float = 1.0  # how often the server looks for a stop or the idle time
    ai_check: float = 20  # an AI's own status check
    claude_sign_in: float = 600  # Claude's sign-in in the browser
    wilma_window: float = 600  # the Terminal window Wilma's sign-in falls back to
    whatsapp_read: float = setup_whatsapp.WHATSAPP_READ_SECONDS
    brief: float = 900  # making the first Brief, reading the Sources included


class ChatInstalls:
    """What lets the family ask for changes in the chat later, by AI, installed in the
    background once Welcome has saved it: `installing`, `installed`, `install-failed`,
    `not-installed` (no Claude Code) or `no-plugin-copy` (the program wasn't installed by the
    install line). `api/state` says how each went."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.results: dict[str, str] = {}
        self._threads: dict[str, threading.Thread] = {}  # each AI's latest install

    def install(self, name: str) -> threading.Thread:
        """Installs Claude Code's plugin or the Codex skills, unless they're being installed
        already."""
        with self._lock:
            if self.results.get(name) == "installing":
                return self._threads[name]
            self.results[name] = "installing"
            thread = threading.Thread(target=self._installing, args=(name,), daemon=True)
            self._threads[name] = thread
        thread.start()
        return thread

    def join(self) -> None:
        """Waits for every install: one isn't cut off halfway."""
        with self._lock:
            threads = list(self._threads.values())
        for thread in threads:
            thread.join()

    def _installing(self, name: str) -> None:
        try:
            if name == "claude":
                program = claude().program()
                result = "not-installed" if program is None \
                    else chat_install.install_claude_plugin(program)
            else:
                root = chat_install.plugin_copy()
                if root is None:
                    result = "no-plugin-copy"
                else:
                    chat_install.install_codex_skills(root)
                    result = "installed"
        except Exception:  # never leave it installing
            result = "install-failed"
        self.results[name] = result


@dataclass
class Context:
    """What the setup page's phases share, for the config at `config`."""
    config: Path
    clock: Callable[[], float] = time.monotonic  # the idle clock
    timeouts: Timeouts = field(default_factory=Timeouts)
    # The one saving lock, with setup's progress through Connect.
    connect: setup_connect.ConnectSteps = field(init=False)
    chat: ChatInstalls = field(default_factory=ChatInstalls, init=False)
    stopped: threading.Event = field(default_factory=threading.Event, init=False)
    # One read of the senders, one health check, one first Brief and one Finish at a time.
    working: threading.Lock = field(default_factory=threading.Lock, init=False)
    # The latest word of the health check (`health`) and of Finish (`finish`), which going back
    # to Connect waits for and forgets.
    runs: dict[str, dict[str, Any]] = field(default_factory=dict, init=False)
    # Every outcome is true, or the family finished for now: the server stops once that's been said.
    closing: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        self.connect = setup_connect.ConnectSteps(self.config)

    def progress(self) -> dict[str, Any]:
        return setup_save.read(self.config)["progress"]

    def data(self) -> dict[str, Any]:
        """The config as saved so far, or empty when there's none yet."""
        try:
            data = yaml.safe_load(self.config.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            return {}
        return data if isinstance(data, dict) else {}

    def section(self, key: str) -> dict[str, Any]:
        return section(self.data(), key)

    def model(self) -> Config:
        """The config as saved so far, read as the config model, or an empty one."""
        try:
            return Config.model_validate({"kids": [], **self.data()})
        except ValidationError:
            return Config(kids=[])

    def chosen_language(self) -> str | None:
        """The language already picked, from the config: None until there's one the page offers."""
        language = self.data().get("summary_language")
        return language if language in LANGUAGES else None

    def gmail_address(self) -> str | None:
        """The Household's Gmail address, the setup parent's, once it's known."""
        address = self.section("gmail").get("username")
        return address if isinstance(address, str) and address else None


class Phase:
    """A phase of setup on the page: the calls it answers, the files it serves, its part of
    `api/state`, and what it ends when the server stops."""

    def __init__(self, context: Context) -> None:
        self.context = context

    def routes(self) -> dict[str, Action]:
        """The page's calls this phase answers, by the path under the page's address."""
        return {}

    def pages(self) -> dict[str, Page]:
        """The files this phase serves, by the path under the page's address."""
        return {}

    def state(self, progress: dict[str, Any]) -> dict[str, Any]:
        """This phase's part of `api/state`, with setup's `progress` as saved."""
        return {}

    def stop(self) -> None:
        """Ends what this phase has under way, as the server stops."""


def section(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    return value if isinstance(value, dict) else {}


def ais() -> tuple[str, ...]:
    """The AIs the page offers on this system, Welcome's default first."""
    return profile.current().ais


def chats() -> dict[str, str]:
    """The AIs with a chat to continue setup in on this system, and their setup skill. The skills
    set up a Mac, so Windows has none yet."""
    return {ai: skill for ai, skill in CHAT_SKILLS.items() if ai in profile.current().chats}


def claude() -> llm.Backend:
    """Claude, which the page signs in, installs and opens the chat setup in on its own steps."""
    return llm.named("claude")


def claude_missing() -> dict[str, Any]:
    """What the page says without Claude Code: on a Mac, with the line that installs it. On
    Windows the page installs it itself."""
    if "claude" in profile.current().page_installs:
        return {"result": "not-installed"}
    return {"result": "not-installed", "install": claude().install_line()}


def not_offered() -> dict[str, Any]:
    return setup_save.outcome("invalid-answers", "Pick one of the AIs the page offers.",
                              errors=[f"ai: should be one of {', '.join(ais())}"])


def nothing_to_give() -> dict[str, Any]:
    return setup_save.outcome("invalid-answers", "Nothing to give.", errors=["answers: should be {}"])


def keychain_code(extra: dict[str, Any]) -> dict[str, Any]:
    """Only macOS's code from what a Keychain step reports: a failed test call's error isn't
    returned, since it can name the Mac's files."""
    return {"code": extra["code"]} if "code" in extra else {}
