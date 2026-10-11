"""The maintainers' gateway, which writes the Brief for a Household with a Gateway Key and no
Claude or ChatGPT subscription (ADR 0014).

The program calls the gateway's Anthropic-format `POST /v1/messages` itself, so the Household
needs neither Claude Code nor Codex. The Gateway Key lives in the Keychain and goes only into the
request's `x-api-key` header: it's never printed, logged or put on a command line (ADR 0005).
What a reply means is read from its HTTP status: 401 an unknown or revoked key, 402 (or any error
that says `Budget exceeded`) a key past its daily budget, 429 and 5xx a busy model, and a 200
whose `stop_reason` is `refusal` a failed call, never an empty Brief.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import requests

from .utils import keychain

ACCOUNT = "gateway-key"
# What the gateway maps to its real model, so changing model is a gateway config change.
MODEL = "parent-recap"
ANTHROPIC_VERSION = "2023-06-01"
# A cap on the reply, well above a busy night's Brief: the Household pays only for what it uses.
MAX_TOKENS = 16000
SETUP = "parent-recap setup gateway"
# The statuses waiting may fix: rate-limited, and the gateway or the model behind it busy.
BUSY = frozenset({429, 500, 502, 503, 504, 529})
UNKNOWN_KEY, OUT_OF_BUDGET = 401, 402
# How a call refused for a key past its daily budget starts, so the Brief can say so. The budget
# resets a day after it last did (ops/gateway/README.md).
SPENT_MESSAGE = "the Gateway Key has used up today's credit"

# The gateway's usage fields, by the kind of token LLMReply.usage calls them.
_USAGE = {"input": "input_tokens", "cache_write": "cache_creation_input_tokens",
          "cached_input": "cache_read_input_tokens", "output": "output_tokens"}


class GatewayError(RuntimeError):
    """A call the gateway didn't answer with a reply: `status` is its HTTP status, None when it
    didn't answer at all."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class NoKey(RuntimeError):
    """No Gateway Key the evening run can read in the Keychain."""


@dataclass
class Reply:
    text: str
    usage: dict[str, int] | None
    model: str | None


def host(url: str) -> str:
    return urlparse(url).hostname or url


def key() -> str:
    """The stored Gateway Key. Raises NoKey when there is none, or one macOS asks about first."""
    try:
        stored = (keychain.get(ACCOUNT) or "").strip()
    except keychain.KeychainError:
        raise NoKey("the Gateway Key is in the Keychain, but macOS asks for the Keychain password "
                    f"before reading it, which the evening Brief can't answer (store it again with "
                    f"{SETUP})") from None
    if not stored:
        raise NoKey(f"no Gateway Key in the Keychain (store one with {SETUP})")
    return stored


def post(url: str, gateway_key: str, system: str, prompt: str, timeout: float,
         max_tokens: int = MAX_TOKENS) -> requests.Response:
    """One call to the gateway at `url`. A call that times out raises TimeoutError, one that
    reaches no gateway GatewayError, so neither says more than the address. It always asks for
    MODEL: a Gateway Key may use no other, so an llm.model left from another backend is ignored."""
    body = {"model": MODEL, "max_tokens": max_tokens, "system": system,
            "messages": [{"role": "user", "content": prompt}]}
    try:
        return requests.post(f"{url.rstrip('/')}/v1/messages", json=body, timeout=timeout,
                             headers={"x-api-key": gateway_key,
                                      "anthropic-version": ANTHROPIC_VERSION})
    except requests.Timeout:
        raise TimeoutError(f"the gateway at {host(url)} didn't answer within {timeout:g}s") from None
    except requests.RequestException as e:
        raise GatewayError(f"couldn't reach the gateway at {host(url)} "
                           f"({e.__class__.__name__})") from None


def said(response: requests.Response) -> str:
    """The error the gateway gave, as its body says it, else its HTTP status."""
    try:
        error = response.json().get("error") or {}
        message = error.get("message") if isinstance(error, dict) else str(error)
    except (ValueError, AttributeError):
        message = None
    return f"HTTP {response.status_code}: {(message or response.text or '').strip()[:300]}"


def out_of_budget(response: requests.Response) -> bool:
    """Whether the gateway refused the call because the key has used today's budget. Bifrost says
    so with a 402 and `Budget exceeded: …`; the words count with any error status, never in a
    reply, which may quote a message that says them."""
    return response.status_code == OUT_OF_BUDGET or (
        response.status_code != 200 and "budget exceeded" in said(response).lower())


def spent(error: str) -> bool:
    """Whether a failed call's error is the key's budget run out for the day."""
    return error.startswith(SPENT_MESSAGE)


def read(response: requests.Response) -> Reply:
    """The reply's text, tokens by kind and model. Raises GatewayError for any other answer: an
    error status, a refusal, or a reply with no text."""
    if out_of_budget(response):
        raise GatewayError(f"{SPENT_MESSAGE} (the gateway answered {said(response)})", response.status_code)
    if response.status_code != 200:
        raise GatewayError(f"the gateway answered {said(response)}", response.status_code)
    try:
        data: dict[str, Any] = response.json()
    except ValueError:
        raise GatewayError("the gateway's reply isn't JSON", response.status_code) from None
    if data.get("stop_reason") == "refusal":
        raise GatewayError("the model refused to answer", response.status_code)
    text = "".join(b.get("text") or "" for b in data.get("content") or []
                   if isinstance(b, dict) and b.get("type") == "text")
    if not text.strip():
        raise GatewayError(f"the gateway's reply has no text (stop_reason: {data.get('stop_reason')})",
                           response.status_code)
    usage = data.get("usage") or {}
    return Reply(text, {kind: usage.get(k) or 0 for kind, k in _USAGE.items()} if usage else None,
                 data.get("model"))


# What a test call found: the gateway answered and the key works, the gateway answered but
# refused the key or the key has used today's budget, the gateway didn't answer, or another failure.
WORKS, UNKNOWN, SPENT, NO_ANSWER, FAILED = "works", "unknown-key", "spent", "no-answer", "failed"


def test_call(url: str, gateway_key: str, timeout: float = 60) -> tuple[str, str | None]:
    """One small call, as doctor and setup check the gateway and the key. Returns one of WORKS,
    UNKNOWN, SPENT, NO_ANSWER and FAILED, and for all but WORKS what went wrong, with the key
    taken out."""
    try:
        response = post(url, gateway_key, "This is a connectivity test.",
                        "Reply with just the two letters OK", timeout, max_tokens=16)
        read(response)
    except (TimeoutError, GatewayError) as e:
        status = getattr(e, "status", None)
        outcome = (NO_ANSWER if status is None else SPENT if spent(str(e))
                   else UNKNOWN if status == UNKNOWN_KEY else FAILED)
        detail = str(e).replace(gateway_key, "<key>") if gateway_key else str(e)
        return outcome, detail
    return WORKS, None
