"""Claude: `claude -p`, signed in with a Claude Pro/Max subscription's token."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .. import tools
from ..config import LLMConfig
from ..system import secrets
from . import parse, retry
from .backend import Finding, LLMReply

# Claude Code's native installer: it installs into ~/.local/bin and updates itself, where a
# global npm install can leave it unable to.
CLAUDE_INSTALL = "curl -fsSL https://claude.ai/install.sh | bash"
# Its Windows twin, for PowerShell, which puts claude.exe in %USERPROFILE%\.local\bin, without
# admin (https://code.claude.com/docs/en/setup).
CLAUDE_INSTALL_WINDOWS = "irm https://claude.ai/install.ps1 | iex"


def sessionless_env() -> dict[str, str]:
    # Started from inside a Claude Code session (setup preview, doctor), the parent's session
    # variables would make the child think it is part of that session. Only auth is kept.
    return {k: v for k, v in os.environ.items()
            if k == "CLAUDE_CODE_OAUTH_TOKEN"
            or not (k == "CLAUDECODE" or k.startswith(("CLAUDE_CODE_", "CLAUDE_AGENT_SDK_")))}


def token_env(token: str) -> dict[str, str]:
    """Env for the `claude` CLI that signs in with the subscription token and nothing else."""
    env = sessionless_env()
    env["CLAUDE_CODE_OAUTH_TOKEN"] = token
    # Make sure no conflicting auth wins
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("ANTHROPIC_AUTH_TOKEN", None)
    env.pop("ANTHROPIC_BASE_URL", None)
    return env


def install_line() -> str:
    """The line that installs Claude Code on this system."""
    return CLAUDE_INSTALL_WINDOWS if sys.platform == "win32" else CLAUDE_INSTALL


def native_dir() -> Path:
    """Where Claude Code's native installer (https://claude.ai/install.sh) puts `claude`."""
    return Path.home() / ".local" / "bin"


def find() -> str | None:
    """The claude binary to run. The native installer adds its folder to the shell's PATH, which
    a shell started before it, or the nightly job, may not have, so tools.find looks there too."""
    return tools.find("claude")


def test_call(program: str, env: dict[str, str]) -> str | None:
    """Makes one small call with `claude`, as doctor and setup check it. Returns None when it
    worked, otherwise what went wrong."""
    try:
        # Skip user settings like the nightly call does, so the check sees what the nightly run sees.
        proc = subprocess.run([program, "-p", "Reply with just the two letters OK",
                               "--output-format", "json", "--setting-sources", "",
                               "--no-session-persistence"],
                              capture_output=True, text=True, encoding="utf-8", timeout=120, env=env,
                              stdin=subprocess.DEVNULL)
        data = json.loads(proc.stdout or "{}")
    except (OSError, ValueError, subprocess.TimeoutExpired) as e:
        return str(e)
    if data.get("is_error") or proc.returncode != 0:
        return str(data.get("result") or proc.stderr)
    return None


def signed_in_env() -> tuple[dict[str, str], str]:
    """Build env for `claude` CLI. Prefer subscription OAuth over API key.

    Returns (env_dict, auth_label) where label is one of:
      - "keychain-oauth"   → CLAUDE_CODE_OAUTH_TOKEN (subscription, free)
      - "keychain-api-key" → ANTHROPIC_API_KEY (paid API)
      - "keychain-needs-prompt" → one is stored, but macOS asks for the Keychain password
                             before it can be read, so whatever the parent process set is used
      - "inherited"        → fall through to whatever the parent process set
    """
    needs_prompt = False

    def read(account: str) -> str | None:
        nonlocal needs_prompt
        try:
            return (secrets.get(account) or "").strip() or None
        except secrets.SecretsError:  # there, or maybe there, but not read: never "missing"
            needs_prompt = True
        return None

    oauth = read("claude-oauth-token")
    if oauth:
        return token_env(oauth), "keychain-oauth"
    env = sessionless_env()
    api_key = read("anthropic-api-key")
    if api_key:
        env["ANTHROPIC_API_KEY"] = api_key
        env.pop("ANTHROPIC_BASE_URL", None)
        env.pop("ANTHROPIC_AUTH_TOKEN", None)
        return env, "keychain-api-key"
    return env, "keychain-needs-prompt" if needs_prompt else "inherited"


# The claude CLI's usage fields, by the kind of token LLMReply.usage calls them.
_USAGE = {"input": "input_tokens", "cache_write": "cache_creation_input_tokens",
          "cached_input": "cache_read_input_tokens", "output": "output_tokens"}


def reply_of(stdout: str) -> tuple[str, dict[str, int] | None, str | None]:
    """The reply text, the tokens by kind and the model that wrote most of the reply."""
    envelope = json.loads(stdout)
    result = envelope.get("result", "")
    usage = envelope.get("usage") or {}
    by_model = envelope.get("modelUsage") or {}
    model = max(by_model, key=lambda m: (by_model[m] or {}).get("outputTokens") or 0) if by_model else None
    text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
    return text, ({kind: usage.get(k) or 0 for kind, k in _USAGE.items()} if usage else None), model


class Claude:
    name = "claude"
    display_name = "Claude"
    probe_host = "api.anthropic.com"

    def __init__(self, llm: LLMConfig) -> None:
        self._model = llm.model

    def program(self) -> str | None:
        return find()

    def install_hint(self) -> str:
        return install_line()

    def run(self, prompt: str, system_prompt: str, timeout: int, budget: int) -> LLMReply:
        # Isolated run, like codex: no built-in tools, none of the user's MCP servers, and an empty
        # working dir so no project CLAUDE.md or settings get picked up. The model only reads the prompt.
        # --strict-mcp-config still lets managed (admin) servers load, so deny every MCP tool too.
        # An empty --setting-sources skips user settings, so the user's ~/.claude/CLAUDE.md, hooks
        # and plugins stay out too (--bare would as well, but it rejects subscription OAuth).
        # --system-prompt replaces Claude Code's own coding-agent prompt: it costs tokens every night
        # and can pull the model toward coding behaviour.
        # --no-session-persistence: otherwise every night leaves the prompt and reply in ~/.claude/projects.
        # The prompt goes on stdin: a catch-up night can pass macOS's 1 MiB argv cap, and argv shows in `ps`.
        # By name when PATH has it, as before; the full path only for one PATH doesn't lead to.
        program = "claude" if shutil.which("claude") else find() or "claude"
        cmd = [program, "-p", "--output-format", "json",
               "--system-prompt", system_prompt, "--tools", "", "--strict-mcp-config",
               "--disallowedTools", "mcp__*", "--setting-sources", "", "--no-session-persistence"]
        if self._model:
            cmd += ["--model", self._model]
        env, auth_label = signed_in_env()
        what = f"claude CLI (prompt: {len(prompt.encode('utf-8'))} B, auth: {auth_label})"
        with tempfile.TemporaryDirectory(prefix="parent-recap-claude-") as work:
            ran = retry.run_with_retry(cmd, timeout, what, budget, env=env, cwd=work, input=prompt)
        proc = ran.proc
        if proc.returncode != 0:
            retry.save_diagnostics("claude_cli_failed.txt", proc,
                                   f"claude -p --output-format json --system-prompt <{len(system_prompt)} chars> "
                                   "--tools '' --strict-mcp-config --disallowedTools 'mcp__*' --setting-sources '' "
                                   f"--no-session-persistence <{len(prompt)} chars on stdin>")
            raise RuntimeError(
                f"claude CLI failed ({proc.returncode}). "
                f"stderr={proc.stderr!r} stdout[:500]={proc.stdout[:500]!r}"
            )
        text, usage, model = reply_of(proc.stdout)
        data, repaired = parse.cli_response(proc.stdout)
        return LLMReply(data, text, usage, repaired, ran.waited, model)

    def check(self) -> list[Finding]:
        windows = sys.platform == "win32"
        claude = find()
        if not claude and windows:
            return [Finding("fail", "claude", "claude command not found on PATH or in "
                            "%USERPROFILE%\\.local\\bin (install Claude Code on the setup page, or "
                            f"in PowerShell with: {install_line()})")]
        if not claude:
            return [Finding("fail", "claude", "claude command not found on PATH, in ~/.local/bin "
                            "or the other usual folders, or by your login shell (install Claude "
                            f"Code with: {install_line()})")]
        from ..setup_steps import PLAIN_TERMINAL
        env, label = signed_in_env()
        where = "" if secrets.reachable() else f", {PLAIN_TERMINAL}"
        if label == "inherited" and windows:
            # `setup claude`'s dialog is the Mac's: on Windows the token is pasted into the page.
            return [Finding("fail", "claude", "no Claude token in Credential Manager, so the "
                            "evening Brief can't call Claude (sign in to Claude on the setup page: "
                            "paste the install line into PowerShell again)")]
        if label == "inherited":
            # This shell's own sign-in can work while the evening job, which has none, can't.
            return [Finding("fail", "claude", "no claude-oauth-token in the Keychain, so the "
                            "evening Brief can't call Claude (store one with parent-recap setup "
                            f"claude{where})")]
        if label == "keychain-needs-prompt":
            # Stored by an earlier version, so it trusts that version's Python and not security.
            return [Finding("fail", "claude", "the Claude token is in the Keychain, but macOS asks "
                            "for the Keychain password before reading it, which the evening Brief "
                            f"can't answer (store it again with parent-recap setup claude{where})")]
        error = test_call(claude, env)
        if error is not None:
            return [Finding("fail", "claude", f"call failed (auth: {label}): {error[:120]}")]
        return [Finding("ok", "claude", f"call succeeded (auth: {label})")]
