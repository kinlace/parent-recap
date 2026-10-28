"""Gmail's connect step: the address and the App Password, which make the address the setup
parent's, first among the Recipients (ADR 0007). The step also offers "Parent Recap sends the
Brief" (ADR 0028): the Recipients' addresses, and Gmail skipped. A Household whose AI is its own
Claude Code or Codex gives a Gateway Key for sending alone with them, which the gateway as the AI
needn't."""
from __future__ import annotations

import re
from http import HTTPStatus
from typing import Any, Literal

from pydantic import Field

from .. import setup_ai, setup_gmail, setup_save
from ..setup_connect import Reply, Strict, parse
from .context import Action, Context, Phase, keychain_code

# What connecting Gmail on the page can say: the chat's `setup gmail` results, and `no-address`
# for an address that isn't one. What to do when Google offers no App Passwords is folded under the
# step itself, so the page never says `app-passwords-unavailable` (#491).
GMAIL_RESULTS = ("saved", "no-address", "not-an-app-password", "rejected", "no-connection",
                 "keychain-not-reachable", "keychain-failed")
# What "Parent Recap sends the Brief" can say: saved, an address that isn't one, or the same
# address given for two Recipients.
GATEWAY_SENDS_RESULTS = ("saved", "no-address", "same-address")
# And how testing and storing a Gateway Key for sending alone went when it didn't save, which the
# page says as the gateway AI's key field does (ai.GATEWAY_RESULTS).
SEND_KEY_RESULTS = ("not-a-key", "rejected", "no-connection", "test-call-failed",
                    "keychain-not-reachable", "keychain-failed")
ADDRESS = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class GmailSignIn(Strict):
    address: str
    password: str  # the App Password


class SentTo(Strict):
    address: str
    language: Literal["fi", "en", "zh"]


class GatewaySends(Strict):
    recipients: list[SentTo] = Field(min_length=1)  # the setup parent first
    key: str | None = None  # the Gateway Key for sending alone, unless the gateway is the AI


class Gmail(Phase):
    def routes(self) -> dict[str, Action]:
        return {"api/gmail": self.connect_gmail, "api/gmail/gateway": self.parent_recap_sends}

    def state(self, progress: dict[str, Any]) -> dict[str, Any]:
        return {"gmail": {"address": self.context.gmail_address(),
                          "sender": self.context.model().email.sender}}

    def connect_gmail(self, raw: Any) -> Reply:
        """Tests the App Password pasted into the page's own field with Gmail and stores it in
        the Keychain, through the chat's Gmail step (ADR 0007). Once it's saved, Gmail is done,
        the address is the Household's Gmail and the setup parent's, first among the Recipients,
        unless Parent Recap sends the Brief: then Gmail is only read, and the Recipients stay.
        The App Password is never returned, logged, saved anywhere else or put on a command line."""
        answer = parse(GmailSignIn, raw)
        address = answer.address.strip().lower()
        if not ADDRESS.match(address):
            return HTTPStatus.OK, {"result": "no-address"}
        result, _, extra = setup_gmail.sign_in(address, answer.password)
        if result != "saved":
            return HTTPStatus.OK, {"result": result, **keychain_code(extra)}
        return self.context.connect.complete(
            "gmail", lambda progress: self._answers(address, progress), reply={"address": address})

    def parent_recap_sends(self, raw: Any) -> Reply:
        """"Parent Recap sends the Brief" (ADR 0028): saves the Recipients, the setup parent first,
        and the gateway as what sends the Brief, and skips Gmail, which needs no App Password then.
        Gmail can still be opened from the Source list later, to have the school's mail read.
        A Household whose AI is its own Claude Code or Codex gives a Gateway Key for sending alone,
        pasted into the page's own field (ADR 0007): it's tested with the Brief relay's check,
        which sends nothing and makes no model call, and stored, as `setup gateway` does. The
        gateway as the AI has its key tested with a model call on the AI step instead. The key is
        never returned, logged or put on a command line."""
        answer = parse(GatewaySends, raw)
        if (answer.key is None) != (self.context.model().llm.backend == "gateway"):
            return HTTPStatus.BAD_REQUEST, setup_save.outcome(
                "invalid-answers", "Give a Gateway Key for sending alone, unless the gateway is "
                "the AI: the AI step takes its key.",
                errors=["key: should be given when, and only when, the AI isn't the gateway"])
        addresses = [r.address.strip().lower() for r in answer.recipients]
        if not all(ADDRESS.match(a) for a in addresses):
            return HTTPStatus.OK, {"result": "no-address"}
        if len(set(addresses)) < len(addresses):
            return HTTPStatus.OK, {"result": "same-address"}
        if answer.key is not None:
            result, extra = setup_ai.gateway_sign_in(self.context.gateway_url(), answer.key,
                                                     sends_only=True)
            if result != "saved":
                return HTTPStatus.OK, {"result": result, **keychain_code(extra)}
        household = self.context.model().summary_language
        recipients = [{"address": a, **({"language": r.language} if r.language != household else {})}
                      for a, r in zip(addresses, answer.recipients)]
        return self.context.connect.skip("gmail", {
            "sender": "gateway", "recipients": recipients,
            # Nothing of a Gmail connected before is read once it's skipped.
            "sources": {"gmail": {"allowlist_domains": [], "allowlist_senders": []}}})

    def _answers(self, address: str, progress: dict[str, Any]) -> dict[str, Any]:
        """Gmail's address, and the setup parent first among the Recipients, unless Parent Recap
        sends the Brief to the Recipients saved with that choice."""
        answers: dict[str, Any] = {"sources": {"gmail": {"address": address}}}
        if self.context.model().email.sender == "gateway":
            return answers
        if "partner" in progress:  # Welcome's choice, waiting for the parent's address
            answers["recipients"] = recipients(self.context, address, progress["partner"])
        elif not self.context.section("email").get("to"):
            answers["recipients"] = [{"address": address}]
        return answers


def recipients(context: Context, parent: str,
               partner: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The setup parent first, keeping the language saved for them, then the partner."""
    to = context.section("email").get("to")
    first = to[0] if isinstance(to, list) and to else None
    first = {"address": first} if isinstance(first, str) else first
    same = isinstance(first, dict) and first.get("address") == parent
    also = partner and partner["address"].lower() != parent.lower()  # not the parent twice
    return [first if same else {"address": parent}, *([partner] if also else [])]
