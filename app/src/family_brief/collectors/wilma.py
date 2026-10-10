from __future__ import annotations

import hashlib
import json
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import own_node
from ..config import Config
from ..state import State
from .base import Message, retry_once_on_timeout, unreadable

log = logging.getLogger(__name__)

MAX_BODY_CHARS = 8000
NOT_INSTALLED = "the wilma CLI isn't installed (connect Wilma again: parent-recap setup wilma)"


class WilmaError(Exception):
    """A `wilma` command that didn't give JSON back, with why in its message. `code` is the CLI's
    own name for what went wrong, which it gives from 2.0 on, such as login_failed."""

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code

    @property
    def wrong_password(self) -> bool:
        """Whether Wilma turned the username and password down, and nothing else went wrong. The
        CLI before 2.0 said only "Wilma login failed"."""
        return self.code == "login_failed" or "Wilma login failed" in str(self)


def _run(args: list[str], config: Path | None = None) -> Any:
    """`wilma <args> --json` on Parent Recap's own Node (ADR 0011), as JSON. `config` is the CLI's
    config file to read instead of its own."""
    command = own_node.wilma()
    if command is None:
        raise WilmaError(NOT_INSTALLED)
    try:
        proc = retry_once_on_timeout(
            lambda: subprocess.run([*command, *args, "--json"], capture_output=True, text=True,
                                   timeout=60, env=own_node.wilma_env(config)),
            (subprocess.TimeoutExpired,), f"wilma {' '.join(args)}")
    except FileNotFoundError:
        raise WilmaError(NOT_INSTALLED) from None
    except subprocess.TimeoutExpired:
        raise WilmaError(f"wilma {' '.join(args)} timed out") from None
    if proc.returncode != 0:
        raise _failed(args, proc)
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise WilmaError(f"wilma JSON parse failed: {e}; head: {proc.stdout[:200]}") from None


def _failed(args: list[str], proc: subprocess.CompletedProcess) -> WilmaError:
    """Why `wilma <args> --json` failed. The CLI says it on stdout, as {"status": "error", "code":
    ..., "message": ...}, with a code of its own from 2.0 on. Anything else goes to stderr."""
    try:
        said = json.loads(proc.stdout)
    except ValueError:
        said = None
    code = message = None
    if isinstance(said, dict) and said.get("status") == "error":
        code = said["code"] if isinstance(said.get("code"), str) else None
        message = said["message"] if isinstance(said.get("message"), str) else None
    reason = message or proc.stderr.strip() or proc.stdout.strip()
    if code:
        reason = f"{code}: {reason}"
    return WilmaError(f"wilma {' '.join(args)} failed: {reason[:200]}", code)


def _run_or_log(args: list[str]) -> Any:
    """`_run`, or None after logging why, which the Brief's coverage line then shows."""
    try:
        return _run(args)
    except WilmaError as e:
        log.error("%s", e)
        return None


def _parse_ts(v: Any) -> datetime | None:
    """A time the CLI gives, or None when it doesn't know it: from 2.0 it says null, and before
    that it gave the epoch."""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        dt = datetime.fromtimestamp(v, tz=timezone.utc)
    elif isinstance(v, str):
        try:
            dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    else:
        return None
    return dt if dt.timestamp() > 0 else None


def _first(d: dict, *keys: str, default: Any = None) -> Any:
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return v
    return default


def list_kids(config: Path | None = None) -> list[dict[str, Any]]:
    """Each Kid in Wilma as their full name, school and class, from `wilma kids list`. The CLI
    gives only the name, so school and class are None unless it gives them. From 2.0 it lists
    the Kids of every sign-in its config has, so `config`, a config file of its own, checks one
    sign-in alone. Raises WilmaError when the CLI isn't signed in."""
    data = _run(["kids", "list"], config)
    items = data if isinstance(data, list) else (data.get("kids") or data.get("students") or [])
    kids = []
    for item in items:
        if not isinstance(item, dict):
            continue
        kid = item.get("student") if isinstance(item.get("student"), dict) else item
        kids.append({"name": kid.get("name"), "school": _first(kid, "school", "schoolName"),
                     "class": _first(kid, "className", "class")})
    return kids


# What a student's block lists its items under: `items` in wilma CLI 1.x, and from 2.0 what the
# command lists.
_ITEMS = ("items", "messages", "news", "lessons")


def _walk_students(payload: Any) -> list[tuple[dict, list[dict]]]:
    """Parse `{students: [{student: {...}, items: [...]}]}` shape, whose items are `messages`,
    `news` or `lessons` from wilma CLI 2.0. Returns [(student_info, items), ...]."""
    if not isinstance(payload, dict):
        return []
    out: list[tuple[dict, list[dict]]] = []
    for block in payload.get("students", []) or []:
        if not isinstance(block, dict):
            continue
        student = block.get("student") or {}
        items = next((block[k] for k in _ITEMS if isinstance(block.get(k), list)), [])
        out.append((student, items))
    return out


class _Wilmas:
    """The Wilmas the CLI's saved sign-ins are for, from its config. From 2.0 the CLI signs in with
    every one of them, labels each student with their Wilma when there are several, and a message
    or news id is unique only within one Wilma."""

    def __init__(self, kids: set[str]) -> None:
        from .. import setup_wilma  # it reads this module's Kid list, so it is imported only here
        self._setup = setup_wilma
        path = setup_wilma.config_path()
        self.profiles = setup_wilma.profiles(path)
        last = setup_wilma.last_profile_id(path)
        first = next((p for p in self.profiles if p.get("id") == last),
                     self.profiles[0] if len(self.profiles) == 1 else None)
        # The Wilma the CLI's commands sign in to first: before 2.0, the only one.
        self.first = self._address(first)
        self.kids = kids

    def _address(self, profile: dict | None) -> str | None:
        url = profile.get("tenantUrl") if profile else None
        return self._setup.normalized(url) if isinstance(url, str) and url else None

    def named(self, label: Any) -> list[dict]:
        """The sign-ins the CLI calls `label`: the Wilma's name, or its address."""
        return [p for p in self.profiles
                if isinstance(label, str) and label in (p.get("tenantName"), p.get("tenantUrl"))]

    def of(self, student: dict) -> str | None:
        """The address of the Wilma `student`'s items come from, or None when it isn't known."""
        label = student.get("wilma")
        if label is None:
            return self.first
        addresses = {self._address(p) for p in self.named(label)} - {None}
        return addresses.pop() if len(addresses) == 1 else None

    def log_problems(self, payload: dict) -> None:
        """Logs each sign-in that didn't answer while others did, which the CLI lists from 2.0 on,
        by its username and Wilma. One the Household's Brief depends on is an error, so the Brief
        says Wilma was partly read. One for none of the Kids, such as one left from an earlier use
        of the CLI, is only a warning, and doctor names it."""
        for problem in payload.get("problems") or []:
            if not isinstance(problem, dict):
                continue
            signins = self.named(problem.get("wilma"))
            who = ", ".join(f"{p.get('username')} at {p.get('tenantName') or p.get('tenantUrl')}"
                            for p in signins) or str(problem.get("wilma"))
            if not signins or any(self._setup.for_the_household(p, self.kids) for p in signins):
                log.error("wilma: %s (the sign-in as %s)", problem.get("message"), who)
            else:
                log.warning("wilma: %s (the sign-in as %s, for none of the Kids, so the Brief "
                            "doesn't say so)", problem.get("message"), who)


def _scope(address: str | None) -> str | None:
    """A short tag for the Wilma at `address`, which the Source's ids carry so that two Wilmas'
    items with the same id stay apart. A tag, not the address, since the model sees the ids."""
    return hashlib.sha256(address.encode()).hexdigest()[:8] if address else None


def _item(detail: Any) -> dict:
    """The message or news item `messages read` or `news read` gives: the whole answer in wilma
    CLI 1.x, and its `message` or `news` from 2.0."""
    if not isinstance(detail, dict):
        return {}
    return next((detail[k] for k in ("message", "news") if isinstance(detail.get(k), dict)), detail)


def _addressees(item: dict) -> int | None:
    """How many people a Wilma message is addressed to, by the different names the CLI gives as
    its `recipients`, or None when it gives none: wilma CLI 1.x doesn't, and Wilma may hide them.
    Only the count is kept, since the names are Third Parties' (ADR 0013)."""
    names = item.get("recipients")
    if not isinstance(names, list):
        return None
    different = {" ".join(n.split()).casefold() for n in names if isinstance(n, str) and n.strip()}
    return len(different) or None


def _collect_list(state: State, source_tag: str,
                   list_cmd: list[str],
                   read_cmd_prefix: list[str] | None,
                   cutoff: datetime | None = None,
                   wilmas: _Wilmas | None = None) -> list[Message]:
    """Pull a list (messages|news) per student and fetch bodies. Each item's id carries its Wilma's
    tag when that is known. An id seen without one, as before 2.0, when the CLI read only the first
    Wilma, still counts as seen for that Wilma's items."""
    payload = _run_or_log(list_cmd)
    if payload is None:
        return []
    wilmas = wilmas or _Wilmas(set())
    wilmas.log_problems(payload)

    results: list[Message] = []
    total = 0
    for student, items in _walk_students(payload):
        student_name = student.get("name")
        student_number = student.get("studentNumber")
        address = wilmas.of(student)
        scope = _scope(address)
        for it in items:
            total += 1
            wilma_id = _first(it, "wilmaId", "id", "newsId")
            if not wilma_id:
                continue
            before = f"{source_tag}:{wilma_id}"
            dedup_key = f"{source_tag}:{scope}:{wilma_id}" if scope else before
            if state.has_seen_message("wilma", dedup_key) or (
                    address == wilmas.first and state.has_seen_message("wilma", before)):
                continue

            subject = _first(it, "subject", "title", "headline", default="") or ""
            ts = _parse_ts(_first(it, "sentAt", "publishedAt", "published", "date", "timestamp", "createdAt"))
            if cutoff and (ts is None or ts < cutoff):
                # Too old for tonight, or with no date: from 2.0 the CLI also lists the news
                # pinned to Wilma's page, which have none and stay there all year.
                state.mark_message_seen("wilma", dedup_key)
                continue
            sender = _first(it, "sender", "from", "senderName", "author", default="")

            body, addressees = "", None
            if read_cmd_prefix and student_number:
                try:
                    detail = _item(_run([*read_cmd_prefix, str(wilma_id), "--student",
                                         str(student_number)]))
                except WilmaError as e:
                    # Left unseen, so a later run reads it again rather than passing on no body.
                    log.error("%s", unreadable("wilma", dedup_key, e))
                    continue
                body = _first(detail, "body", "content", "text", "html", "plainText",
                              default="") or ""
                addressees = _addressees(detail)
                if not sender:
                    sender = _first(detail, "senderName", "sender", "from", "author", default="")

            metadata = {"wilma_kind": source_tag, "raw_id": wilma_id,
                        "student_number": student_number, "wilma_url": address}
            if source_tag == "message":
                metadata["addressee_count"] = addressees  # held_back.sent_to_everyone reads it
            results.append(Message(
                source="wilma",
                external_id=dedup_key,
                timestamp=ts or datetime.now(timezone.utc),
                sender=str(sender) if sender else None,
                subject=str(subject),
                body=str(body)[:MAX_BODY_CHARS],
                chat_name=source_tag,
                kid_hint=student_name,
                metadata=metadata,
            ))
            state.mark_message_seen("wilma", dedup_key)

    log.info("wilma %s: %d items total, %d new", " ".join(list_cmd), total, len(results))
    return results


# Model input, so English whatever the Brief's language; the summarize prompt explains the header.
_WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _collect_schedule(state: State, wilmas: _Wilmas | None = None) -> list[Message]:
    """Next-school-day schedule as a synthetic Message per run.

    IMPORTANT: `wilma schedule --when tomorrow` on Fri/Sat/Sun skips ahead to the
    next actual school day (usually Monday). We must therefore label the message
    with the concrete ISO date the returned lessons *actually* fall on, so the LLM
    doesn't treat "tomorrow" literally.
    """
    payload = _run_or_log(["schedule", "list", "--all-students", "--when", "tomorrow"])
    if payload is None:
        return []
    if isinstance(payload, dict):
        (wilmas or _Wilmas(set())).log_problems(payload)
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


def link(m: Message) -> str | None:
    """Where the family reads the Wilma message or news item `m` in Wilma: its page under the
    Kid's role (the role a path starting /!<student number> picks, as the CLI's own requests do), on
    the Wilma it came from, or for one read before that was kept, the Wilma the CLI signed in to
    last. None for the timetable, or when that Wilma isn't known."""
    from .. import setup_wilma  # it reads this module's Kid list, so it is imported only here

    kind = {"message": "messages", "news": "news"}.get(str(m.metadata.get("wilma_kind")))
    raw_id = m.metadata.get("raw_id")
    tenant = m.metadata.get("wilma_url")
    if not tenant:
        path = setup_wilma.config_path()
        profile = setup_wilma.profile(path, setup_wilma.last_profile_id(path) or "")
        tenant = profile.get("tenantUrl") if profile else None
    if not (kind and raw_id and isinstance(tenant, str) and tenant.startswith("https://")):
        return None
    student = m.metadata.get("student_number")
    role = f"/!{student}" if student else ""
    return f"{tenant.rstrip('/')}{role}/{kind}/{raw_id}"


def collect(cfg: Config, state: State, kid_terms: list[str]) -> list[Message]:
    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(hours=cfg.wilma.lookback_hours)
    wilmas = _Wilmas({k.name.casefold() for k in cfg.kids})
    msgs = _collect_list(
        state, "message",
        list_cmd=["messages", "list", "--all-students", "--limit", "30"],
        read_cmd_prefix=["messages", "read"],
        cutoff=cutoff,
        wilmas=wilmas,
    )
    news = _collect_list(
        state, "news",
        list_cmd=["news", "list", "--all-students", "--limit", "20"],
        read_cmd_prefix=["news", "read"],
        # News only has a publication date (midnight), so allow an extra day or an item
        # posted after last night's run would already look too old tonight.
        cutoff=cutoff - timedelta(days=1),
        wilmas=wilmas,
    )
    schedule = _collect_schedule(state, wilmas)
    return [*msgs, *news, *schedule]
