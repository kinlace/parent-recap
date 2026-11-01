"""Wilma's connect step (ADR 0008): the pinned CLI, the town search, the sign-in in the page's own
fields or, when that fails, in a Terminal window, and the Wilmas signed in to, which a family with
Kids at schools in two towns adds another to or removes one of (#647)."""
from __future__ import annotations

import threading
from http import HTTPStatus
from typing import Any
from urllib.parse import urlparse

from pydantic import Field

from .. import failure, install_record, setup_save, setup_wilma
from ..setup_connect import Reply, Strict, parse
from .context import Action, Context, Phase, nothing_to_give

# What signing in to Wilma on the page can say. Only `sign-in-failed` offers the Terminal sign-in
# window: a wrong password is said to be just that.
WILMA_RESULTS = ("signed-in", "wrong-password", "sign-in-failed", "no-kids", "not-installed")
# What the town search can say when it finds nothing to show: no CLI, or a CLI without the list.
TOWN_RESULTS = ("not-installed", "no-list")
# Getting the pinned wilma CLI ready, which the town list comes with: while it installs, and why
# it couldn't be.
WILMA_READY_RESULTS = ("installing", "no-node", "install-failed")
# The Terminal sign-in window: while the family signs in there, and how it ended if not signed in.
WILMA_WINDOW_RESULTS = ("waiting", "not-signed-in", "sign-in-failed", "timeout", "no-terminal",
                        "not-installed")


class WilmaRemoval(Strict):
    id: str  # the CLI's profile id, as `api/state` lists it


class WilmaSignIn(Strict):
    url: str
    town: str | None  # None for a Wilma whose list entry names no town
    username: str = Field(pattern=r"\S")
    password: str = Field(min_length=1)


class Wilma(Phase):
    def __init__(self, context: Context) -> None:
        super().__init__(context)
        self._lock = threading.Lock()  # one Wilma sign-in at a time, since each writes its profile
        self._window: dict[str, Any] | None = None  # the Terminal sign-in window's, once opened

    def routes(self) -> dict[str, Action]:
        return {"api/wilma/install": self.wilma_ready, "api/towns": self.find_towns,
                "api/wilma": self.sign_in_wilma,
                "api/wilma/terminal": self.open_wilma_window,
                "api/wilma/check": self.check_wilma_window,
                "api/wilma/remove": self.remove_wilma}

    def state(self, progress: dict[str, Any]) -> dict[str, Any]:
        return {"wilma": {"signins": signins()}}

    def wilma_ready(self, raw: Any) -> Reply:
        """Installs the pinned wilma CLI unless it's there, since the town list comes with it. A
        failed install says why, for the page to show."""
        if raw != {}:
            return HTTPStatus.BAD_REQUEST, nothing_to_give()
        return HTTPStatus.OK, setup_wilma.install()

    def find_towns(self, raw: Any) -> Reply:
        """The entries of Wilma's list that the town or name typed finds, each with its town."""
        if not (isinstance(raw, dict) and set(raw) == {"query"} and isinstance(raw["query"], str)):
            return HTTPStatus.BAD_REQUEST, setup_save.outcome(
                "invalid-answers", "Type a town.", errors=["query: should be text"])
        if not setup_wilma.installed():
            return HTTPStatus.OK, {"result": "not-installed"}
        listed = setup_wilma.tenants()
        if listed is None:
            return HTTPStatus.OK, {"result": "no-list"}
        return HTTPStatus.OK, {"result": "found", "towns": setup_wilma.search(listed, raw["query"])}

    def sign_in_wilma(self, raw: Any) -> Reply:
        """Signs in to the Wilma picked from the list with the username and the password typed
        into the page's own fields, by writing the CLI's profile and reading the Kids with it.
        Once signed in, Wilma is done, and the Kids, as Wilma spells them, and the town are the
        Household's. The password is never returned, logged or put on a command line."""
        answer = parse(WilmaSignIn, raw)
        if not setup_wilma.installed():
            return HTTPStatus.OK, {"result": "not-installed"}
        listed = setup_wilma.tenants()
        if listed is None:
            return HTTPStatus.OK, {"result": "no-list"}
        tenant = _listed_tenant(listed, answer.url, answer.town)
        if tenant is None:
            return HTTPStatus.BAD_REQUEST, _wilma_invalid()
        with self._lock:
            out = setup_wilma.sign_in(tenant, answer.username.strip(), answer.password)
        if out["result"] != "signed-in":
            return HTTPStatus.OK, out
        return self._signed_in(out["profile"], out["kids"], answer.town)

    def open_wilma_window(self, raw: Any) -> Reply:
        """Opens the Terminal sign-in window (#74), for when the page's own sign-in fails for any
        reason but a wrong password, and waits for it in the background: the page asks how it
        went with `check_wilma_window`. `town` is the one picked on the page, if any."""
        if not (isinstance(raw, dict) and set(raw) == {"town"}
                and (raw["town"] is None or isinstance(raw["town"], str))):
            return HTTPStatus.BAD_REQUEST, setup_save.outcome(
                "invalid-answers", "Give the town picked, or null.",
                errors=["town: should be text or null"])
        with self._lock:
            if self._window is not None and self._window["result"] is None:
                return HTTPStatus.OK, {"result": "waiting"}  # the window is already open
            window: dict[str, Any] = {"result": None}
            self._window = window
        threading.Thread(target=self._wait_for_window, daemon=True,
                         args=(window, self.context.chosen_language() or "en", raw["town"])).start()
        return HTTPStatus.OK, {"result": "waiting"}

    def check_wilma_window(self, raw: Any) -> Reply:
        """How the Terminal sign-in window went: `waiting` while the family is still in it."""
        if raw != {}:
            return HTTPStatus.BAD_REQUEST, nothing_to_give()
        window = self._window
        if window is None:
            return HTTPStatus.OK, {"result": "no-window"}
        return HTTPStatus.OK, window["result"] or {"result": "waiting"}

    def _wait_for_window(self, window: dict[str, Any], language: str, town: str | None) -> None:
        try:
            out = setup_wilma.sign_in_in_terminal(language, self.context.timeouts.wilma_window)
            if out["result"] == "signed-in":
                kids = [k for k in out["kids"] if isinstance(k.get("name"), str) and k["name"]]
                signed_in = setup_wilma.last_profile_id(setup_wilma.config_path())
                _, done = self._signed_in(signed_in, kids, _city(out.get("wilma_address"), town))
            else:  # with why, where it failed
                done = {k: v for k, v in out.items() if k in ("result", "reason")}
        except Exception as e:  # never leave the page waiting
            done = {"result": "sign-in-failed", "reason": failure.logged(
                "Wilma didn't sign in", failure.raised(e, "The Wilma window"))}
        window["result"] = done

    def remove_wilma(self, raw: Any) -> Reply:
        """Removes one of the Wilmas signed in to, with its password, as uninstall does, and the
        Kids only it lists, keeping the others working. The last one isn't removed here: a family
        without Wilma skips it instead."""
        answer = parse(WilmaRemoval, raw)
        with self._lock:
            path = setup_wilma.config_path()
            removed = setup_wilma.profile(path, answer.id)
            if removed is None or len(setup_wilma.profiles(path)) < 2:
                return HTTPStatus.BAD_REQUEST, setup_save.outcome(
                    "invalid-answers", "Remove one of the Wilmas listed, while there are two or "
                    "more.", errors=["id: should be one of the Wilmas api/state lists, while it "
                                     "lists two or more"])
            setup_wilma.remove_profile(path, answer.id)
            install_record.remove("wilma-profile", answer.id)
            left = setup_wilma.profiles(path)
            still = {n.casefold() for p in left for n in setup_wilma.student_names(p)}
            gone = {n.casefold() for n in setup_wilma.student_names(removed)} - still
            answers: dict[str, Any] = {"kids": [{"name": k.name} for k in self.context.model().kids
                                                if k.name.casefold() not in gone]}
            # The Household's town was the removed Wilma's: it becomes the next one's.
            if self.context.data().get("city") in _towns(removed) and \
                    (town := _city(_host(left[0]), None)):
                answers["city"] = town
            status, out = self.context.connect.save(answers)
        if status != HTTPStatus.OK:
            return status, out
        return status, {**out, "result": "removed", "signins": signins()}

    def _signed_in(self, profile_id: str | None, kids: list[dict[str, Any]],
                   city: str | None) -> Reply:
        """Saves the Kids Wilma lists and the town, with Wilma on and done. The Kids of the other
        Wilmas signed in to stay, so a second Wilma adds its Kids to the first's, and the
        Household's town stays the first Wilma's."""
        others = [p for p in setup_wilma.profiles(setup_wilma.config_path())
                  if p.get("id") != profile_id]
        theirs = {n.casefold() for p in others for n in setup_wilma.student_names(p)}
        new = {k["name"].casefold(): k for k in kids}
        # In the order they were saved in, a Kid this Wilma lists too with what it says now.
        kept = [new.pop(k.name.casefold(), {"name": k.name}) for k in self.context.model().kids
                if k.name.casefold() in theirs or k.name.casefold() in new]
        town = None if others and self.context.data().get("city") else city
        answers: dict[str, Any] = {
            "kids": [{"name": k["name"],
                      **({"school": k["school"]} if k.get("school") else {}),
                      **({"class_name": k["class"]} if k.get("class") else {})}
                     for k in [*kept, *new.values()]],
            "sources": {"wilma": {"enabled": True}},
            **({"city": town} if town else {}),
        }
        return self.context.connect.complete("wilma", answers, reply={
            "result": "signed-in", "kids": [k["name"] for k in kids], "city": city,
            "signins": signins()})


def signins() -> list[dict[str, Any]]:
    """Each Wilma the CLI signs in to, in its config's order, for the page to list: its id, its
    name, the username and the Kids it lists. Never the password."""
    return [{"id": p["id"], "wilma": p.get("tenantName") or p.get("tenantUrl"),
             "username": p.get("username"), "kids": setup_wilma.student_names(p)}
            for p in setup_wilma.profiles(setup_wilma.config_path())
            if isinstance(p.get("id"), str)]


def _host(profile: dict[str, Any]) -> str | None:
    """The host of the Wilma the CLI's `profile` signs in to, such as espoo.inschool.fi."""
    url = str(profile.get("tenantUrl") or "")
    return urlparse(url if "//" in url else f"//{url}").hostname


def _towns(profile: dict[str, Any]) -> list[str]:
    """The towns of the Wilma the CLI's `profile` signs in to, as Wilma's list and the presets
    name them."""
    host = _host(profile)
    tenant = next((t for t in setup_wilma.tenants() or [] if urlparse(t["url"]).hostname == host),
                  None)
    preset = setup_wilma.WILMA_CITIES.get(host or "")
    return [*(setup_wilma.towns_of(tenant) if tenant else []), *([preset] if preset else [])]


def _city(address: str | None, town: str | None) -> str | None:
    """The town of the Wilma signed in to in the Terminal window: the one picked on the page if
    it's one of that Wilma's, else its first."""
    listed = setup_wilma.tenants() or []
    tenant = next((t for t in listed if urlparse(t["url"]).hostname == address), None)
    towns = setup_wilma.towns_of(tenant) if tenant else []
    if towns:
        return town if town in towns else towns[0]
    return setup_wilma.WILMA_CITIES.get(address or "")


def _listed_tenant(listed: list[dict[str, Any]], url: str,
                   town: str | None) -> dict[str, Any] | None:
    """The entry of Wilma's list at `url` when `town` is one of its towns, or is None for an entry
    without any; else None."""
    tenant = setup_wilma.entry(listed, url)
    towns = setup_wilma.towns_of(tenant) if tenant else []
    if tenant is None or (town not in towns if towns else town is not None):
        return None
    return tenant


def _wilma_invalid() -> dict[str, Any]:
    # Names the fields only: the answers carry the password.
    return setup_save.outcome(
        "invalid-answers", "Pick your town's Wilma from the list and give the username and the "
        "password.", errors=["answers: should be the url and town picked from the list, and the "
                             "username and the password, as text"])
