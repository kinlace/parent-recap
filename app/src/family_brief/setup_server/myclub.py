"""MyClub's connect step: a calendar link for each of a Kid's clubs (#810), and for a Household
without Wilma, the Kids by the names the family calls them."""
from __future__ import annotations

from http import HTTPStatus
from typing import Any

from .. import setup_save, sources
from ..setup_connect import Nothing, Reply, Strict, parse
from .context import Action, Phase, add_kid

# MyClub's code is in its folder (ADR 0051); this page's step reaches it through its record until
# the page is split by Source.
setup_myclub = sources.MYCLUB.code("setup")
myclub_links = sources.MYCLUB.code("config").myclub_links

# What saving a Kid's MyClub calendar link can say, as `setup myclub` does. Adding a Kid by the
# name the family calls them, for a Household without Wilma, says one of context.KID_RESULTS.
MYCLUB_RESULTS = ("saved", "not-a-myclub-link", "link-failed", "not-a-calendar", "save-failed",
                  "same-link", "already-saved")
# What removing one of a Kid's links can say.
REMOVE_RESULTS = ("removed", "no-such-link", "remove-failed")


class MyClubLink(Strict):
    kid: str
    link: str  # a secret (ADR 0007)


class LinkToRemove(Strict):
    kid: str
    index: int  # which of the Kid's links, from 0, as the state counts them


class KidName(Strict):
    name: str


class MyClub(Phase):
    def routes(self) -> dict[str, Action]:
        return {"api/myclub": self.save_myclub_link, "api/myclub/remove": self.remove_myclub_link,
                "api/myclub/kid": self.add_kid, "api/myclub/done": self.myclub_done}

    def state(self, progress: dict[str, Any]) -> dict[str, Any]:
        return {"myclub": self._myclub()}

    def save_myclub_link(self, raw: Any) -> Reply:
        """Checks the calendar link pasted into the page's own field for one of the Household's
        Kids and saves it beside the links they have, for another club (#810), as the chat's
        MyClub step saves one. Once a link is saved, MyClub is done, and setup moves on once every
        Kid has one, unless it was another club's; until then the parent can add the next Kid's
        or another club's.
        The link is a secret (ADR 0007): it's never returned, logged or put on a command line."""
        answer = parse(MyClubLink, raw)
        if answer.kid not in self._kid_names():  # named only: the answers carry the link
            return HTTPStatus.BAD_REQUEST, setup_save.outcome(
                "invalid-answers", "Pick one of the Kids.", errors=["kid: should be a Kid's name"])
        connect = self.context.connect
        another = any(k["linked"] for k in self._myclub()["kids"] if k["name"] == answer.kid)
        # Downloaded before the lock, so a slow MyClub holds up no other save.
        result, extra = setup_myclub.save_link(self.context.config, answer.kid, answer.link,
                                               connect.lock, another=True)
        if result != "saved":
            return HTTPStatus.OK, {"result": result}  # without the error, which can name files
        # Setup moves on by itself once every Kid has their link, but not from another club's
        # link: the parent may have more to add, and presses Continue.
        return connect.complete(
            "myclub", move_on=lambda progress: progress["source"] == "myclub" and not another
            and all(k["linked"] for k in self._myclub()["kids"]),
            reply={"kid": answer.kid, "events": extra["events"], "kids": self._myclub()["kids"]})

    def remove_myclub_link(self, raw: Any) -> Reply:
        """Removes one of a Kid's links, by its place among them, since the page never has the
        links themselves (ADR 0007)."""
        answer = parse(LinkToRemove, raw)
        if answer.kid not in self._kid_names():
            return HTTPStatus.BAD_REQUEST, setup_save.outcome(
                "invalid-answers", "Pick one of the Kids.", errors=["kid: should be a Kid's name"])
        result = setup_myclub.remove_link(self.context.config, answer.kid, answer.index,
                                          self.context.connect.lock)
        return HTTPStatus.OK, {"result": result, "kids": self._myclub()["kids"]}

    def add_kid(self, raw: Any) -> Reply:
        """For a Household without Wilma: adds a Kid by the name the family calls them, so their
        MyClub link can be saved. With Wilma, the Kids are the ones Wilma lists."""
        status, out = add_kid(self.context, parse(KidName, raw).name)
        if out.get("result") != "added":
            return status, out
        return status, {**out, "kids": self._myclub()["kids"]}

    def myclub_done(self, raw: Any) -> Reply:
        """Moves setup on from MyClub once a link is saved, leaving the other Kids without one."""
        parse(Nothing, raw)
        return self.context.connect.move_on("myclub", setup_save.outcome(
            "invalid-answers", "Save a Kid's link first, or skip MyClub.",
            errors=["answers: should be {}, once a link is saved"]))

    def _myclub(self) -> dict[str, Any]:
        """The Kids known so far, each with whether a link is saved and how many, but never a
        link, and whether the family adds them here: only without Wilma."""
        data = self.context.data()
        kids = [k for k in data.get("kids") or []
                if isinstance(k, dict) and isinstance(k.get("name"), str)]
        counts = [len(myclub_links(k.get("myclub_ical_url"))) for k in kids]
        return {"kids": [{"name": k["name"], "linked": n > 0, "links": n}
                         for k, n in zip(kids, counts)],
                "add": self.context.adds_kids()}

    def _kid_names(self) -> list[str]:
        return [k["name"] for k in self._myclub()["kids"]]
