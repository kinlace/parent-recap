"""The golden files' check, which `--update-goldens[=PATTERN]` switches to rewriting the goldens
whose names match, so one night's files can be regenerated without touching the rest. Each file
rewritten gets one line saying what changed, which conftest.py prints at the end of the run."""
from __future__ import annotations

import difflib
from fnmatch import fnmatchcase
from pathlib import Path


class Goldens:
    def __init__(self, directory: Path, pattern: str | None) -> None:
        self.directory = directory
        self.pattern = pattern  # None compares; a pattern rewrites the goldens it matches
        self._matched: set[str] = set()
        # Each golden rewritten: as it was before the run, or None if new, and as it is now.
        self._rewritten: dict[str, tuple[str | None, str]] = {}

    def matches(self, name: str) -> bool:
        """A glob, or a name up to a dot: `normal_night` is every `normal_night.*`."""
        return self.pattern is not None and (fnmatchcase(name, self.pattern)
                                             or fnmatchcase(name, self.pattern + ".*"))

    def check(self, name: str, actual: str) -> None:
        path = self.directory / name
        old = path.read_text() if path.exists() else None
        if self.matches(name):
            self._matched.add(name)
            if actual != old:
                before = self._rewritten.get(name, (old, ""))[0]
                self._rewritten[name] = (before, actual)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(actual)
            return
        assert old is not None, f"missing golden {name}; run: pytest --update-goldens={name}"
        assert actual == old, f"{name} differs; if intended, run: pytest --update-goldens={name}"

    def summary(self) -> list[str]:
        """One line per golden rewritten, or why none was: a pattern with a typo matches none."""
        if not self._matched:
            return [f"none: no golden checked in this run matches {self.pattern}"]
        lines = []
        for name, (before, after) in sorted(self._rewritten.items()):
            if before is None:
                lines.append(f"{name}: new, {len(after.splitlines())} lines")
            elif before != after:  # a later test may have put it back
                ops = difflib.SequenceMatcher(None, before.splitlines(), after.splitlines(),
                                              autojunk=False).get_opcodes()
                added = sum(j2 - j1 for op, _i1, _i2, j1, j2 in ops if op != "equal")
                removed = sum(i2 - i1 for op, i1, i2, _j1, _j2 in ops if op != "equal")
                lines.append(f"{name}: +{added} -{removed} lines")
        return lines or [f"none: the {len(self._matched)} goldens matching {self.pattern} are unchanged"]
