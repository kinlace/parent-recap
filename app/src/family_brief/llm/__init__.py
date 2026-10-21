"""The AI backend that writes the Brief (ADR 0023): one interface, Backend, with an adapter per
provider, Claude, Codex and the maintainers' gateway, and an in-memory one for tests. The rest of
the program asks the Household's backend, never a provider by name. A Household uses exactly one
and never falls back to another (ADR 0017)."""
from __future__ import annotations

from pathlib import Path

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


def named(name: str, codex_path: str | None = None) -> Backend:
    """The backend `name`, as llm.backend says it, for setup, which asks of the one the family
    picks before the config names it. `codex_path` is the config's llm.codex_path, if it has one."""
    return for_llm(LLMConfig(backend=name, codex_path=codex_path))


def program_dirs() -> list[str]:
    """The folders the evening job's PATH must have for a backend's program: where the one found
    by name is, and the folder Claude Code's native installer puts claude in, which the shell
    installing the job may not have on PATH yet. Codex runs by its full path."""
    found = claude.find()
    dirs = [str(Path(found).resolve().parent), str(Path(found).parent)] if found else []
    return [*dirs, str(claude.native_dir())]
