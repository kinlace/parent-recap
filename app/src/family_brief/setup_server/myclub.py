"""MyClub's connect step: a calendar link for each Kid, and for a Household without Wilma, the
Kids by the names the family calls them."""
from __future__ import annotations

from http import HTTPStatus
from typing import Any

from .. import setup_myclub, setup_save
from ..setup_connect import Nothing, Reply, Strict, parse
from .context import Action, Phase

# What saving a Kid's MyClub calendar link can say, as `setup myclub` does, and adding a Kid by
# the name the family calls them, for a Household without Wilma.
MYCLUB_RESULTS = ("saved", "not-a-myclub-link", "link-failed", "not-a-calendar", "save-failed",
                  "same-link")
MYCLUB_KID_RESULTS = ("added", "kid-exists")


class MyClubLink(Strict):
    kid: str
    link: str  # a secret (ADR 0007)


class KidName(Strict):
    name: str


class MyClub(Phase):
    def routes(self) -> dict[str, Action]:
        return {"api/myclub": self.save_myclub_link, "api/myclub/kid": self.add_kid,
                "api/myclub/done": self.myclub_done}

    def state(self, progress: dict[str, Any]) -> dict[str, Any]:
        return {"myclub": self._myclub()}

    def save_myclub_link(self, raw: Any) -> Reply:
        """Checks the calendar link pasted into the page's own field for one of the Household's
        Kids and saves it, through the chat's MyClub step. Once a link is saved, MyClub is done,
        and setup moves on once every Kid has one; until then the parent can add the next Kid's.
        The link is a secret (ADR 0007): it's never returned, logged or put on a command line."""
        answer = parse(MyClubLink, raw)
        if answer.kid not in self._kid_names():  # named only: the answers carry the link
            return HTTPStatus.BAD_REQUEST, setup_save.outcome(
                "invalid-answers", "Pick one of the Kids.", errors=["kid: should be a Kid's name"])
        connect = self.context.connect
        # Downloaded before the lock, so a slow MyClub holds up no other save.
        result, extra = setup_myclub.save_link(self.context.config, answer.kid, answer.link,
                                               connect.lock)
        if result != "saved":
            return HTTPStatus.OK, {"result": result}  # without the error, which can name files
        # Setup moves on by itself once every Kid has their link.
        return connect.complete(
            "myclub", move_on=lambda progress: progress["source"] == "myclub"
            and all(k["linked"] for k in self._myclub()["kids"]),
            reply={"kid": answer.kid, "events": extra["events"], "kids": self._myclub()["kids"]})

    def add_kid(self, raw: Any) -> Reply:
        """For a Household without Wilma: adds a Kid by the name the family calls them, so their
        MyClub link can be saved. With Wilma, the Kids are the ones Wilma lists."""
        name = parse(KidName, raw).name.strip()
        if not (name and self._myclub()["add"]):
            return HTTPStatus.BAD_REQUEST, setup_save.outcome(
                "invalid-answers", "Type the Kid's name. A Household with Wilma has its Kids from "
                "there.", errors=["name: should be text, for a Household without Wilma"])
        connect = self.context.connect
        with connect.lock:
            names = self._kid_names()
            if name.casefold() in (n.casefold() for n in names):
                return HTTPStatus.OK, {"result": "kid-exists"}
            # Only the names: each Kid already there keeps the rest, such as their link.
            status, out = connect.save({"kids": [{"name": n} for n in [*names, name]]})
        if status != HTTPStatus.OK:
            return status, out
        return status, {**out, "result": "added", "kids": self._myclub()["kids"]}

    def myclub_done(self, raw: Any) -> Reply:
        """Moves setup on from MyClub once a link is saved, leaving the other Kids without one."""
        parse(Nothing, raw)
        return self.context.connect.move_on("myclub", setup_save.outcome(
            "invalid-answers", "Save a Kid's link first, or skip MyClub.",
            errors=["answers: should be {}, once a link is saved"]))

    def _myclub(self) -> dict[str, Any]:
        """The Kids known so far, each with whether their link is saved but never the link, and
        whether the family adds them here: only without Wilma."""
        data = self.context.data()
        kids = [k for k in data.get("kids") or []
                if isinstance(k, dict) and isinstance(k.get("name"), str)]
        return {"kids": [{"name": k["name"], "linked": bool(k.get("myclub_ical_url"))}
                         for k in kids],
                "add": self.context.section("wilma").get("enabled") is not True}

    def _kid_names(self) -> list[str]:
        return [k["name"] for k in self._myclub()["kids"]]
