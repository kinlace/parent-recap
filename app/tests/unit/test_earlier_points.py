"""The earlier Briefs' points tonight's Brief gets (archive.recent_points), and whether one of
their to-dos makes a quiet night worth a Brief (archive.has_due_tomorrow). An earlier to-do is
re-reminded only on the evening before it is due (#1051), however long ago it was first told."""
from __future__ import annotations

import json
from datetime import date, timedelta

from conftest import default_config
from family_brief.actions import archive
from family_brief.brief import ActionItem, Brief, KidSection, Notice, RunNotes, to_archive
from family_brief.config import Config

TODAY = "2026-10-11"  # a Sunday


def _config(tmp_path) -> Config:
    return Config.model_validate({**default_config(), "archive": {"dir": str(tmp_path)}})


def _night(cfg: Config, days_ago: int, *, notice: str = "", todos: tuple[tuple[str, str], ...] = (),
           delivered: bool = True) -> None:
    day = (date.fromisoformat(TODAY) - timedelta(days=days_ago)).isoformat()
    kid = KidSection("Mia", [Notice(notice)] if notice else [],
                     [ActionItem(what, by=by) for what, by in todos])
    (cfg.archive.resolved_dir() / f"{day}.raw.json").write_text(json.dumps(
        {"messages": [], "summary": to_archive(Brief(kids=[kid]), RunNotes()), "delivered": delivered}))


def _points(cfg: Config) -> list[tuple[str, list[str], list[str]]]:
    return [(day.date, [n.text for k in day.kids for n in k.notices],
             [a.what for k in day.kids for a in k.action_items])
            for day in archive.recent_points(cfg, TODAY)]


def test_the_last_three_nights_give_all_their_points(tmp_path):
    cfg = _config(tmp_path)
    _night(cfg, 3, notice="Class photo on Friday", todos=(("Pay the trip", "2026-10-20"),))
    _night(cfg, 1, todos=(("Bring boots", "2026-10-12"),))

    assert _points(cfg) == [("2026-10-08", ["Class photo on Friday"], ["Pay the trip"]),
                            ("2026-10-10", [], ["Bring boots"])]


def test_an_older_night_gives_only_its_to_dos_due_tomorrow(tmp_path):
    cfg = _config(tmp_path)
    _night(cfg, 9, notice="Autumn break next week",
           todos=(("Sign the permission slip", "2026-10-12"), ("Order photos", "2026-10-20")))
    _night(cfg, 5, todos=(("Pay the fee", "2026-10-14"),))

    assert _points(cfg) == [("2026-10-02", [], ["Sign the permission slip"])]


def test_an_older_night_that_was_never_delivered_gives_nothing(tmp_path):
    cfg = _config(tmp_path)
    _night(cfg, 9, todos=(("Sign the permission slip", "2026-10-12"),), delivered=False)

    assert _points(cfg) == []


def test_only_a_to_do_due_tomorrow_makes_a_quiet_night_worth_a_brief(tmp_path):
    cfg = _config(tmp_path)
    _night(cfg, 1, todos=(("Bring boots", "2026-10-11"), ("Pay the trip", "2026-10-13")))
    assert not archive.has_due_tomorrow(archive.recent_points(cfg, TODAY), TODAY)

    _night(cfg, 2, todos=(("Sign the slip", "2026-10-12"),))
    assert archive.has_due_tomorrow(archive.recent_points(cfg, TODAY), TODAY)
