"""Continue in the chat: handing setup over to the chat with the AI picked, at any phase."""
from __future__ import annotations

import shlex
from http import HTTPStatus
from typing import Any

from .. import setup_save
from ..system import desktop
from .context import CHAT_SKILLS, Action, Phase, Reply, ais, chats, claude, not_offered

# What handing over can say, by AI.
CHAT_RESULTS = {"claude": ("opened", "no-terminal", "not-installed"), "codex": ("open-codex",)}
CHAT_WINDOW = "Parent Recap setup"


class Chat(Phase):
    def routes(self) -> dict[str, Action]:
        return {"api/chat": self.continue_in_chat}

    def state(self, progress: dict[str, Any]) -> dict[str, Any]:
        return {"chat": dict(self.context.chat.results)}

    def stop(self) -> None:
        desktop.forget_terminal(CHAT_WINDOW)

    def continue_in_chat(self, raw: Any) -> Reply:
        """Hands setup over to the chat with the AI picked: opens Claude Code at the setup skill
        in a Terminal window, or says what to type in Codex. The skill reads the progress and the
        answers saved so far, and carries on from the same phase and Source. The plugin or the
        Codex skills are installed first if they aren't yet, since the skill comes with them."""
        if not (isinstance(raw, dict) and set(raw) == {"ai"} and raw["ai"] in CHAT_SKILLS):
            return HTTPStatus.BAD_REQUEST, setup_save.outcome(
                "invalid-answers", "Pick Claude or ChatGPT.", errors=["ai: should be claude or codex"])
        if raw["ai"] not in ais():
            return HTTPStatus.BAD_REQUEST, not_offered()
        if raw["ai"] not in chats():
            return HTTPStatus.BAD_REQUEST, setup_save.outcome(
                "invalid-answers", "The chat sets up a Mac only.",
                errors=["ai: has no chat to continue in on this system"])
        skill = CHAT_SKILLS[raw["ai"]]
        installs = self.context.chat
        if installs.results.get(raw["ai"]) != "installed":  # the skill must be there to start
            installs.install(raw["ai"]).join()
        if raw["ai"] == "codex":
            return HTTPStatus.OK, {"result": "open-codex", "type": skill}
        program = claude().program()
        if program is None:
            return HTTPStatus.OK, {"result": "not-installed", "install": claude().install_line()}
        # Claude Code in the family's home folder, at the setup skill.
        if desktop.open_terminal([program, skill], {}, CHAT_WINDOW):
            return HTTPStatus.OK, {"result": "opened"}
        return HTTPStatus.OK, {"result": "no-terminal", "type": shlex.join([program, skill])}
