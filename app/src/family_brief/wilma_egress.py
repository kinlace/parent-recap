"""The hosted version's way out to Wilma in Finland (ADR 0046).

The Helsinki and Vantaa Wilmas turn away requests from outside Finland, so the server in Stockholm
sends its wilma CLI calls through an HTTP CONNECT proxy in Finland when one is set: the address in
SETTINGS, which gateway-setup.sh writes (ops/gateway/README.md "The Finnish egress for Wilma").
Only the wilma CLI's environment gets the proxy (`own_node.wilma_env`), so no other Source's
traffic and no model call goes that way. A family's own computer has no such file, so nothing
changes there.

Before the first call, and again once the last answer is FRESH_SECONDS old, it checks that the
proxy's port answers. When it doesn't, the CLI goes direct, which still works for a Wilma without
the check (Espoo), and a refusal from one with it is put down to the proxy (`explain`): the Brief
then says only that Wilma couldn't be reached, and `signup health` names the cause (`causes`) for
the maintainers.
"""
from __future__ import annotations

import logging
import re
import socket
import time
from pathlib import Path
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

SETTINGS = Path("/etc/parent-recap/wilma-egress.env")
SETTING = "WILMA_EGRESS_PROXY"
ADDRESS = re.compile(r"http://[A-Za-z0-9.-]+:[0-9]{1,5}/?")  # as gateway-setup.sh checks it
PROBE_SECONDS = 3.0
FRESH_SECONDS = 120.0
# Starts the reason of a Wilma error put down to the proxy not answering, which the Brief says in
# its own neutral words (render.py) and doctor names.
UNREACHABLE = "Finnish egress unreachable"
# The cause `signup health` gives the maintainers for it, and what `signup status` prints for it.
CAUSE = "wilma-egress"

_answered: dict[str, tuple[float, bool]] = {}  # proxy -> when it was last tried, and whether it answered
_blamed = False  # whether an error was put down to the proxy in this process


def proxy(settings: Path | None = None) -> str | None:
    """The proxy's address, http://HOST:PORT, or None when none is set or it isn't one."""
    try:
        lines = (settings or SETTINGS).read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError):
        return None
    for line in lines:
        name, sep, value = line.partition("=")
        if sep and name.strip() == SETTING:
            value = value.strip().strip("\"'")
            return value if _valid(value) else None
    return None


def _valid(address: str) -> bool:
    """http://HOST:PORT, with a name or IPv4 address for HOST, as gateway-setup.sh and
    `admin check` take it."""
    return ADDRESS.fullmatch(address) is not None


def env() -> dict[str, str]:
    """What the wilma CLI's environment gets: Node's own proxy settings (Node 24 follows
    HTTPS_PROXY with NODE_USE_ENV_PROXY) when a proxy is set and answers, else nothing."""
    address = proxy()
    if address is None or not _answers(address):
        return {}
    return {"NODE_USE_ENV_PROXY": "1", "HTTPS_PROXY": address}


def _answers(address: str) -> bool:
    now = time.monotonic()
    tried = _answered.get(address)
    if tried is not None and now - tried[0] < FRESH_SECONDS:
        return tried[1]
    parts = urlsplit(address)
    try:
        socket.create_connection((parts.hostname, parts.port), timeout=PROBE_SECONDS).close()
        answers = True
    except OSError as e:
        answers = False
        log.warning("The Finnish egress for Wilma didn't answer (%s): the wilma CLI goes direct "
                    "for now (ADR 0046)", type(e).__name__)
    _answered[address] = (now, answers)
    return answers


def went_direct() -> bool:
    """Whether a proxy is set and didn't answer when last tried, so the CLI went direct."""
    address = proxy()
    return address is not None and address in _answered and not _answered[address][1]


def blocked(message: str, code: str | None) -> bool:
    """Whether a wilma CLI error is Wilma turning the request away before the password check: the
    CLI's login_blocked (2.5.1 on), or the 403 at the login token step before it."""
    return code == "login_blocked" or "HTTP 403" in message


def explain(message: str, code: str | None) -> str:
    """`message`, the reason of a wilma CLI error, put down to the proxy when it didn't answer and
    Wilma turned the request away. Then this process names the cause (`causes`)."""
    global _blamed
    if not (went_direct() and blocked(message, code)):
        return message
    _blamed = True
    return f"{UNREACHABLE}: {message}"


def causes() -> list[str]:
    """The causes this process found for what it couldn't read, for `signup health`."""
    return [CAUSE] if _blamed else []


def reset() -> None:
    """Forgets what this process found, as a new one starts."""
    global _blamed
    _answered.clear()
    _blamed = False
