from __future__ import annotations

import json
import logging
import subprocess
from datetime import datetime, timezone
from typing import Any

from ..config import Config
from ..state import State
from .base import Message, retry_once_on_timeout, unreadable

log = logging.getLogger(__name__)

WILMA = "wilma"  # installed via: npm i -g @wilm-ai/wilma-cli
MAX_BODY_CHARS = 8000


class WilmaError(Exception):
    """A `wilma` command that didn't give JSON back; the message says why."""


def _run(args: list[str]) -> Any:
    try:
        proc = retry_once_on_timeout(
            lambda: subprocess.run([WILMA, *args, "--json"], capture_output=True, text=True, timeout=60),
            (subprocess.TimeoutExpired,), f"wilma {' '.join(args)}")
    except FileNotFoundError:
        raise WilmaError("wilma CLI not installed (npm i -g @wilm-ai/wilma-cli)") from None
    except subprocess.TimeoutExpired:
        raise WilmaError(f"wilma {' '.join(args)} timed out") from None
    if proc.returncode != 0:
        raise WilmaError(f"wilma {' '.join(args)} failed: {proc.stderr.strip()[:200]}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise WilmaError(f"wilma JSON parse failed: {e}; head: {proc.stdout[:200]}") from None


def _run_or_log(args: list[str]) -> Any:
    """`_run`, or None after logging why, which the Brief's coverage line then shows."""
    try:
        return _run(args)
    except WilmaError as e:
        log.error("%s", e)
        return None


def _parse_ts(v: Any) -> datetime:
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(v, tz=timezone.utc)
    if isinstance(v, str):
        try:
            dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _first(d: dict, *keys: str, default: Any = None) -> Any:
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return v
    return default


def list_kids() -> list[dict[str, Any]]:
    """Each Kid in Wilma as their full name, school and class, from `wilma kids list`. Wilma CLI
    1.4 gives only the name, so school and class are None unless the CLI gives them. Raises
    WilmaError when the CLI isn't signed in."""
    data = _run(["kids", "list"])
    items = data if isinstance(data, list) else (data.get("kids") or data.get("students") or [])
    kids = []
    for item in items:
        if not isinstance(item, dict):
            continue
        kid = item.get("student") if isinstance(item.get("student"), dict) else item
        kids.append({"name": kid.get("name"), "school": _first(kid, "school", "schoolName"),
                     "class": _first(kid, "className", "class")})
    return kids


def _walk_students(payload: Any) -> list[tuple[dict, list[dict]]]:
    """Parse `{students: [{student: {...}, items: [...]}]}` shape. Returns [(student_info, items), ...]."""
    if not isinstance(payload, dict):
        return []
    out: list[tuple[dict, list[dict]]] = []
    for block in payload.get("students", []) or []:
        if not isinstance(block, dict):
            continue
        student = block.get("student") or {}
        items = block.get("items") or []
        if isinstance(items, list):
            out.append((student, items))
    return out


def _collect_list(state: State, source_tag: str,
                   list_cmd: list[str],
                   read_cmd_prefix: list[str] | None,
                   cutoff: datetime | None = None) -> list[Message]:
    """Pull a list (messages|news) per student and fetch bodies."""
    payload = _run_or_log(list_cmd)
    if payload is None:
        return []

    results: list[Message] = []
    total = 0
    for student, items in _walk_students(payload):
        student_name = student.get("name")
        student_number = student.get("studentNumber")
        for it in items:
            total += 1
            wilma_id = _first(it, "wilmaId", "id", "newsId")
            if not wilma_id:
                continue
            dedup_key = f"{source_tag}:{wilma_id}"
            if state.has_seen_message("wilma", dedup_key):
                continue

            subject = _first(it, "subject", "title", "headline", default="") or ""
            ts = _parse_ts(_first(it, "sentAt", "publishedAt", "published", "date", "timestamp", "createdAt"))
            if cutoff and ts < cutoff:
                state.mark_message_seen("wilma", dedup_key)
                continue
            sender = _first(it, "sender", "from", "senderName", "author", default="")

            body = ""
            if read_cmd_prefix and student_number:
                try:
                    detail = _run([*read_cmd_prefix, str(wilma_id), "--student", str(student_number)])
                except WilmaError as e:
                    # Left unseen, so a later run reads it again rather than passing on no body.
                    log.error("%s", unreadable("wilma", dedup_key, e))
                    continue
                if isinstance(detail, dict):
                    body = _first(detail, "body", "content", "text", "html", "plainText",
                                  default="") or ""
                    if not body and isinstance(detail.get("message"), dict):
                        body = _first(detail["message"], "body", "content", "text",
                                      default="") or ""
                    if not sender:
                        sender = _first(detail, "senderName", "sender", "from",
                                        "author", default="")

            results.append(Message(
                source="wilma",
                external_id=dedup_key,
                timestamp=ts,
                sender=str(sender) if sender else None,
                subject=str(subject),
                body=str(body)[:MAX_BODY_CHARS],
                chat_name=source_tag,
                kid_hint=student_name,
                metadata={"wilma_kind": source_tag, "raw_id": wilma_id,
                          "student_number": student_number},
            ))
            state.mark_message_seen("wilma", dedup_key)

    log.info("wilma %s: %d items total, %d new", " ".join(list_cmd), total, len(results))
    return results


# Model input, so English whatever the Brief's language; the summarize prompt explains the header.
_WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _collect_schedule(state: State) -> list[Message]:
    """Next-school-day schedule as a synthetic Message per run.

    IMPORTANT: `wilma schedule --when tomorrow` on Fri/Sat/Sun skips ahead to the
    next actual school day (usually Monday). We must therefore label the message
    with the concrete ISO date the returned lessons *actually* fall on, so the LLM
    doesn't treat "tomorrow" literally.
    """
    payload = _run_or_log(["schedule", "list", "--all-students", "--when", "tomorrow"])
    if payload is None:
        return []
    now = datetime.now(timezone.utc)
    sched_ext_id = f"schedule:{now.strftime('%Y%m%d')}"
    if state.has_seen_message("wilma", sched_ext_id):
        return []

    # Group lessons by their actual date (per-student), so LLM sees which day these belong to.
    from collections import defaultdict
    by_date_student: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for student, items in _walk_students(payload):
        student_name = student.get("name", "?")
        for it in items:
            actual_date = _first(it, "date", default="") or ""
            start = _first(it, "start", "startTime", "time", default="")
            end = _first(it, "end", "endTime", default="")
            subj = _first(it, "subject", "course", "title", "name", default="")
            loc = _first(it, "room", "location", "classroom", default="")
            line = (f"  {start}{'–' + end if end else ''}  {subj}"
                    + (f"  @ {loc}" if loc else ""))
            by_date_student[actual_date][student_name].append(line)

    if not by_date_student:
        return []

    # Compute "today" locally so weekday labels are correct.
    from datetime import date as _date, datetime as _dt
    today_local = _dt.now().astimezone().date()

    lines: list[str] = []
    for iso_date in sorted(by_date_student):
        try:
            d = _date.fromisoformat(iso_date)
            days_ahead = (d - today_local).days
            if days_ahead == 0:
                rel = "today"
            elif days_ahead == 1:
                rel = "tomorrow"
            elif days_ahead == 2:
                rel = "day after tomorrow"
            else:
                rel = f"in {days_ahead} days"
            weekday = _WEEKDAYS[d.weekday()]
            header = f"[{iso_date} {weekday} · {rel}]"
        except ValueError:
            header = f"[{iso_date}]"
        lines.append(header)
        for student_name, entries in by_date_student[iso_date].items():
            lines.append(f"— {student_name} —")
            lines.extend(entries)
        lines.append("")

    first_iso = sorted(by_date_student.keys())[0] if by_date_student else "?"
    state.mark_message_seen("wilma", sched_ext_id)
    return [Message(
        source="wilma",
        external_id=sched_ext_id,
        timestamp=now,
        subject=f"Timetable for the next school day (from {first_iso})",
        body="\n".join(lines)[:MAX_BODY_CHARS],
        chat_name="schedule",
        metadata={"wilma_kind": "schedule_next_school_day",
                  "first_date": first_iso},
    )]


def collect(cfg: Config, state: State, kid_terms: list[str]) -> list[Message]:
    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(hours=cfg.wilma.lookback_hours)
    msgs = _collect_list(
        state, "message",
        list_cmd=["messages", "list", "--all-students", "--limit", "30"],
        read_cmd_prefix=["messages", "read"],
        cutoff=cutoff,
    )
    news = _collect_list(
        state, "news",
        list_cmd=["news", "list", "--all-students", "--limit", "20"],
        read_cmd_prefix=["news", "read"],
        # News only has a publication date (midnight), so allow an extra day or an item
        # posted after last night's run would already look too old tonight.
        cutoff=cutoff - timedelta(days=1),
    )
    schedule = _collect_schedule(state)
    return [*msgs, *news, *schedule]
