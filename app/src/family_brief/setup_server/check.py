"""Working and Check: reading the Gmail senders once every Source is done or skipped, then the
check page, each list ticked by best guess, whose confirmation starts the health check."""
from __future__ import annotations

import threading
from http import HTTPStatus
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from .. import ops, setup_connect, setup_gmail, setup_save
from ..config import Config, Kid
from . import health
from .context import Action, Context, Phase, Reply, nothing_to_give

# Working: reading the Gmail senders for the check page, how far it is and how it ended, and
# `no-read` when this server hasn't started one. Wilma's Kids and WhatsApp's groups were read in
# Connect already.
WORKING_RESULTS = ("reading", "read", "read-failed", "no-read")
SENDER_DAYS = 60


class KidCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str            # as Wilma spells it, or the everyday name for a Kid added on the page
    everyday_name: str


class CheckAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kids: list[KidCheck]                  # the Kids ticked
    whatsapp: list[str] | None            # the groups ticked; None without WhatsApp
    senders: list[str]                    # the sender domains ticked
    recipients: list[setup_save.RecipientAnswer]
    evening: str                          # HH:MM
    household_label: str | None = None    # a pilot Household's; None for any other


class Check(Phase):
    def __init__(self, context: Context) -> None:
        super().__init__(context)
        self._reading: dict[str, Any] | None = None  # Working's read of the senders, once started

    def routes(self) -> dict[str, Action]:
        return {"api/working": self.start_reading, "api/working/check": self.check_reading,
                "api/check": self.confirm_check}

    def state(self, progress: dict[str, Any]) -> dict[str, Any]:
        return {"check": self._check(progress)}

    # ── Working

    def start_reading(self, raw: Any) -> Reply:
        """Once every Source is done or skipped, moves setup on to Working and reads the Gmail
        senders in the background, as `discover gmail-senders --json` does: the page asks how far
        it is with `check_reading`, and once they're read setup moves on to Check by itself."""
        context = self.context
        with context.connect.lock:
            progress = context.progress()
            if raw != {} or progress["phase"] not in ("connect", "working") \
                    or setup_connect.next_source(progress["sources"]) is not None:
                return HTTPStatus.BAD_REQUEST, setup_save.outcome(
                    "invalid-answers", "Connect each Source, or skip it, first.",
                    errors=["answers: should be {}, once every Source is done or skipped"])
            out = setup_save.save(context.config, {"progress": {"phase": "working"}})
        if out["result"] != "saved":
            return HTTPStatus.CONFLICT, out
        with context.working:
            if self._reading is None or self._reading["result"] != "reading":
                self._reading = {"result": "reading", "read": 0, "total": None}
                threading.Thread(target=self._read_senders, daemon=True).start()
        return HTTPStatus.OK, {**out, "result": "reading"}

    def check_reading(self, raw: Any) -> Reply:
        """How far reading the senders is: `reading`, with how many of how many are read."""
        if raw != {}:
            return HTTPStatus.BAD_REQUEST, nothing_to_give()
        return HTTPStatus.OK, self._reading or {"result": "no-read"}

    def _read_senders(self) -> None:
        def read(done: int, total: int) -> None:
            self._reading = {"result": "reading", "read": done, "total": total}
        try:
            senders = ops.gmail_senders(Config.load(self.context.config), SENDER_DAYS, progress=read)
            with self.context.connect.lock:
                out = setup_save.save(self.context.config, {"progress": {
                    "phase": "check", "gmail_senders": senders}})
        except Exception:  # its error can name the address: never leave the page reading
            out = {"result": "failed"}
        self._reading = {"result": "read", "progress": out["progress"]} \
            if out["result"] == "saved" else {"result": "read-failed"}

    # ── Check

    def _check(self, progress: dict[str, Any]) -> dict[str, Any]:
        """What the check page shows, each list ticked by best guess: the Kids, all ticked with
        their everyday names; the WhatsApp groups, those linked to a Kid ticked (none without
        WhatsApp); the Gmail senders that look like school, city or club mail ticked, and never
        a public one; each Recipient with their language; the evening time; and for a pilot
        Household, the label its feedback goes under (None for any other). `other_sources` is
        whether a Source besides Gmail brings news, so that a Gmail with no sender ticked only
        sends the Brief (#361)."""
        cfg = self.context.model()
        hour, minute = cfg.schedule.daily_hour, cfg.schedule.daily_minute
        groups = _groups(cfg, progress) if progress["sources"]["whatsapp"] == "done" else None
        # The WhatsApp groups are picked on this page, so it's the ticked ones that count.
        others = any(s != "whatsapp" for s in cfg.other_sources()) \
            or any(g["ticked"] for g in groups or [])
        return {"kids": [{"name": k.name, "everyday_name": everyday_name(k)} for k in cfg.kids],
                "whatsapp": groups,
                "senders": _senders(cfg, progress),
                "other_sources": others,
                "recipients": [{"address": r.address, "language": cfg.language_of(r)}
                               for r in cfg.email.to],
                "evening": f"{hour:02d}:{minute:02d}",
                "feedback": {"household_label": cfg.feedback.household_label}
                if cfg.feedback.enabled else None}

    def confirm_check(self, raw: Any) -> Reply:
        """Saves what the check page confirms through `setup save`, then runs the health check in
        the background: the page asks how it went with `check_health`. Only what the page offered
        can be picked."""
        try:
            answer = CheckAnswer.model_validate(raw)
        except ValidationError as e:
            return HTTPStatus.BAD_REQUEST, setup_save.invalid(e)
        context = self.context
        with context.working:
            if context.runs.get("health", {}).get("result") == "checking":
                return HTTPStatus.CONFLICT, {"result": "checking"}
            with context.connect.lock:
                progress = context.progress()
                cfg = context.model()
                offered = self._check(progress)
                errors = _check_errors(answer, offered, progress["phase"])
                if errors:
                    return HTTPStatus.BAD_REQUEST, setup_save.outcome(
                        "invalid-answers", "Pick from what the check page offers.", errors=errors)
                out = setup_save.save(context.config, _check_answers(answer, cfg, progress))
            if out["result"] != "saved":
                return (HTTPStatus.BAD_REQUEST if out["result"] == "invalid-answers"
                        else HTTPStatus.CONFLICT), out
            context.runs["health"] = {"result": "checking"}
            threading.Thread(target=health.run, args=(context,), daemon=True).start()
        return HTTPStatus.OK, {**out, "result": "checking"}


def _groups(cfg: Config, progress: dict[str, Any]) -> list[dict[str, Any]]:
    """The groups WhatsApp's step found, then those picked before but not found this time."""
    called = {k.name: everyday_name(k) for k in cfg.kids}
    picked = {c.name: c for c in cfg.whatsapp.chats}
    groups = []
    for chat in progress.get("whatsapp_chats") or []:
        kids = [k for k in (chat.get("hint") or {}).get("kids", []) if k in called]
        groups.append({"name": chat["name"], "last": chat.get("last"),
                       "archived": bool(chat.get("archived")), "kids": [called[k] for k in kids],
                       "ticked": bool(kids) or chat["name"] in picked})
    found = {g["name"] for g in groups}
    for chat in cfg.whatsapp.chats:
        if chat.name not in found:
            kids = list(called) if chat.kid == "both" else [k for k in called if k == chat.kid]
            groups.append({"name": chat.name, "last": None, "archived": False,
                           "kids": [called[k] for k in kids], "ticked": True})
    return groups


def _senders(cfg: Config, progress: dict[str, Any]) -> list[dict[str, Any]]:
    """The senders Working found, but no public mail service, then the city's starting
    allowlist and the domains already on the allowlist, ticked."""
    allowed = cfg.gmail.allowlist_domains
    senders = [{"domain": s["domain"], "count": s["count"], "example": s["example"],
                "ticked": s["likely"] or s["domain"] in allowed}
               for s in progress.get("gmail_senders") or [] if not s.get("public")]
    # Only a preset's: a town's guessed domain is ticked only once mail came from it.
    city = setup_gmail.CITY_DOMAINS.get(cfg.city or "")
    for domain in [*([city] if city else []), *allowed]:
        if domain not in (s["domain"] for s in senders):
            senders.append({"domain": domain, "count": 0, "example": "", "ticked": True})
    return senders


def _check_answers(answer: CheckAnswer, cfg: Config, progress: dict[str, Any]) -> dict[str, Any]:
    """The check page's answers as `setup save` takes them. A WhatsApp group is linked to the
    Kids its name matched who are still ticked, or to `both`, and labelled after what matched:
    the class, the school or an activity."""
    kept = {k.name.strip() for k in answer.kids}
    answers: dict[str, Any] = {
        "kids": [{"name": k.name.strip(), "everyday_name": k.everyday_name.strip()}
                 for k in answer.kids],
        "recipients": [{"address": r.address,
                        **({"language": r.language} if r.language != cfg.summary_language
                           else {})} for r in answer.recipients],
        "evening": answer.evening,
        "sources": {"gmail": {"allowlist_domains": answer.senders}},
    }
    if answer.household_label is not None:
        answers["feedback"] = {"household_label": answer.household_label.strip()}
    if answer.whatsapp is not None:
        found = {c["name"]: c for c in progress.get("whatsapp_chats") or []}
        before = {c.name: c for c in cfg.whatsapp.chats}
        chats = []
        for name in answer.whatsapp:
            if name in found:
                hint = found[name].get("hint") or {}
                kids = [k for k in hint.get("kids", []) if k in kept]
                label = next((_label(term, k) for term in hint.get("matched", [])
                              for k in cfg.kids if k.name in kept and _label(term, k)), None)
            else:
                chat = before[name]
                kids = [chat.kid] if chat.kid in kept or chat.kid == "both" else []
                label = chat.label
            chats.append({"name": name, "label": label,
                          "kid": kids[0] if len(kids) == 1 else ("both" if kids else None)})
        answers["sources"]["whatsapp"] = {"enabled": bool(chats), "chats": chats}
    return answers


def everyday_name(kid: Kid) -> str:
    """What the family calls the Kid: the name saved for it, else the first word of their name."""
    return kid.everyday_name or (kid.name.split() or [kid.name])[0]


def _check_errors(answer: CheckAnswer, offered: dict[str, Any], phase: str) -> list[str]:
    """Where the check page's answers pick something it didn't offer."""
    errors = []
    if phase != "check":
        errors.append("answers: setup isn't at Check")
    names = [k.name.strip().casefold() for k in answer.kids]
    if not names or not all(names) or len(set(names)) < len(names) \
            or not all(k.everyday_name.strip() for k in answer.kids):
        errors.append("kids: should be one or more Kids, each with a name of their own and an "
                      "everyday name")
    groups = offered["whatsapp"]
    if (answer.whatsapp is None) != (groups is None) or \
            not set(answer.whatsapp or []) <= {g["name"] for g in groups or []}:
        errors.append("whatsapp: should be groups the check page lists, or null without WhatsApp")
    if not set(answer.senders) <= {s["domain"] for s in offered["senders"]}:
        errors.append("senders: should be sender domains the check page lists")
    if [r.address for r in answer.recipients] != [r["address"] for r in offered["recipients"]]:
        errors.append("recipients: should be the Recipients the check page lists, in order")
    label = answer.household_label
    if label is not None and (offered["feedback"] is None or not label.strip()):
        errors.append("household_label: should be a name, and only for a pilot Household")
    return errors


def _label(term: str, kid: Kid) -> str | None:
    """What a WhatsApp group whose name matched `term` is about, for `kid`."""
    if term == kid.class_name:
        return "class"
    if term == kid.school:
        return "school"
    return term if term in kid.activities else None
