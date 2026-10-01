from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from .. import languages
from ..brief_text import PRODUCT_NAME, BriefText
from ..collectors.base import Message
from ..config import Config
from ..summarize import digest_of


def write(cfg: Config, date_str: str, messages: list[Message], summary: dict[str, Any],
          calendar_created: list[dict], delivered: bool = True) -> Path:
    out_dir = cfg.archive.resolved_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    md_path = out_dir / f"{date_str}.md"
    json_path = out_dir / f"{date_str}.raw.json"

    json_path.write_text(json.dumps(
        {
            "generated_at": datetime.now().isoformat(),
            "messages": [m.to_dict() for m in messages],
            "summary": summary,
            "calendar_created": calendar_created,
            "delivered": delivered,
        },
        ensure_ascii=False, indent=2,
    ))

    md = _render_markdown(languages.text(cfg, cfg.brief_language()), date_str, summary, calendar_created, messages)
    md_path.write_text(md)
    return md_path


def _render_markdown(t: BriefText, date_str: str, summary: dict[str, Any],
                     calendar_created: list[dict], messages: list[Message]) -> str:
    lines: list[str] = [f"# {PRODUCT_NAME} · {date_str}", ""]

    digest = digest_of(summary)
    if digest:
        lines += [t.archive_digest, "", digest, ""]

    for kid in summary.get("per_kid", []) or []:
        lines.append(f"### {kid.get('kid','(unknown)')}")
        notices = kid.get("notices") or []
        if notices:
            lines.append(t.archive_notices)
            lines += [f"- {n.get('text', '') if isinstance(n, dict) else n}" for n in notices]
            lines.append("")
        actions = kid.get("action_items") or []
        if actions:
            lines.append(t.archive_action_items)
            for a in actions:
                who = a.get("who", "")
                by = a.get("by", "")
                when_who = ", ".join(x for x in (t.due.format(date=by) if by else "", who) if x)
                lines.append(f"- [ ] {a.get('what','')}  " + (f"_({when_who})_" if when_who else ""))
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


def recent_points(cfg: Config, today: str, days: int = 3) -> list[dict[str, Any]]:
    """Notices and to-dos from the previous few nights' briefs, oldest first. Tonight's summary
    uses them to stay consistent with what it already said and to re-surface near deadlines.
    Briefs that were never delivered are skipped: the parents were not told, and their Messages
    come round again tonight. Archives from before the flag count as delivered."""
    base = date.fromisoformat(today)
    out: list[dict[str, Any]] = []
    for i in range(days, 0, -1):
        day = (base - timedelta(days=i)).isoformat()
        try:
            data = json.loads((cfg.archive.resolved_dir() / f"{day}.raw.json").read_text())
        except (OSError, ValueError):
            continue
        if data.get("delivered") is False:
            continue
        kids = [{"kid": k.get("kid"), "notices": k.get("notices") or [],
                 "action_items": k.get("action_items") or []}
                for k in (data.get("summary") or {}).get("per_kid") or []
                if k.get("notices") or k.get("action_items")]
        if kids:
            out.append({"date": day, "per_kid": kids})
    return out


def has_due_soon(points: list[dict[str, Any]], today: str, within_days: int = 3) -> bool:
    """Whether an earlier to-do falls due between today and `within_days` from now."""
    start = date.fromisoformat(today)
    end = start + timedelta(days=within_days)
    for day in points:
        for kid in day["per_kid"]:
            for item in kid["action_items"]:
                try:
                    if isinstance(item, dict) and start <= date.fromisoformat(str(item.get("by"))[:10]) <= end:
                        return True
                except ValueError:
                    continue
    return False
