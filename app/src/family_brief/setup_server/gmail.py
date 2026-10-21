"""Gmail's connect step: the address and the App Password, which make the address the setup
parent's, first among the Recipients (ADR 0007)."""
from __future__ import annotations

import re
from http import HTTPStatus
from typing import Any

from .. import setup_gmail
from ..setup_connect import Reply, Strict, parse
from .context import Action, Context, Phase, keychain_code

# What connecting Gmail on the page can say: the chat's `setup gmail` results, `no-address` for
# an address that isn't one, and `app-passwords-unavailable`, which the page's own button gives.
GMAIL_RESULTS = ("saved", "no-address", "not-an-app-password", "rejected", "no-connection",
                 "keychain-not-reachable", "keychain-failed", "app-passwords-unavailable")
ADDRESS = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class GmailSignIn(Strict):
    address: str
    password: str  # the App Password


class Gmail(Phase):
    def routes(self) -> dict[str, Action]:
        return {"api/gmail": self.connect_gmail}

    def state(self, progress: dict[str, Any]) -> dict[str, Any]:
        return {"gmail": {"address": self.context.gmail_address()}}

    def connect_gmail(self, raw: Any) -> Reply:
        """Tests the App Password pasted into the page's own field with Gmail and stores it in
        the Keychain, through the chat's Gmail step (ADR 0007). Once it's saved, Gmail is done,
        the address is the Household's Gmail and the setup parent's, first among the Recipients.
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

    def _answers(self, address: str, progress: dict[str, Any]) -> dict[str, Any]:
        """Gmail's address, and the setup parent first among the Recipients."""
        answers: dict[str, Any] = {"sources": {"gmail": {"address": address}}}
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
