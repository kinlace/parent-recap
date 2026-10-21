"""The setup page's Connect steps: completing, skipping and opening a Source (ADR 0006).

Every call of the page's that completes, skips or reopens a Source goes through `ConnectSteps`,
which holds the page's one saving lock, merges the step into setup's progress, works out the next
Source to do, and answers a save that didn't go through with 409 Conflict. A call's answers are
read with a typed model through `parse`, so a malformed one gets the same 400 from every step.
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from http import HTTPStatus
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict, ValidationError

from . import setup_save
from . import sources as source_registry
from .system import macos_only

Reply = tuple[HTTPStatus, dict[str, Any]]
Progress = dict[str, Any]
# What a step saves besides its progress, or how to tell from the progress read under the lock.
Answers = dict[str, Any] | Callable[[Progress], dict[str, Any]]
T = TypeVar("T", bound=BaseModel)
# What skipping a Source also saves: a Household that skips Wilma doesn't use it.
SKIP_ANSWERS: dict[str, dict[str, Any]] = {
    source_registry.WILMA.name: {"sources": {source_registry.WILMA.name: {"enabled": False}}},
}


class Strict(BaseModel):
    """A call's answers: exactly the keys it names, each of its type."""
    model_config = ConfigDict(extra="forbid")


class Nothing(Strict):
    """The answers of a call that takes none: `{}`."""


class Malformed(Exception):
    """A call's answers aren't what it takes: the page's server answers 400 with `answer`."""

    def __init__(self, answer: dict[str, Any]) -> None:
        super().__init__(answer["result"])
        self.answer = answer


def parse(model: type[T], raw: Any) -> T:
    """The answers `raw`, as parsed from JSON, read as `model`, or Malformed. Only where each
    problem is, never the value, which can be a secret."""
    if not isinstance(raw, dict):
        raise Malformed(setup_save.outcome(
            "invalid-answers", "Nothing was saved. Fix the answers named in errors and save again.",
            errors=["answers: should be an object"]))
    try:
        return model.model_validate(raw)
    except ValidationError as e:
        raise Malformed(setup_save.invalid(e)) from None


def sources() -> list[str]:
    """The Sources this system offers, in Connect's list's order."""
    return [s for s in setup_save.SOURCES if macos_only.offers(s)]


def next_source(statuses: dict[str, str]) -> str | None:
    """The first Source in the list still to do, or None once each is done or skipped."""
    return next((s for s in sources() if statuses.get(s) == "to-do"), None)


def skipped(statuses: dict[str, str]) -> bool:
    """Whether a Source this system offers was skipped, so it can be added from Connect's list."""
    return any(statuses.get(s) == "skipped" for s in sources())


class ConnectSteps:
    """Setup's progress through Connect, for the config at `config`. `lock` is held for every
    save, so that one step's read and write of the progress never interleave with another's."""

    def __init__(self, config: Path) -> None:
        self.config = config
        self.lock = threading.RLock()  # one save at a time

    def progress(self) -> Progress:
        return setup_save.read(self.config)["progress"]

    def save(self, answers: dict[str, Any]) -> Reply:
        """Saves `answers` through `setup save`: 200 with what it says, or 409 if not saved."""
        with self.lock:
            out = setup_save.save(self.config, answers)
        return (HTTPStatus.OK if out["result"] == "saved" else HTTPStatus.CONFLICT), out

    def complete(self, name: str, answers: Answers | None = None, *,
                 move_on: bool | Callable[[Progress], bool] = True, keep_skipped: bool = False,
                 reply: dict[str, Any] | None = None) -> Reply:
        """Saves the Source `name` as done with `answers`, and moves setup on to the next Source
        unless `move_on` says otherwise. With `keep_skipped`, a Source skipped meanwhile stays
        skipped and nothing is saved: the family's choice stands. `reply` is added to the answer."""
        with self.lock:
            progress = self.progress()
            if keep_skipped and progress["sources"][name] == "skipped":
                return HTTPStatus.OK, {"progress": progress, **(reply or {})}
            on = move_on(progress) if callable(move_on) else move_on
            statuses = {**progress["sources"], name: "done"}
            status, out = self._step(progress, answers, {name: "done"},
                                     {"source": next_source(statuses)} if on else {})
        return status, ({**out, **(reply or {})} if status == HTTPStatus.OK else out)

    def move_on(self, name: str, refusal: dict[str, Any]) -> Reply:
        """Moves setup on from the Source `name` once it's done, or refuses with `refusal`."""
        with self.lock:
            statuses = self.progress()["sources"]
            if statuses[name] != "done":
                return HTTPStatus.BAD_REQUEST, refusal
            return self.save({"progress": {"source": next_source(statuses)}})

    def skip(self, name: str, answers: dict[str, Any] | None = None) -> Reply:
        """Saves the Source `name` as skipped with `answers`, and moves setup on to the next one."""
        with self.lock:
            progress = self.progress()
            statuses = {**progress["sources"], name: "skipped"}
            return self._step(progress, {**SKIP_ANSWERS.get(name, {}), **(answers or {})},
                              {name: "skipped"}, {"source": next_source(statuses)})

    def open(self, name: str) -> Reply:
        """Opens the Source `name` from the list, which brings a skipped one back."""
        with self.lock:
            progress = self.progress()
            status = "to-do" if progress["sources"][name] == "skipped" else progress["sources"][name]
            return self._step(progress, None, {name: status}, {"source": name})

    def reopen(self, source: str | None, allowed: Callable[[Progress], bool],
               refusal: dict[str, Any]) -> Reply:
        """Takes setup from Check or Finish back to Connect at `source`, or at its list for None,
        when `allowed` says the progress allows it, or refuses with `refusal`."""
        with self.lock:
            progress = self.progress()
            if progress["phase"] not in ("check", "finish") or not allowed(progress):
                return HTTPStatus.BAD_REQUEST, refusal
            return self.save({"progress": {"phase": "connect", "source": source}})

    def _step(self, progress: Progress, answers: Answers | None, statuses: dict[str, str],
              source: dict[str, Any]) -> Reply:
        given = (answers(progress) if callable(answers) else answers) or {}
        return self.save({**given, "progress": {**given.get("progress", {}),
                                                "sources": statuses, **source}})
