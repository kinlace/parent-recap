from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

from .. import languages
from ..brief import ActionItem, Brief, EarlierBrief, KidSection, RunNotes, from_archive, to_archive
from ..brief_text import PRODUCT_NAME, BriefText
from ..collectors.base import Message
from ..config import Config, ReReminders
from ..summarize import third_parties
from ..week_ahead import NIGHTS_BACK, EarlierNight


def write(cfg: Config, date_str: str, messages: list[Message], brief: Brief, notes: RunNotes,
          calendar_created: list[dict], delivered: bool = True) -> Path:
    out_dir = cfg.archive.resolved_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    md_path = out_dir / f"{date_str}.md"
    json_path = out_dir / f"{date_str}.raw.json"

    json_path.write_text(json.dumps(
        {
            "generated_at": datetime.now().isoformat(),
            "messages": [m.to_dict() for m in messages],
            "summary": to_archive(brief, notes),
            "calendar_created": calendar_created,
            "delivered": delivered,
        },
        ensure_ascii=False, indent=2,
    ), encoding="utf-8")

    md = _render_markdown(languages.text(cfg, cfg.brief_language()), date_str, brief, calendar_created, messages)
    md_path.write_text(md, encoding="utf-8")
    return md_path


def _render_markdown(t: BriefText, date_str: str, brief: Brief,
                     calendar_created: list[dict], messages: list[Message]) -> str:
    lines: list[str] = [f"# {PRODUCT_NAME} · {date_str}", ""]

    if brief.digest:
        lines += [t.archive_digest, "", brief.digest, ""]

    for kid in brief.kids:
        lines.append(f"### {kid.kid or '(unknown)'}")
        if kid.notices:
            lines.append(t.archive_notices)
            lines += [f"- {n.text}" for n in kid.notices]
            lines.append("")
        if kid.action_items:
            lines.append(t.archive_action_items)
            for a in kid.action_items:
                when_who = ", ".join(x for x in (t.due.format(date=a.by) if a.by else "", a.who or "") if x)
                lines.append(f"- [ ] {a.what}  " + (f"_({when_who})_" if when_who else ""))
            lines.append("")

    for g in brief.groups:
        lines += [f"### 💬 {g.group}", "", g.text, ""]

    if calendar_created:
        lines += [t.archive_new_events, ""]
        for ev in calendar_created:
            lines.append(f"- **{ev['title']}** @ {ev['start']}" + (f" — {ev['kid']}" if ev.get('kid') else ""))
        lines.append("")

    if messages:
        lines += [t.archive_messages, ""]
        for m in messages:
            lines.append(f"- `{m.source}` {m.timestamp.isoformat()} "
                         f"**{m.subject or m.chat_name or ''}** "
                         f"<{m.sender or ''}>")
        lines.append("")

    return "\n".join(lines)


def recent_points(cfg: Config, today: str, days: int = 3) -> list[EarlierBrief]:
    """Notices and to-dos from the previous few nights' briefs, oldest first. Tonight's summary
    uses them to stay consistent with what it already said and to re-remind a to-do on the
    evening before it is due (#1051). An older night, back as far as the Week Ahead reads, gives
    only its to-dos due tomorrow, so one told a week ahead still comes again on its eve; with
    `re_reminders: daily` (#1054), every to-do not yet due, so it comes again every evening.
    Briefs that were never delivered are skipped: the parents were not told, and their Messages
    come round again tonight. Archives from before the flag count as delivered. Each night's
    `third_parties` are the people its Messages came from, for the AI filter, not for the model."""
    base = date.fromisoformat(today)
    out: list[EarlierBrief] = []
    for i in range(max(days, NIGHTS_BACK), 0, -1):
        day = (base - timedelta(days=i)).isoformat()
        try:
            data = json.loads((cfg.archive.resolved_dir() / f"{day}.raw.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or data.get("delivered") is False:
            continue
        brief, _ = from_archive(data.get("summary"))
        kids = brief.kids if i <= days else [
            KidSection(k.kid, [], [a for a in k.action_items if _to_remind(a, today, cfg.re_reminders)])
            for k in brief.kids]
        kids = [k for k in kids if k.notices or k.action_items]
        if kids:
            out.append(EarlierBrief(day, kids, third_parties(cfg, data.get("messages") or [])))
    return out


def _to_remind(item: ActionItem, today: str, re_reminders: ReReminders) -> bool:
    """Whether tonight's Brief re-reminds `item`: due tomorrow, or with `daily` (#1054) due
    tomorrow or later. One without a readable due date is never re-reminded."""
    try:
        due = date.fromisoformat(str(item.by or "")[:10])
    except ValueError:
        return False
    days_left = (due - date.fromisoformat(today)).days
    return days_left == 1 or (re_reminders == "daily" and days_left > 1)


def earlier_nights(cfg: Config, today: str, days: int = NIGHTS_BACK) -> list[EarlierNight]:
    """The Briefs of the `days` evenings before `today` that reached the parents, each with the
    people its Messages came from, for the Week Ahead (ADR 0053). A Brief never delivered is left
    out, as recent_points leaves it out."""
    base = date.fromisoformat(today)
    out: list[EarlierNight] = []
    for i in range(days, 0, -1):
        day = (base - timedelta(days=i)).isoformat()
        try:
            data = json.loads((cfg.archive.resolved_dir() / f"{day}.raw.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or data.get("delivered") is False:
            continue
        brief, _ = from_archive(data.get("summary"))
        out.append(EarlierNight(day, brief, third_parties(cfg, data.get("messages") or [])))
    return out


def has_re_reminder(points: list[EarlierBrief], today: str, re_reminders: ReReminders = "once") -> bool:
    """Whether tonight's Brief re-reminds an earlier to-do: one due tomorrow (#1051), or with
    `re_reminders` daily one due tomorrow or later (#1054), so a quiet night still sends a Brief."""
    return any(_to_remind(item, today, re_reminders) for day in points for kid in day.kids for item in kid.action_items)
