"""The maintainers' gateway, called with the Household's Gateway Key (ADR 0014)."""
from __future__ import annotations

import requests

from .. import gateway as api
from ..config import LLMConfig
from . import parse, retry
from .backend import Finding, LLMReply


def _busy(response: requests.Response) -> str | None:
    """What says the model behind the gateway is busy, from the reply's HTTP status, or None. A
    busy status whose error waiting won't fix, such as the gateway's own credit or the key's
    budget for the day run out, isn't."""
    if response.status_code not in api.BUSY or api.out_of_budget(response):
        return None
    said = api.said(response)
    return None if retry.WONT_PASS.search(said) else said[:120]


class Gateway:
    name = "gateway"
    # The gateway writes with Claude behind it, so the Brief names Claude as it always has.
    display_name = "Claude"

    def __init__(self, llm: LLMConfig) -> None:
        self._url = llm.gateway_url

    @property
    def probe_host(self) -> str:
        return api.host(self._url)

    def program(self) -> str | None:
        return None  # its own HTTP call: nothing to install

    def install_hint(self) -> str:
        return f"ask the Parent Recap maintainers for a Gateway Key and store it with {api.SETUP}"

    def run(self, prompt: str, system_prompt: str, timeout: int, budget: int) -> LLMReply:
        # Its own HTTP call with the Gateway Key (ADR 0014): no CLI, so nothing of the Mac's own
        # Claude Code or Codex setup reaches it, and the key is only ever in the request's header.
        key = api.key()
        url = self._url
        what = f"gateway {api.host(url)} (prompt: {len(prompt.encode('utf-8'))} B)"
        # An error that echoes the key back goes no further than here, to the log or the Brief.
        response, waited = retry.with_retry(
            lambda: api.post(url, key, system_prompt, prompt, timeout),
            lambda r: (_busy(r) or "").replace(key, "<key>") or None, timeout, what, budget)
        try:
            reply = api.read(response)
        except api.GatewayError as e:
            raise api.GatewayError(str(e).replace(key, "<key>"), e.status) from None
        data, repaired = parse.model_json(reply.text)
        return LLMReply(data, reply.text, reply.usage, repaired, waited, reply.model)

    def check(self) -> list[Finding]:
        """Whether the gateway answers at its configured address, and whether the Gateway Key
        works, from one small call. Without a readable key the call goes with none, which a
        gateway that answers refuses."""
        url = self._url
        try:
            key, missing = api.key(), None
        except api.NoKey as e:
            key, missing = "", str(e)
        outcome, detail = api.test_call(url, key, timeout=120)
        if outcome == api.NO_ANSWER:
            found = [Finding("fail", "gateway", f"no answer from {url}: {detail} (check the Mac is "
                             "online and llm.gateway_url in the config)")]
        else:
            found = [Finding("ok", "gateway", f"answers at {url}")]
        if missing:
            found.append(Finding("fail", "gateway-key", f"{missing}, so the evening Brief can't call the gateway"))
        elif outcome == api.WORKS:
            found.append(Finding("ok", "gateway-key", "works, test call succeeded"))
        elif outcome == api.UNKNOWN:
            found.append(Finding("fail", "gateway-key", "the gateway doesn't know this Gateway Key, "
                                 "or it was revoked (ask the Parent Recap maintainers for a new one "
                                 f"and store it with {api.SETUP})"))
        elif outcome == api.SPENT:
            found.append(Finding("warn", "gateway-key", "works, but has used up today's budget, so "
                                 "the gateway takes no more calls with it until tomorrow"))
        elif outcome == api.FAILED:
            found.append(Finding("fail", "gateway-key", f"test call failed: {detail}"))
        return found
