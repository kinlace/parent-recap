"""Gmail's connect step, in two parts. First who gets the Brief: the setup parent's address, then
anyone else's, each with their language. Parent Recap sends every Brief from its own address
through the maintainers' gateway (ADR 0032), so a Household whose AI is its own Claude Code or
Codex needs a Gateway Key for sending: once the Recipients are saved, the page asks the gateway for
a send-only one bound to them, which mails each a link to confirm (ADR 0033), and `api/gmail/confirm`
sends it again. A key pasted with the Recipients is used instead; the gateway as the AI has its key
on its own step. Then, only if the family wants the school's mail read, the Gmail address and its
App Password (ADR 0007); Gmail can be skipped once the Recipients are saved. Check and Finish say
who hasn't confirmed, from doctor, and still take a pasted key with `api/gmail/key`."""
from __future__ import annotations

import re
from http import HTTPStatus
from typing import Any, Literal

from pydantic import Field

from .. import send_key, setup_ai, setup_gmail, setup_save
from ..setup_connect import Reply, Strict, parse
from .ai import GATEWAY_RESULTS
from .context import Action, Context, Phase, keychain_code

# What connecting Gmail on the page can say: the chat's `setup gmail` results, and `no-address`
# for an address that isn't one. What to do when Google offers no App Passwords is folded under the
# step itself, so the page never says `app-passwords-unavailable` (#491).
GMAIL_RESULTS = ("saved", "no-address", "not-an-app-password", "rejected", "no-connection",
                 "keychain-not-reachable", "keychain-failed")
# What saving who gets the Brief can say: saved, an address that isn't one, or the same address
# given for two Recipients.
RECIPIENTS_RESULTS = ("saved", "no-address", "same-address")
# And how testing and storing a Gateway Key for sending didn't go, which the page says as the
# gateway AI's key field does.
SEND_KEY_RESULTS = tuple(r for r in GATEWAY_RESULTS if r != "saved")
ADDRESS = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class GmailSignIn(Strict):
    address: str
    password: str  # the App Password


class SentTo(Strict):
    address: str
    language: Literal["fi", "en", "zh"]


class BriefTo(Strict):
    recipients: list[SentTo] = Field(min_length=1)  # the setup parent first
    key: str | None = None  # a pasted Gateway Key for sending, never when the gateway is the AI


class Resend(Strict):
    resend: bool = False


class SendKey(Strict):
    key: str


class Gmail(Phase):
    def routes(self) -> dict[str, Action]:
        return {"api/gmail": self.connect_gmail, "api/gmail/recipients": self.save_recipients,
                "api/gmail/key": self.save_send_key, "api/gmail/confirm": self.confirm}

    def state(self, progress: dict[str, Any]) -> dict[str, Any]:
        cfg = self.context.model()
        return {"gmail": {"address": self.context.gmail_address(),
                          "to": [{"address": r.address, "language": cfg.language_of(r)}
                                 for r in cfg.email.to]}}

    def connect_gmail(self, raw: Any) -> Reply:
        """Tests the App Password pasted into the page's own field with Gmail and stores it in
        the Keychain, through the chat's Gmail step (ADR 0007). Once it's saved, Gmail is done and
        the address is the Household's Gmail, which is only read: the Recipients stay as saved.
        The App Password is never returned, logged, saved anywhere else or put on a command line."""
        answer = parse(GmailSignIn, raw)
        address = answer.address.strip().lower()
        if not ADDRESS.match(address):
            return HTTPStatus.OK, {"result": "no-address"}
        result, _, extra = setup_gmail.sign_in(address, answer.password)
        if result != "saved":
            return HTTPStatus.OK, {"result": result, **keychain_code(extra)}
        return self.context.connect.complete(
            "gmail", {"sources": {"gmail": {"address": address}}}, reply={"address": address})

    def save_recipients(self, raw: Any) -> Reply:
        """Saves who gets the Brief, the setup parent first, and leaves Gmail to connect or skip.
        A Household whose AI is its own Claude Code or Codex then gets a send-only Gateway Key
        bound to them, asked of the gateway when none is stored, and the gateway mails each a
        link to confirm (ADR 0033): `confirm` says to whom. It may paste a key instead, into the
        page's own field (ADR 0007): that one is tested with the Brief relay's check, which sends
        nothing and makes no model call, and stored, as `setup gateway` does. The gateway as the
        AI has its key tested with a model call on the AI step instead. The key is never
        returned, logged or put on a command line."""
        answer = parse(BriefTo, raw)
        if answer.key is not None and self.context.model().llm.backend == "gateway":
            return HTTPStatus.BAD_REQUEST, _key_refused()
        addresses = [r.address.strip().lower() for r in answer.recipients]
        if not all(ADDRESS.match(a) for a in addresses):
            return HTTPStatus.OK, {"result": "no-address"}
        if len(set(addresses)) < len(addresses):
            return HTTPStatus.OK, {"result": "same-address"}
        if answer.key is not None:
            result, extra = self._send_key(answer.key)
            if result != "saved":
                return HTTPStatus.OK, {"result": result, **extra}
        household = self.context.model().summary_language
        recipients = [{"address": a, **({"language": r.language} if r.language != household else {})}
                      for a, r in zip(addresses, answer.recipients)]
        with self.context.connect.lock:
            out = setup_save.save(self.context.config, {"recipients": recipients})
        if out["result"] != "saved":
            return HTTPStatus.CONFLICT, out
        cfg = self.context.model()
        if send_key.may_request(cfg):
            out = {**out, "confirm": _confirmation(send_key.ensure(cfg))}
        return HTTPStatus.OK, out

    def confirm(self, raw: Any) -> Reply:
        """Who of the Recipients hasn't confirmed yet, with `resend` mailing each of them the
        link again; asks the gateway for a key first when none is stored."""
        answer = parse(Resend, raw)
        cfg = self.context.model()
        if not send_key.may_request(cfg):
            return HTTPStatus.BAD_REQUEST, _key_refused()
        return HTTPStatus.OK, _confirmation(send_key.ensure(cfg, resend=answer.resend))

    def save_send_key(self, raw: Any) -> Reply:
        """A Gateway Key for sending alone, for a Household whose AI is its own Claude Code or Codex
        and which has none yet, such as one that sent through its own Gmail before ADR 0032: Check
        and Finish take it when the Brief sending check says it's missing. Tested and stored as on
        Gmail's step; nothing else is saved."""
        answer = parse(SendKey, raw)
        if self.context.model().llm.backend == "gateway":
            return HTTPStatus.BAD_REQUEST, _key_refused()
        result, extra = self._send_key(answer.key)
        return HTTPStatus.OK, {"result": result, **extra}

    def _send_key(self, key: str) -> tuple[str, dict[str, Any]]:
        result, extra = setup_ai.gateway_sign_in(self.context.gateway_url(), key, sends_only=True)
        return result, keychain_code(extra)


def _key_refused() -> dict[str, Any]:
    return setup_save.outcome(
        "invalid-answers", "The gateway is the AI, so its key is the AI step's, and it is never "
        "asked for here.", errors=["key: should not be given when the AI is the gateway"])


def _confirmation(status: send_key.Status) -> dict[str, Any]:
    """A send_key.Status as the page shows it: how it went and the addresses still to confirm and
    just mailed. Its error stays out: the page says each result in its own words."""
    return {"result": status.result, "unconfirmed": status.unconfirmed, "mailed": status.mailed}


def recipients(context: Context, parent: str,
               partner: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The setup parent first, keeping the language saved for them, then the partner."""
    to = context.section("email").get("to")
    first = to[0] if isinstance(to, list) and to else None
    first = {"address": first} if isinstance(first, str) else first
    same = isinstance(first, dict) and first.get("address") == parent
    also = partner and partner["address"].lower() != parent.lower()  # not the parent twice
    return [first if same else {"address": parent}, *([partner] if also else [])]
