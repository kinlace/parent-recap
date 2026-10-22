"""Codex: `codex exec`, signed in with a ChatGPT account."""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from .. import tools
from ..config import LLMConfig
from ..system import profile
from . import parse, retry
from .backend import Finding, LLMReply

# The desktop apps that ship codex, in the order they're tried, in /Applications or ~/Applications.
CODEX_APPS = ("Codex.app", "ChatGPT.app")
# Where codex sits inside one of them: the ChatGPT app keeps its Codex in codex-cli/bin since
# its own Codex arrived (#203); the older place is kept for older versions of both apps.
CODEX_IN_APP = ("Contents/Resources/codex-cli/bin/codex", "Contents/Resources/codex")
CODEX_BUNDLED = tuple(os.path.join(folder, app, inside)
                      for app in CODEX_APPS
                      for folder in ("/Applications", os.path.expanduser("~/Applications"))
                      for inside in CODEX_IN_APP)
# How long doctor waits for `codex login status`.
STATUS_SECONDS = 20


def _page_installs() -> bool:
    """Whether the setup page installs the Codex CLI on this system, with no Codex app to sign in
    from (Windows); elsewhere it comes with the Codex or ChatGPT desktop app."""
    return "codex" in profile.current().page_installs


def missing() -> str:
    """What to do when codex isn't found, on this system."""
    system = profile.current()
    if _page_installs():
        return (f"install the Codex CLI on the setup page, or in {system.terminal} with "
                f"{system.install_lines['codex']}")
    return "install the Codex or ChatGPT desktop app and sign in"


def find_at(codex_path: str | None) -> str | None:
    """The codex binary to run, or the config's llm.codex_path when it leads to one. The Codex
    and ChatGPT desktop apps each ship one that updates with the app, so prefer those over
    whatever npm or brew put on PATH."""
    candidates = [os.path.expanduser(codex_path)] if codex_path else []
    candidates += CODEX_BUNDLED
    for c in candidates:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return tools.find("codex")


def _signed_out(codex: str) -> bool:
    """Whether `codex login status` says signed out, which it reads wherever Codex keeps the
    sign-in: its auth.json, or the system's own store if the family chose that. A check that
    doesn't run leaves it to the test call. Only the exit code is read: the output names the
    account."""
    try:
        proc = subprocess.run([codex, "login", "status"], capture_output=True, text=True, encoding="utf-8",
                              timeout=STATUS_SECONDS, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode != 0


class Codex:
    name = "codex"
    display_name = "Codex"
    probe_host = "chatgpt.com"

    def __init__(self, llm: LLMConfig) -> None:
        self._model = llm.model
        self._codex_path = llm.codex_path

    def program(self) -> str | None:
        return find_at(self._codex_path)

    def install_hint(self) -> str:
        return missing()

    def install_line(self) -> str | None:
        # On a Mac it comes with the Codex or ChatGPT desktop app.
        return profile.current().install_lines.get("codex")

    def program_env(self) -> dict[str, str]:
        return dict(os.environ)

    def test_token(self, token: str) -> str | None:
        return "Codex signs in with codex login, not a pasted token"

    def run(self, prompt: str, system_prompt: str, timeout: int, budget: int) -> LLMReply:
        codex = self.program()
        if not codex:
            raise RuntimeError(f"codex not found: {missing()}")
        # codex exec has no separate system prompt, so the instructions go first in one message.
        text = (f"{system_prompt}\n\n---\n\n{prompt}\n\n"
                "Output only the final JSON object. Don't run any commands, and don't read or write any files.")
        with tempfile.TemporaryDirectory(prefix="parent-recap-codex-") as work:
            out = Path(work) / "last_message.txt"
            # Isolated run: empty working dir, read-only sandbox, no web search, no saved session, and
            # none of the user's config (their MCP servers and tools stay out). Auth still comes from
            # ~/.codex, or the system's own store if they chose it (https://learn.chatgpt.com/docs/auth).
            # No shell, exec, apps or plugins either: the read-only sandbox still lets a command read
            # any file on the Mac, and approval_policy=never would run it without asking.
            cmd = [codex, "exec", "--skip-git-repo-check", "--ephemeral", "--ignore-user-config",
                   "--sandbox", "read-only", "-c", 'approval_policy="never"',
                   "-c", 'web_search="disabled"',
                   "--disable", "shell_tool", "--disable", "unified_exec",
                   "--disable", "apps", "--disable", "plugins", "--color", "never",
                   "-C", work, "-o", str(out)]
            if self._model:
                cmd += ["-m", self._model]
            cmd.append("-")  # prompt on stdin: it is far too long for argv on busy days
            what = f"codex exec (prompt: {len(text.encode('utf-8'))} B)"
            ran = retry.run_with_retry(cmd, timeout, what, budget, input=text)
            proc = ran.proc
            result = out.read_text(encoding="utf-8") if out.exists() else ""
        if proc.returncode != 0 or not result.strip():
            retry.save_diagnostics("codex_cli_failed.txt", proc, f"{codex} exec ... - <{len(text)} chars on stdin>")
            raise RuntimeError(f"codex exec failed ({proc.returncode}). stderr tail={proc.stderr[-500:]!r}")
        # No token count: -o holds only the last message, and the "tokens used" line in stderr gave
        # 3–16 per night on Codex 0.145. The --json event stream carries real usage if we need it.
        data, repaired = parse.model_json(result)
        return LLMReply(data, result, None, repaired, ran.waited)

    def check(self) -> list[Finding]:
        codex = self.program()
        if not codex:
            return [Finding("fail", "codex", f"codex command not found: {missing()}, or set "
                            "llm.codex_path in the config")]
        sign_in = (f"sign in again on the setup page, or run codex login in "
                   f"{profile.current().terminal}; Codex keeps the sign-in in ~/.codex unless its "
                   "own config says otherwise"
                   if _page_installs() else
                   "open the Codex app and sign in again with your ChatGPT account")
        if _signed_out(codex):
            return [Finding("fail", "codex", f"not signed in ({sign_in})")]
        try:
            data = self.run('Reply with only this JSON: {"ok": true}', "This is a connectivity test.",
                            timeout=120, budget=0).data
        except Exception as e:
            return [Finding("fail", "codex", f"call failed: {str(e)[:200]} (if you're signed out, "
                            f"{sign_in}. ChatGPT Free and Go can't use the Codex CLI; it needs Plus "
                            "or higher)")]
        if data.get("ok") is True:
            return [Finding("ok", "codex", f"call succeeded ({codex})")]
        return [Finding("fail", "codex", f"reachable, but the reply has the wrong shape: {str(data)[:80]}")]
