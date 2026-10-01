"""Friday afternoon: fetch weekend events, rank via Claude, deliver via iMessage.

Reuses summarize.call_llm_json + actions.imessage. Persists candidates+picks so
we can wire a feedback loop later.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

import copy
import hashlib
import math
from dataclasses import dataclass
from datetime import timedelta
from string import Template

from . import languages, translate
from .brief_text import WEEKEND_TEXT, Language, WeekendText
from .languages import WEEKEND
from .actions import calendar as calendar_action, email as email_action, imessage
from .collectors import weekend_events as we
from .collectors.base import CalendarEvent
from .collectors.weekend_events import Candidate
from .config import Config
from .state import State
from .summarize import call_llm, call_llm_json

log = logging.getLogger(__name__)


SYSTEM_PROMPT = """You help families with kids in Finland (Greater Helsinki) pick weekend events. The kids' ages and interests are in kid_profiles and kid_preferences.

The user gives you:
- The kids' and parents' preferences (free text)
- **Feedback on last week's picks**: each entry in `last_week_feedback` has the title, keywords, area, the reason shown, and an `outcome` (kept|deleted|unknown).
  - `kept` = the family left it in the calendar (a weak positive signal: they may not have gone, but it was worth keeping)
  - `deleted` = the family deleted it (**a strong negative signal**: rank this kind of event, venue, keyword and time slot lower from now on)
  - `unknown` = not decided
- A list of this weekend's candidate events (from the Linked Events API, Helsinki, Espoo and Vantaa), each with title / start / price / locality / description / keywords / audience ages and so on

Your task:
1. Pick and **rank** the top N events strictly by the preferences (N is given in the user's instructions)
2. **Sports, outdoor, hands-on and children's theatre must make up the bulk (at least 70%)**
3. **At most 2 arts or music events in total**: a hard cap, so parents can broaden the kids' interests now and then
4. **Use last_week_feedback**: avoid the kinds, keywords, time slots and venues that were deleted; treat kept ones as a positive signal
5. Leave out events clearly meant for adults, late at night, or needing fluent Finnish (unless it is a sport the kids like that works across languages)
6. Spread them out geographically; don't pack too much into one day
7. Give each pick a one-sentence reason why it suits this family

Return only one JSON object, with this schema:
{
  "picks": [
    {
      "ext_id": "<the original event id>",
      "rank": 1,
      "why": "one-sentence reason, in {language_name}"
    },
    ...
  ]
}

- Copy ext_id exactly from the input, so the pick links back to its record
- No markdown fences, no extra explanation
"""


def _text(cfg: Config, language: Language) -> WeekendText:
    """Weekend Picks' own text in `language`, or English until its translation has worked."""
    return languages.own(cfg, language, WEEKEND) or WEEKEND_TEXT["en"]


def _when(c: Candidate, t: WeekendText, weekdays: tuple[str, ...]) -> str:
    start = c.start.astimezone()
    return t.when.format(weekday=weekdays[start.weekday()], day=start.day, month=start.month,
                         hour=start.hour, minute=start.minute)


def _price(c: Candidate, t: WeekendText) -> str:
    return t.free if c.is_free else (f"€{c.price_eur:g}" if c.price_eur is not None else t.price_on_page)


def _place(c: Candidate, t: WeekendText) -> str:
    region = t.locality.format(locality=c.locality) if c.locality else ""
    return (c.location_name or c.locality or t.place_unknown) + region


KID_HINT_KEYWORDS = {
    # Finnish
    "lapsi", "lapset", "lasten", "perhe", "perheen", "perheliikunta", "koulu",
    "leikki", "leikit", "liikunta", "urheilu", "pelit", "harrastus", "nuoret",
    # English/generic
    "family", "kids", "children", "youth", "sport", "sports", "play", "outdoor",
    # arts/music (still eligible; parents want some)
    "musiikki", "taide", "teatteri", "museo", "workshop", "työpaja",
}


def _has_kid_hint(c: Candidate) -> bool:
    hay = " ".join([c.title, c.description, " ".join(c.keywords)]).lower()
    return any(k in hay for k in KID_HINT_KEYWORDS)


def _trim(candidates: list[Candidate], cap: int) -> list[Candidate]:
    """Cap total events sent to LLM, favoring kid-hinted events with locality diversity."""
    if len(candidates) <= cap:
        return candidates
    # Score: kid-hint > age-appropriate > everything else
    def score(c: Candidate) -> int:
        s = 0
        if _has_kid_hint(c):
            s += 10
        if c.audience_min_age is not None and c.audience_min_age <= 12:
            s += 3
        if c.is_free:
            s += 1
        return s
    scored = sorted(candidates, key=score, reverse=True)
    # Take proportionally by locality, cap-per-locality to prevent Helsinki dominance
    per_locality_cap = max(cap // 3, 5)  # e.g. 100/3 → ~33 per city
    kept: list[Candidate] = []
    counts: dict[str, int] = {}
    for c in scored:
        if len(kept) >= cap:
            break
        loc = c.locality or "?"
        if counts.get(loc, 0) >= per_locality_cap:
            continue
        kept.append(c)
        counts[loc] = counts.get(loc, 0) + 1
    log.info("trim: %d → %d candidates for LLM (locality caps: %s)",
             len(candidates), len(kept), counts)
    return kept


def rank(cfg: Config, candidates: list[Candidate],
         feedback: list[dict] | None = None) -> list[dict[str, Any]]:
    """The picks, each with a reason in the first Weekend Picks Recipient's language. When the model
    fails, the first few candidates in order, marked `_ranking_failed`."""
    language = cfg.weekend_language()
    if not candidates:
        return []
    trimmed = _trim(candidates, cap=100)
    payload = {
        "kid_profiles": [
            {"name": k.name, "grade": k.grade, "class": k.class_name,
             "activities": k.activities}
            for k in cfg.kids
        ],
        "kid_preferences": cfg.weekend_events.kid_preferences,
        "parent_preferences": cfg.weekend_events.parent_preferences,
        "last_week_feedback": feedback or [],
        "max_picks": cfg.weekend_events.max_candidates,
        "candidates": [c.to_dict() for c in trimmed],
    }
    prompt = (
        f"From the {len(candidates)} weekend candidates below, pick and rank the top "
        f"{cfg.weekend_events.max_candidates}, and produce the JSON as the system instructions say.\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )
    try:
        # Not .format: the prompt's JSON schema is full of literal braces.
        system = SYSTEM_PROMPT.replace("{language_name}", languages.text(cfg, language).language_name)
        result = call_llm_json(cfg, prompt, system)
    except Exception as e:
        log.error("weekend rank LLM failed: %s", e)
        # graceful fallback: return first N by locality mix
        seen_loc: dict[str, int] = {}
        fallback: list[dict[str, Any]] = []
        for c in candidates:
            seen_loc[c.locality] = seen_loc.get(c.locality, 0) + 1
            if seen_loc[c.locality] > 4:
                continue
            fallback.append({"ext_id": c.ext_id,
                             "rank": len(fallback) + 1,
                             "why": _text(cfg, language).ranking_failed,
                             "_ranking_failed": True})
            if len(fallback) >= cfg.weekend_events.max_candidates:
                break
        return fallback

    return _ranked_picks(result.get("picks"), {c.ext_id for c in candidates})


def _numeric_rank(pick: Any) -> float | None:
    """The pick's rank as a number (the model writes 1 or "1"), or None when it isn't an object
    with one."""
    r = pick.get("rank") if isinstance(pick, dict) else None
    if isinstance(r, bool):
        return None
    try:
        n = float(r)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) else None


def _ranked_picks(picks: Any, ext_ids: set[str]) -> list[dict[str, Any]]:
    """The model's picks in rank order, leaving out any that isn't an object, has no rank or names
    an event that isn't among the candidates."""
    if not isinstance(picks, list):
        log.warning("weekend rank: the reply has no list of picks")
        return []
    ranked = [(n, p) for p in picks
              if (n := _numeric_rank(p)) is not None
              and isinstance(p.get("ext_id"), str) and p["ext_id"] in ext_ids]
    if len(ranked) < len(picks):
        log.warning("weekend rank: skipped %d picks without a rank or a known event", len(picks) - len(ranked))
    return [p for _, p in sorted(ranked, key=lambda np: np[0])]


def _weekend_str(weekend: tuple[Any, Any]) -> str:
    return f"{weekend[0].isoformat()} ~ {weekend[1].isoformat()}"


_PICKS_INTRO = Template("""You translate the reasons given for a Household's Weekend Picks, family events for the coming weekend in Finland picked for their kids, from $original into $target.

You receive one JSON object with the picks, each with its ext_id, its rank and a one-sentence reason (why).
""")
_PICKS_TRANSLATE = Template("Translate every why into $target")
_PICKS_KEEP_FINNISH = Template("Keep Finnish event, venue and place names as written, so parents can still find them")
_PICKS_KEEP = [
    Template("Keep names, dates, times and amounts as they are"),
    Template("Keep every ext_id and rank exactly as it is, and every pick in the same order: don't add, drop or "
             "reorder any"),
]


def _translated(cfg: Config, picks: list[dict], original: Language, target: Language) -> list[dict]:
    """`picks` with their reasons in `target`. Raises when the call fails or the reply doesn't carry
    the same picks in the same order; the caller then sends the original."""
    if not any(p.get("why") for p in picks):  # no reason written: nothing for the model to do
        return picks
    sent = {"picks": [{"ext_id": p.get("ext_id"), "rank": p.get("rank"), "why": p.get("why") or ""} for p in picks]}
    instructions = translate.instructions(_PICKS_INTRO, _PICKS_TRANSLATE, _PICKS_KEEP_FINNISH, _PICKS_KEEP,
                                          languages.text(cfg, original), languages.text(cfg, target), target)
    log.info("Translating Weekend Picks from %s into %s", original, target)
    reply = call_llm(cfg, json.dumps(sent, ensure_ascii=False, indent=2), instructions).data.get("picks")
    if not isinstance(reply, list):
        raise ValueError("the translation has no list of picks")
    def order(picks: list) -> list[tuple[str, str]]:
        return [(str(p.get("ext_id")), str(p.get("rank"))) for p in picks]  # 1 and "1" are the same rank
    try:
        same = order(reply) == order(sent["picks"])
    except AttributeError:  # a pick that isn't an object
        same = False
    if not same:
        raise ValueError("the translation doesn't carry the same picks")
    out = copy.deepcopy(picks)
    for p, r in zip(out, reply):
        if p.get("why"):
            if not isinstance(r.get("why"), str) or not r["why"].strip():
                raise ValueError("the translation has a pick without its reason")
            p["why"] = r["why"]
    return out


@dataclass
class _Version:
    """This week's Weekend Picks as the Recipients who read one language get them."""
    t: WeekendText
    weekdays: tuple[str, ...]
    to: list[str]
    picks: list[dict]
    translation_note: str = ""  # at the top of the original when the translation failed

    @classmethod
    def written_in(cls, cfg: Config, language: Language, to: list[str], picks: list[dict],
                   translation_note: str = "") -> "_Version":
        return cls(_text(cfg, language), languages.text(cfg, language).weekdays, to, picks, translation_note)


def _versions(cfg: Config, picks: list[dict]) -> list[_Version]:
    """One version per language among the Weekend Picks Recipients, the original first. The calendar
    and the archive keep the original only (ADR 0004)."""
    original = cfg.weekend_language()
    versions = []
    for language, to in (cfg.weekend_recipients_by_language() or {original: []}).items():
        if language == original:
            versions.append(_Version.written_in(cfg, language, to, picks))
        elif all(p.get("_ranking_failed") for p in picks):
            # The model is down, so there are no reasons to translate; the fallback's comes in their words.
            reason = _text(cfg, language).ranking_failed
            versions.append(_Version.written_in(cfg, language, to, [{**p, "why": reason} for p in picks]))
        else:
            try:
                versions.append(_Version.written_in(cfg, language, to, _translated(cfg, picks, original, language)))
            except Exception as e:
                log.error("Translating Weekend Picks into %s failed: %s (sending the original)", language, e)
                versions.append(_Version.written_in(cfg, original, to, picks,
                                                    translation_note=_text(cfg, language).translation_failed))
    return versions


def _format_body(candidates_by_id: dict[str, Candidate], weekend: tuple[Any, Any], v: _Version) -> str:
    t, weekdays, picks = v.t, v.weekdays, v.picks
    lines = [t.heading.format(weekend=_weekend_str(weekend)),
             *([v.translation_note, ""] if v.translation_note else []), t.intro, ""]
    for p in picks:
        c = candidates_by_id.get(p.get("ext_id"))
        if not c:
            continue
        when = _when(c, t, weekdays)
        price = _price(c, t)
        lines.append(f"{p.get('rank', '?')}. {c.title}")
        lines.append(f"   ⏰ {when}  📍 {_place(c, t)}  💰 {price}")
        why = (p.get("why") or "").strip()
        if why:
            lines.append(f"   💡 {why}")
        if c.url:
            lines.append(f"   🔗 {c.url}")
        lines.append("")
    return "\n".join(lines)


def _format_html(candidates_by_id: dict[str, Candidate], weekend: tuple[Any, Any], v: _Version) -> str:
    import html as _h
    t, weekdays, picks = v.t, v.weekdays, v.picks
    parts = [f"<h2>{_h.escape(t.heading.format(weekend=_weekend_str(weekend)))}</h2>",
             *([f"<p style='color:#a33'>{_h.escape(v.translation_note)}</p>"] if v.translation_note else []),
             f"<p>{t.intro_html}</p>",
             "<ol>"]
    for p in picks:
        c = candidates_by_id.get(p.get("ext_id"))
        if not c:
            continue
        when = _h.escape(_when(c, t, weekdays))
        price = _h.escape(_price(c, t))
        place = _h.escape(_place(c, t))
        title = _h.escape(c.title)
        link = _h.escape(c.url) if c.url else ""
        head = f'<a href="{link}">{title}</a>' if link else title
        why = _h.escape((p.get("why") or "").strip())
        parts.append("<li style='margin-bottom:12px'>")
        parts.append(f"<b>{head}</b><br>")
        parts.append(f"<span style='color:#666'>⏰ {when} · 📍 {place} · 💰 {price}</span>")
        if why:
            parts.append(f"<br>💡 {why}")
        parts.append("</li>")
    parts.append("</ol>")
    return "<html><body style='font-family:-apple-system,Helvetica,Arial;font-size:14px'>" \
           + "".join(parts) + "</body></html>"


def _write_picks_to_calendar(cfg: Config, state: State,
                             candidates_by_id: dict[str, Candidate],
                             picks: list[dict],
                             weekend: tuple) -> list[dict]:
    """Insert weekend picks as tentative Calendar events with rich metadata.

    Each event carries extendedProperties.private.{weekend_pick=1, weekend_saturday,
    pick_rank, why} so next week's feedback loop can query kept vs deleted.
    """
    if not calendar_action.is_configured():
        log.warning("Calendar not configured; skipping picks-to-calendar write")
        return []
    sat, _ = weekend
    events: list[tuple[CalendarEvent, dict]] = []  # each with its own pick, so feedback lands on the right event
    for p in picks:
        c = candidates_by_id.get(p.get("ext_id"))
        if not c:
            continue
        end = c.end or (c.start + timedelta(hours=2))
        ext_id = f"weekend-pick:{sat.isoformat()}:{c.ext_id}"
        description = ((p.get("why") or "").strip() +
                       ("\n\n" + c.description if c.description else "") +
                       ("\n\n" + c.url if c.url else "") +
                       "\n\n" + _text(cfg, cfg.weekend_language()).calendar_note)
        events.append((CalendarEvent(
            source="weekend_pick",
            external_id=ext_id,
            title=f"🎪 {c.title}",
            start=c.start,
            end=end,
            location=f"{c.location_name} ({c.locality})" if c.location_name and c.locality else (c.location_name or c.locality or None),
            description=description,
            kid=None,
        ), p))

    # Insert using existing create_events with an override to tag as weekend_pick
    # and status=tentative. Custom insert path below.
    import time as _t
    from googleapiclient.errors import HttpError
    svc = calendar_action._build_service()
    calendar_id = cfg.google_calendar.calendar_id
    created: list[dict] = []
    for ev, p in events:
        h = calendar_action.event_hash(ev)
        if calendar_action.already_created(state, ev):
            log.info("weekend pick already in state, skip: %s", ev.title)
            continue
        # remote-dedup
        existing = svc.events().list(
            calendarId=calendar_id,
            privateExtendedProperty=f"family_brief_hash={h}",
            maxResults=1,
        ).execute().get("items", [])
        if existing:
            state.mark_event_created(h, existing[0]["id"])
            continue
        body = {
            "summary": ev.title,
            "description": ev.description,
            "start": {"dateTime": ev.start.isoformat()},
            "end": {"dateTime": ev.end.isoformat()},
            "location": ev.location,
            "status": "tentative",
            "transparency": "transparent",  # doesn't block busy time
            "extendedProperties": {"private": {
                "family_brief": "1",
                "family_brief_hash": h,
                "source": "weekend_pick",
                "external_id": ev.external_id,
                "weekend_pick": "1",
                "weekend_saturday": (weekend[0]).isoformat(),
                "pick_rank": str(p.get("rank", "")),
                "candidate_ext_id": p.get("ext_id", ""),
            }},
            "reminders": {"useDefault": False},
        }
        body = {k: v for k, v in body.items() if v is not None}
        try:
            resp = svc.events().insert(
                calendarId=calendar_id,
                body=body,
                sendUpdates="none",  # weekend picks: don't spam attendees
            ).execute()
        except HttpError as e:
            log.error("failed to insert weekend pick %s: %s", ev.title, e)
            continue
        state.mark_event_created(h, resp["id"])
        created.append({"title": ev.title, "start": ev.start.isoformat(),
                        "google_event_id": resp["id"],
                        "htmlLink": resp.get("htmlLink")})
        _t.sleep(0.05)
    log.info("weekend picks: inserted %d tentative Calendar events", len(created))
    return created


def read_last_week_feedback(cfg: Config, weekend: tuple) -> list[dict]:
    """Look at last weekend's picks in Calendar: which are still there vs deleted.

    Returns feedback records the LLM can use next week to learn preferences.
    """
    if not calendar_action.is_configured():
        return []
    prev_sat = weekend[0] - timedelta(days=7)
    picks_dir = cfg.weekend_events.resolved_dir()
    picks_file = picks_dir / f"{prev_sat.isoformat()}.json"
    if not picks_file.exists():
        return []
    import json as _json
    data = _json.loads(picks_file.read_text())
    prev_picks = data.get("picks") or []
    prev_candidates = {c["ext_id"]: c for c in data.get("candidates") or []}

    svc = calendar_action._build_service()
    calendar_id = cfg.google_calendar.calendar_id
    # Query all events tagged with that Saturday's weekend picks
    resp = svc.events().list(
        calendarId=calendar_id,
        privateExtendedProperty=f"weekend_saturday={prev_sat.isoformat()}",
        showDeleted=True,
        maxResults=100,
    ).execute()
    by_cand: dict[str, dict] = {}
    for ev in resp.get("items", []):
        priv = (ev.get("extendedProperties") or {}).get("private") or {}
        cand_id = priv.get("candidate_ext_id")
        if not cand_id:
            continue
        by_cand[cand_id] = {"status": ev.get("status"), "summary": ev.get("summary")}

    feedback: list[dict] = []
    for p in prev_picks:
        cand_id = p.get("ext_id")
        cand = prev_candidates.get(cand_id, {})
        row = by_cand.get(cand_id)
        if row is None:
            outcome = "unknown"
        elif row.get("status") == "cancelled":
            outcome = "deleted"
        else:
            outcome = "kept"
        feedback.append({
            "title": cand.get("title") or p.get("title"),
            "locality": cand.get("locality"),
            "keywords": cand.get("keywords"),
            "why_shown": p.get("why"),
            "outcome": outcome,
        })
    log.info("feedback from %s: %d picks, kept=%d deleted=%d unknown=%d",
             prev_sat.isoformat(), len(feedback),
             sum(1 for f in feedback if f["outcome"] == "kept"),
             sum(1 for f in feedback if f["outcome"] == "deleted"),
             sum(1 for f in feedback if f["outcome"] == "unknown"))
    return feedback


def _archive(cfg: Config, weekend, candidates: list[Candidate],
             picks: list[dict], body: str) -> None:
    d = cfg.weekend_events.resolved_dir()
    d.mkdir(parents=True, exist_ok=True)
    sat, _ = weekend
    (d / f"{sat.isoformat()}.json").write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "weekend": [weekend[0].isoformat(), weekend[1].isoformat()],
        "candidates_total": len(candidates),
        "candidates": [c.to_dict() for c in candidates],
        "picks": picks,
    }, ensure_ascii=False, indent=2))
    (d / f"{sat.isoformat()}.md").write_text(body)


def run(cfg: Config, dry_run: bool = False) -> int:
    if not cfg.weekend_events.enabled:
        log.info("weekend_events disabled in config; skipping")
        return 0

    weekend = we._weekend_range()
    state = State(cfg.resolved_state_path())

    # 1. Feedback loop: read last week's Calendar kept-vs-deleted signal
    try:
        feedback = read_last_week_feedback(cfg, weekend)
    except Exception as e:
        log.warning("could not read last week feedback (continuing): %s", e)
        feedback = []

    # 2. Fetch + rank
    candidates = we.collect(cfg)
    if not candidates:
        log.info("no weekend candidates matched; nothing to send")
        return 0
    if not dry_run:  # a dry run translates nothing, so it uses the text already there
        languages.prepare_each(cfg, cfg.weekend_languages(), languages.TABLES)
    picks = rank(cfg, candidates, feedback=feedback)
    by_id = {c.ext_id: c for c in candidates}
    body = _format_body(by_id, weekend, _Version.written_in(cfg, cfg.weekend_language(), [], picks))

    _archive(cfg, weekend, candidates, picks, body)

    # 3. Write picks to Calendar as tentative events (feedback signal for next week)
    if dry_run:
        log.info("DRY-RUN: would write %d picks to Calendar", len(picks))
    else:
        try:
            _write_picks_to_calendar(cfg, state, by_id, picks, weekend)
            state.save()
        except Exception as e:
            log.error("weekend picks → Calendar failed: %s", e)

    # 4. Deliver
    if dry_run:
        log.info("DRY-RUN: preview:\n%s", body)
        return 0

    delivered = False
    if cfg.email.enabled:
        for v in _versions(cfg, picks):
            try:
                email_action.send(
                    subject=v.t.subject.format(weekend=_weekend_str(weekend)),
                    body_text=_format_body(by_id, weekend, v),
                    body_html=_format_html(by_id, weekend, v),
                    from_addr=cfg.email.from_addr or cfg.gmail.username or "",
                    to_addrs=v.to,
                )
                delivered = True
            except Exception as e:
                log.error("email delivery failed: %s", e)
    if cfg.imessage.enabled and cfg.weekend_events.recipients:
        try:
            imessage.send(cfg.weekend_events.recipients, body)
            delivered = True
        except Exception as e:
            log.error("imessage delivery failed: %s", e)
    if not delivered:
        log.error("no delivery channel succeeded")
    return 0
