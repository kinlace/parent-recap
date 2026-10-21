"""The AI backend that writes the Brief (ADR 0023): one interface, Backend, with an adapter per
provider, Claude, Codex and the maintainers' gateway, and an in-memory one for tests. The rest of
the program asks the Household's backend, never a provider by name. A Household uses exactly one
and never falls back to another (ADR 0017)."""
from __future__ import annotations

from ..config import Config, LLMConfig
from . import claude, codex, gateway
from .backend import Backend, Finding, LLMReply  # noqa: F401  the package's interface
from .retry import BUSY_PAUSES, CALL_BUDGET  # noqa: F401

_ADAPTERS = {"claude": claude.Claude, "codex": codex.Codex, "gateway": gateway.Gateway}


def for_llm(llm: LLMConfig) -> Backend:
    """The backend the config's `llm` section names."""
    return _ADAPTERS[llm.backend](llm)


def backend(cfg: Config) -> Backend:
    """The Household's backend."""
    return for_llm(cfg.llm)
