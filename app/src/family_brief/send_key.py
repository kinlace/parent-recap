"""The send-only Gateway Key a Household gets on request (ADR 0033): every Brief is sent through
the gateway (ADR 0032), and a Household whose AI is its own Claude Code or Codex needs a key only
for that. With none stored, the program asks the gateway for one bound to the Recipients, stores
it as a pasted key is stored, and the gateway mails each Recipient a link to confirm. With one, it
binds the Recipients as they are now, so a changed one gets its own link. The gateway sends only
to Recipients who confirmed. Keys that write the Brief stay handed out by the maintainers, so a
Household with the gateway as its AI is never given one here.

The setup page, `doctor` (and through it the page's Check and Finish), `setup confirm` and the
evening run all go through `ensure`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from . import gateway, install_record
from .system import secrets

if TYPE_CHECKING:
    from .config import Config

# How it went: everyone confirmed; someone hasn't yet; there's no key and none could be had
# (`no-key`); or the gateway couldn't be asked (`failed`).
CONFIRMED, UNCONFIRMED, NO_KEY, FAILED = "confirmed", "unconfirmed", "no-key", "failed"


@dataclass
class Status:
    result: str
    recipients: list[str] = field(default_factory=list)  # every address bound, the parent's first
    unconfirmed: list[str] = field(default_factory=list)
    mailed: list[str] = field(default_factory=list)      # just sent a confirmation link
    requested: bool = False                              # the key was just made
    error: str | None = None
    again: bool = False                                  # none could be had: asked again later

    @property
    def anyone(self) -> bool:
        """Whether the Brief reaches anyone: someone confirmed."""
        return len(self.unconfirmed) < len(self.recipients)


def recipients(cfg: Config) -> list[dict[str, str]]:
    """The Brief's and Weekend Picks' Recipients with their languages, the setup parent first,
    each once."""
    out: dict[str, dict[str, str]] = {}
    for r in [*cfg.email.to, *cfg.weekend_recipients()]:
        out.setdefault(r.address.lower(), {"address": r.address.lower(),
                                           "language": cfg.language_of(r)})
    return list(out.values())


def may_request(cfg: Config) -> bool:
    """Whether a missing key may be asked for: not when the gateway writes the Brief, whose key
    the maintainers hand out (ADR 0014)."""
    return cfg.llm.backend != "gateway"


def ensure(cfg: Config, resend: bool = False) -> Status:
    """Makes sure the Household has a Gateway Key bound to its Recipients, asking the gateway for
    one when none is stored, and says who still has to confirm. With `resend`, each of them is
    mailed the link again."""
    to = recipients(cfg)
    addresses = [r["address"] for r in to]
    if not to:
        return Status(FAILED, error="nobody gets the Brief yet (email.to is empty)")
    url = cfg.llm.gateway_url
    try:
        key = gateway.key()
    except gateway.NoKey as e:
        if not e.missing or not may_request(cfg):
            return Status(NO_KEY, addresses, error=str(e))
        return _request(url, to)
    try:
        got = gateway.bind(url, key, to, resend)
    except (gateway.GatewayError, TimeoutError) as e:
        return Status(FAILED, addresses, error=f"couldn't ask the gateway who confirmed: {e}")
    return _status(addresses, got)


def _request(url: str, to: list[dict[str, str]]) -> Status:
    addresses = [r["address"] for r in to]
    try:
        got = gateway.request_send_key(url, to)
    except (gateway.GatewayError, TimeoutError) as e:
        return Status(NO_KEY, addresses, error=f"no Gateway Key in the {secrets.STORE}, and the "
                      f"gateway couldn't make one just now: {e}", again=True)
    try:
        secrets.put(gateway.ACCOUNT, got.key or "")
    except secrets.SecretsError as e:
        return Status(NO_KEY, addresses, error=f"the gateway made a Gateway Key, but the "
                      f"{secrets.STORE} didn't store it: {e}", again=True)
    install_record.add("keychain", gateway.ACCOUNT)
    status = _status(addresses, got)
    status.requested = True
    return status


def _status(addresses: list[str], got: gateway.Confirmation) -> Status:
    unconfirmed = [a for a in addresses if a in got.unconfirmed]
    return Status(UNCONFIRMED if unconfirmed else CONFIRMED, addresses, unconfirmed, got.mailed)


def describe(status: Status) -> str:
    """What doctor and the evening run's log say of a Status that isn't CONFIRMED."""
    if status.result in (NO_KEY, FAILED):
        # A key the gateway couldn't make, or that wasn't stored, is asked for again each time.
        again = (" (Parent Recap asks again at the next evening run; `parent-recap setup confirm` "
                 "asks now)") if status.again else ""
        return f"{status.error or status.result}{again}"
    asked = "Parent Recap asked the gateway for a Gateway Key, and " if status.requested else ""
    waiting = ", ".join(status.unconfirmed)
    sent = (f"{asked}a confirmation email was sent to {', '.join(status.mailed)}"
            if status.mailed else f"{asked}a confirmation email was sent before")
    return (f"{waiting} {'has' if len(status.unconfirmed) == 1 else 'have'} not confirmed yet, "
            f"so the gateway won't send the Brief there: {sent} from brief@parentrecap.com. "
            "Press the link in it (look in Junk too); `parent-recap setup confirm --resend` "
            "sends it again")
