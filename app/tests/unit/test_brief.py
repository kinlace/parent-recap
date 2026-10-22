"""Archives written before the Brief was a typed object: each loads, and renders as it did then,
against golden files made by the version that wrote them."""
from __future__ import annotations

import json
from datetime import timezone
from pathlib import Path

import pytest

from conftest import NOW, TZ, html_for_golden, ics_for_golden

from family_brief import render
from family_brief.actions import archive
from family_brief.brief_text import TEXT
from family_brief.collectors.base import Message
from family_brief.summarize import extract_calendar_events

DATA = Path(__file__).parent / "data"
STAMP = NOW.astimezone(timezone.utc)


def loaded(name: str) -> dict:
    return json.loads((DATA / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("version", ["0.6", "0.2"])
def test_an_old_archive_renders_as_it_did(golden, version):
    data = loaded(f"archive-{version}.raw.json")
    summary, t = data["summary"], TEXT["zh"]
    events = extract_calendar_events(summary, TZ, t.untitled)
    night = render.Night(date="2026-09-27", tz=TZ, t=t, assistant="Claude", summary=summary,
                         coverage=summary.get("_coverage") or {}, created=data["calendar_created"],
                         ics_events=events, stamp=STAMP)
    [mail] = render.brief(night, [render.Version(t, ["parent@example.com"], summary)], "parent@example.com", [])

    golden(f"archive_{version}.zh.txt", mail.text)
    golden(f"archive_{version}.zh.html", html_for_golden(mail.html))
    if events:
        golden(f"archive_{version}.zh.ics", ics_for_golden(mail.attachments[0][1]))
    messages = [Message.from_dict(m) for m in data["messages"]]
    golden(f"archive_{version}.zh.md",
           archive._render_markdown(t, "2026-09-27", summary, data["calendar_created"], messages))
