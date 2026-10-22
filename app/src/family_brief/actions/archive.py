from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

from .. import languages
from ..brief import Brief, EarlierBrief, RunNotes, from_archive, to_archive
from ..brief_text import PRODUCT_NAME, BriefText
from ..collectors.base import Message
from ..config import Config
from ..summarize import third_parties


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
    uses them to stay consistent with what it already said and to re-surface near deadlines.
    Briefs that were never delivered are skipped: the parents were not told, and their Messages
    come round again tonight. Archives from before the flag count as delivered. Each night's
    `third_parties` are the people its Messages came from, for the AI filter, not for the model."""
    base = date.fromisoformat(today)
    out: list[EarlierBrief] = []
    for i in range(days, 0, -1):
        day = (base - timedelta(days=i)).isoformat()
        try:
            data = json.loads((cfg.archive.resolved_dir() / f"{day}.raw.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if data.get("delivered") is False:
            continue
        kids = [k for k in from_archive(data.get("summary"))[0].kids if k.notices or k.action_items]
        if kids:
            out.append(EarlierBrief(day, kids, third_parties(cfg, data.get("messages") or [])))
    return out


def has_due_soon(points: list[EarlierBrief], today: str, within_days: int = 3) -> bool:
    """Whether an earlier to-do falls due between today and `within_days` from now."""
    start = date.fromisoformat(today)
    end = start + timedelta(days=within_days)
    for day in points:
        for kid in day.kids:
            for item in kid.action_items:
                try:
                    if start <= date.fromisoformat(str(item.by)[:10]) <= end:
                        return True
                except ValueError:
                    continue
    return False
