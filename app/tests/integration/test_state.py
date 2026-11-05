"""The state file: saved whole, so a reader in another thread or process never sees half of it."""
from __future__ import annotations

import json
import os

from family_brief.state import State


def test_a_reader_during_a_save_sees_the_old_state_whole(tmp_path, monkeypatch):
    # The setup page's outcome check read the state while turning on the evening Brief was saving
    # it, got half a file, and said no Brief had gone out yet.
    path = tmp_path / "state.json"
    first = State(path)
    first.mark_delivered_now(["parent@example.com"])
    first.save()
    seen_during = []
    replace = os.replace

    def replacing(src, dst):
        seen_during.append(State(path).delivered_at("parent@example.com"))
        replace(src, dst)

    monkeypatch.setattr(os, "replace", replacing)
    second = State(path)
    second.mark_scheduled_now()
    second.save()

    assert seen_during and seen_during[0] is not None
    assert State(path).delivered_at("parent@example.com") is not None
    assert "scheduled_at" in json.loads(path.read_text(encoding="utf-8"))
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]  # no file left over
