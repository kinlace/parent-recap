"""The model, faked through the llm package's in-memory Backend (ADR 0023).

Whichever backend the config names is its real adapter, which finds its program, checks its
sign-in and names itself as it does for a family; only its calls to a model go to `backend`, the
in-memory one: the Brief's and the translations', and the small test calls doctor and setup make.
`backend.calls` records each one, in order. How an adapter reaches its model (the CLI's arguments
and environment, the gateway's HTTP, trying a busy model again) is tested in the adapter's own
tests (integration/test_llm_*.py), against its transport.

Also here: the commands of Claude Code and Codex that aren't a model call, their sign-in status
and Claude Code's plugins, which the setup page, doctor and uninstall run; and the adapters'
transports for their own tests, the model's program (ModelProcess) and the gateway's HTTP
(GatewayServer), which meet nothing but the model.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import requests

from family_brief import gateway as api, llm
from family_brief.llm import claude, memory
from family_brief.llm.backend import LLMReply

# The model's reply when a test gives none: it finds nothing to say, so the night sends no Brief
# (#385). A test that needs one sent has it reply SAYS_SOMETHING (conftest.py).
QUIET = {"per_kid": [], "calendar_events": [], "message_digest": ""}


class FakeModel:
    """The model every backend calls, and the AI programs' own commands."""

    def __init__(self, mp: pytest.MonkeyPatch) -> None:
        # What the model answers is `reply`: the same for every call, or a list with one per
        # call, in the order they are made, where an exception is raised as the adapter raises
        # it and a function makes the reply, or the exception, from the prompt it is given.
        self.backend = memory.Memory(replies=dict(QUIET))
        self.error: str | None = None        # every call fails with this, whatever `reply` says
        # For each call in backend.calls, the backend it went through, and what that backend
        # reached for it: the program it found (None for none), or the gateway's host.
        self.served_by: list[str] = []
        self.reached: list[str | None] = []
        self.signed_in: dict[str, bool] = {"claude": True, "codex": True}  # what their status says
        # Claude Code's plugins and marketplaces, as `claude plugin ... list --json` gives them,
        # and each `claude plugin` command run, without the program. Removing one takes it off
        # its list, unless Claude Code refuses to remove.
        self.claude_plugins: list[dict[str, Any]] = []
        self.claude_marketplaces: list[dict[str, Any]] = []
        self.plugin_calls: list[list[str]] = []
        self.claude_removes = True

        for name, adapter in list(llm._ADAPTERS.items()):
            mp.setitem(llm._ADAPTERS, name, self._faked(adapter))
        mp.setattr(claude, "test_call", self._claude_test_call)
        mp.setattr(api, "post", self._gateway_post)

    @property
    def reply(self) -> Any:
        return self.backend.replies

    @reply.setter
    def reply(self, value: Any) -> None:
        self.backend.replies = value

    @property
    def calls(self) -> list[memory.Call]:
        return self.backend.calls

    def prompt(self, i: int = -1) -> str:
        return self.calls[i].prompt

    def payload(self, i: int = -1) -> dict[str, Any]:
        """The JSON the program put after the prompt's instructions: the night's Messages."""
        prompt = self.prompt(i)
        return json.loads(prompt[prompt.index("\n{") + 1:])

    # The model calls
    def _call(self, via: str, reached: str | None, prompt: str, system_prompt: str, timeout: int,
              budget: int) -> LLMReply:
        self.served_by.append(via)
        self.reached.append(reached)
        if self.error is not None:
            self.backend.calls.append(memory.Call(prompt, system_prompt, timeout, budget))
            raise api.GatewayError(self.error, 500) if via == "gateway" else RuntimeError(self.error)
        return self.backend.run(prompt, system_prompt, timeout, budget)

    def _faked(self, adapter: type) -> type:
        """`adapter`, with its model calls made to the in-memory Backend."""
        model = self

        def run(self: Any, prompt: str, system_prompt: str, timeout: int, budget: int) -> LLMReply:
            reached = self.probe_host if self.name == "gateway" else self.program()
            return model._call(self.name, reached, prompt, system_prompt, timeout, budget)
        return type(adapter.__name__, (adapter,), {"run": run})

    def _claude_test_call(self, program: str, _env: dict[str, str]) -> str | None:
        """doctor's and setup's small call to Claude: None when the model answers."""
        try:
            self._call("claude", program, "Reply with just the two letters OK", "", 120, 0)
        except Exception as e:  # noqa: BLE001  any failure, as the CLI's output says it
            return str(e)
        return None

    def _gateway_post(self, url: str, _key: str, system: str, prompt: str, timeout: float,
                      max_tokens: int = api.MAX_TOKENS) -> requests.Response:
        """The gateway's small test call, as doctor and setup make it (its Brief's calls go
        through the faked adapter). A GatewayError the model raises is the gateway's answer."""
        reply = self._call("gateway", api.host(url), prompt, system, int(timeout), 0)
        return _response(200, {"type": "message", "role": "assistant", "stop_reason": "end_turn",
                               "content": [{"type": "text", "text": reply.text}]})

    # Claude Code's and Codex's own commands
    def program_command(self, cmd: list[str], prog: str,
                        **_k: Any) -> subprocess.CompletedProcess | None:
        """Their sign-in checks, answered the way each one does, and Claude Code's plugins as JSON
        lists. None for any other command."""
        if prog == "claude" and cmd[1:3] == ["auth", "status"]:
            signed_in = self.signed_in["claude"]
            out = {"loggedIn": signed_in, **({"authMethod": "claude.ai"} if signed_in else {})}
            return subprocess.CompletedProcess(cmd, 0 if signed_in else 1, json.dumps(out), "")
        if prog == "claude" and cmd[1:2] == ["plugin"]:
            self.plugin_calls.append(cmd[1:])
            return self._plugin_command(cmd)
        if prog == "codex" and cmd[1:3] == ["login", "status"]:
            if self.signed_in["codex"]:
                return subprocess.CompletedProcess(cmd, 0, "", "Logged in using ChatGPT\n")
            return subprocess.CompletedProcess(cmd, 1, "", "Not logged in\n")
        return None

    def _plugin_command(self, cmd: list[str]) -> subprocess.CompletedProcess:
        args = cmd[2:]
        listed = {("list", "--json"): self.claude_plugins,
                  ("marketplace", "list", "--json"): self.claude_marketplaces}.get(tuple(args))
        if listed is not None:
            return subprocess.CompletedProcess(cmd, 0, json.dumps(listed), "")
        removing = args[:1] == ["uninstall"] or args[:2] == ["marketplace", "remove"]
        if removing and not self.claude_removes:
            return subprocess.CompletedProcess(cmd, 1, "", "Failed to uninstall")
        name = args[-1]
        # A list that isn't one (a test's {"error": ...}) is Claude Code unable to say what it has.
        if args[:1] == ["uninstall"] and isinstance(self.claude_plugins, list):
            self.claude_plugins = [p for p in self.claude_plugins if p.get("id") != name]
        elif args[:2] == ["marketplace", "remove"] and isinstance(self.claude_marketplaces, list):
            assert not any(str(p.get("id")).endswith("@" + name) for p in self.claude_plugins), \
                "the plugin goes before its marketplace"
            self.claude_marketplaces = [m for m in self.claude_marketplaces if m.get("name") != name]
        return subprocess.CompletedProcess(cmd, 0, "[]", "")


# ── The adapters' transports, for their own tests

@dataclass
class FailedCall:
    """In a list of replies: this call's process exits non-zero with `stderr`. The gateway answers
    it with HTTP 500 and `stderr` as the error's message."""
    stderr: str


@dataclass
class HTTPReply:
    """In a list of the gateway's replies: it answers this call with HTTP `status` and `body` (JSON
    for a dict)."""
    status: int
    body: str | dict[str, Any]


@dataclass
class ProcessCall:
    """One run of the model's program."""
    argv: list[str]
    stdin: str | None
    cwd: str | None
    cwd_contents: list[str] | None  # what was in cwd when the process started
    env: dict[str, str] | None
    stdin_source: Any  # the `stdin=` argument, e.g. subprocess.DEVNULL


def _next(replies: Any, prompt: str | None) -> Any:
    """The reply to this call: `replies`, or the first of them, taken off, when it is a list."""
    reply = replies
    if isinstance(replies, list):
        assert replies, "more model calls than replies"
        reply = replies.pop(0)
    return reply(prompt) if callable(reply) else reply


def _text(reply: str | dict) -> str:
    return reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)


class ModelProcess:
    """The model's program, `claude -p` or `codex exec`, for that adapter's own tests: every
    process started is it, recorded in `calls` and answered from `replies` as the `cli` answers
    (the same reply every call, or one per call from a list, where FailedCall makes that call
    fail and a function makes the reply from the prompt it is given): claude prints its JSON
    envelope, codex writes the reply to the file its -o names."""

    def __init__(self, cli: str, mp: pytest.MonkeyPatch) -> None:
        self.cli = cli
        self.replies: Any = dict(QUIET)
        self.calls: list[ProcessCall] = []
        mp.setattr(subprocess, "run", self.run)

    def run(self, cmd: list[str], *_a: Any, input: str | None = None, cwd: str | None = None,
            env: dict[str, str] | None = None, stdin: Any = None,
            **_k: Any) -> subprocess.CompletedProcess:
        contents = sorted(p.name for p in Path(cwd).iterdir()) if cwd else None
        self.calls.append(ProcessCall(list(cmd), input, cwd, contents,
                                      dict(env) if env is not None else None, stdin))
        reply = _next(self.replies, input)
        if isinstance(reply, FailedCall):
            return subprocess.CompletedProcess(cmd, 1, "", reply.stderr)
        if self.cli == "codex":
            Path(cmd[cmd.index("-o") + 1]).write_text(_text(reply))
            return subprocess.CompletedProcess(cmd, 0, "", "")
        envelope = {"type": "result", "subtype": "success", "is_error": False, "result": _text(reply)}
        return subprocess.CompletedProcess(cmd, 0, json.dumps(envelope, ensure_ascii=False), "")


@dataclass
class GatewayRequest:
    """One request to the gateway: its URL, its user message, headers and JSON body."""
    url: str
    prompt: str
    headers: dict[str, str]
    body: dict[str, Any]


class GatewayServer:
    """The gateway's /v1/messages, for the gateway adapter's own tests, answering as an
    Anthropic-format endpoint does from `replies` (as ModelProcess takes them, where an HTTPReply
    sets its status and an exception is raised, as requests raises a timeout or a refused
    connection). `requests` records each request."""

    def __init__(self, mp: pytest.MonkeyPatch) -> None:
        self.replies: Any = dict(QUIET)
        self.requests: list[GatewayRequest] = []
        mp.setattr(api.requests, "post", self.post)

    def post(self, url: str, **kwargs: Any) -> requests.Response:
        body = kwargs["json"]
        [message] = body["messages"]
        self.requests.append(GatewayRequest(url, message["content"], dict(kwargs["headers"]), body))
        reply = _next(self.replies, message["content"])
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, HTTPReply):
            return _response(reply.status, reply.body)
        if isinstance(reply, FailedCall):
            return _response(500, {"type": "error", "error": {"type": "api_error",
                                                              "message": reply.stderr}})
        return _response(200, {
            "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-haiku-5-5",
            "content": [{"type": "text", "text": _text(reply)}], "stop_reason": "end_turn",
            "usage": {"input_tokens": 1200, "output_tokens": 300, "cache_creation_input_tokens": 0,
                      "cache_read_input_tokens": 0}})


def _response(status: int, body: str | dict[str, Any]) -> requests.Response:
    r = requests.Response()
    r.status_code = status
    r._content = (body if isinstance(body, str) else json.dumps(body)).encode()
    r.encoding = "utf-8"
    return r
