"""Eepos's and eVaka's connect steps (ADR 0038, 0039, #791): a guardian's sign-in at a music or art
school on Eepos, or in a city's eVaka, typed into the page's own fields. Each is checked against
the service and kept as `setup eepos` and `setup evaka` keep theirs: the password in the system's
secret store, only once it works, and the rest in the config. The step lists the sign-ins already
added, each with the Kids its students or children matched, and the family adds another school or
city, removes one, or skips the Source, which removes them all (#500).

A Household without Wilma adds its Kids here too (#796): a student or child just found who
matches no Kid is added as one, by the name the family calls them, and the sign-in names that
Kid for them, so its events go to the Kid from the next evening. A field adds a Kid by name
alone, for a step opened again, when nothing was just found; such a Kid is matched by name each
evening."""
from __future__ import annotations

from http import HTTPStatus
from typing import Any, ClassVar

import yaml
from pydantic import BaseModel, Field

from .. import setup_save, sources
from ..config import Config
from ..setup_connect import Nothing, Reply, Strict, parse
from ..system import secrets
from .context import Action, Phase, add_kid, keychain_code

# Eepos's and eVaka's code is in their folders (ADR 0051); this page's steps reach it through
# their records until the page is split by Source.
eepos, setup_eepos = sources.EEPOS.code("collector"), sources.EEPOS.code("setup")
evaka, setup_evaka = sources.EVAKA.code("collector"), sources.EVAKA.code("setup")

# What adding a sign-in can say, as `setup eepos` and `setup evaka` do, besides the place given
# not being one: `not-a-school` (or `school-website`, for another site's address, such as the
# school's own website) or `not-a-city`.
SIGNIN_RESULTS = ("saved", "no-username", "wrong-password", "changed", "no-answer",
                  "keychain-not-reachable", "not-stored", "save-failed")
EEPOS_RESULTS = (*SIGNIN_RESULTS, "not-a-school", "school-website")
EVAKA_RESULTS = (*SIGNIN_RESULTS, "not-a-city")

# The cities on eVaka's own code as the family knows them, by the name the config keeps.
CITY_NAMES = {"espoo": "Espoo", "tampere": "Tampere", "turku": "Turku", "oulu": "Oulu",
              "hameenkyro": "Hämeenkyrö", "kangasala": "Kangasala", "lempaala": "Lempäälä",
              "nokia": "Nokia", "orivesi": "Orivesi", "pirkkala": "Pirkkala",
              "vesilahti": "Vesilahti", "ylojarvi": "Ylöjärvi"}


class EeposSignIn(Strict):
    school: str    # the school's Eepos address, or its part before .eepos.fi
    username: str
    password: str = Field(pattern=r"\S")  # a secret: never returned, logged or kept but in the store


class EeposRemoval(Strict):
    school: str
    username: str


class EvakaSignIn(Strict):
    city: str      # a city's name as `api/state` lists it, or the host of its eVaka
    username: str  # the guardian's email
    password: str = Field(pattern=r"\S")


class EvakaRemoval(Strict):
    city: str
    username: str


class EeposKid(Strict):
    """A Kid added by the name the family calls them, and, for a student just found, which one."""
    name: str
    school: str | None = None
    username: str | None = None
    id: str | None = None


class EvakaKid(Strict):
    """A Kid added by the name the family calls them, and, for a child just found, which one."""
    name: str
    city: str | None = None
    username: str | None = None
    id: str | None = None


class _SignIns(Phase):
    """A Source read with a guardian's sign-in per school or city, `place` naming which."""
    name: ClassVar[str]
    place: ClassVar[str]
    people: ClassVar[str]  # what `keep` calls the students or children it found
    steps: ClassVar[Any]   # setup_eepos or setup_evaka
    SignIn: ClassVar[type[BaseModel]]
    Removal: ClassVar[type[BaseModel]]
    Kid: ClassVar[type[BaseModel]]

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        # The ids of the students or children each sign-in found when it was added, by its place
        # and username: the ones a Kid may be added for.
        self.found: dict[tuple[str, str], set[str]] = {}

    def routes(self) -> dict[str, Action]:
        return {f"api/{self.name}": self.add, f"api/{self.name}/remove": self.remove,
                f"api/{self.name}/kid": self.add_kid, f"api/{self.name}/done": self.done}

    def state(self, progress: dict[str, Any]) -> dict[str, Any]:
        return {self.name: {"signins": self.signins(), "add": self.context.adds_kids()}}

    def add(self, raw: Any) -> Reply:
        """Signs in with the username and the password typed into the page's own fields and, only
        once that works, keeps the sign-in, in place of one for the same username there. Then the
        Source is done, and the step stays for another school or city until Continue. A password
        that doesn't work is said to be wrong and replaces nothing. The password is never
        returned, logged or put on a command line."""
        answer = parse(self.SignIn, raw)
        place = self.of(getattr(answer, self.place))
        if place is None:
            return HTTPStatus.OK, {"result": self.refusal(getattr(answer, self.place))}
        username = answer.username.strip()
        if not username:
            return HTTPStatus.OK, {"result": "no-username"}
        password = answer.password.strip()
        if not secrets.reachable():  # macOS in tmux or over SSH, where its prompt never shows
            return HTTPStatus.OK, {"result": "keychain-not-reachable"}
        connect = self.context.connect
        # Signed in before the lock, so a slow service holds up no other save.
        result, extra = self.steps.keep(self.context.config, self.context.model(), place,
                                        username, password, connect.lock)
        if result != "saved":  # without the error, which can name files
            return HTTPStatus.OK, {"result": result, **keychain_code(extra)}
        self.found[(place, username.casefold())] = {p["id"] for p in extra[self.people]}
        found = [{"name": p["name"], "kid": p["kid"], "id": p["id"], "first": p["first"]}
                 for p in extra[self.people]]
        return connect.complete(self.name, move_on=False, reply={
            "result": "saved", "found": found, "events": extra["events"],
            "signins": self.signins()})

    def remove(self, raw: Any) -> Reply:
        """Removes one of the sign-ins listed, with its password. With none left, the Source is
        to do again, to add one or skip it."""
        answer = parse(self.Removal, raw)
        place = self.of(getattr(answer, self.place))
        connect = self.context.connect
        with connect.lock:
            if place is None or not self.steps.remove(self.context.config, place, answer.username):
                return HTTPStatus.BAD_REQUEST, setup_save.outcome(
                    "invalid-answers", "Remove one of the sign-ins listed.",
                    errors=[f"{self.place}, username: should be one of the sign-ins api/state lists"])
            self.found.pop((place, answer.username.strip().casefold()), None)
            left = self.signins()
            status, out = connect.save(
                {"progress": {"sources": {self.name: "to-do"}} if not left else {}})
        if status != HTTPStatus.OK:
            return status, out
        return status, {**out, "result": "removed", "signins": left}

    def add_kid(self, raw: Any) -> Reply:
        """For a Household without Wilma, as MyClub's step does: adds a Kid by the name the family
        calls them. Given with a sign-in and the id of one of the students or children it found
        when it was added, the sign-in also names this Kid for them, so the step lists the Kid
        under it at once and each evening puts their events under the Kid. The id of anyone else
        is refused."""
        answer = parse(self.Kid, raw)
        given, username, person = getattr(answer, self.place), answer.username, answer.id
        if (given, username, person) == (None, None, None):
            status, out = add_kid(self.context, answer.name)
        else:
            place = self.of(given or "")
            key = (place, (username or "").strip().casefold())
            if place is None or person is None or person not in self.found.get(key, set()):
                return HTTPStatus.BAD_REQUEST, setup_save.outcome(
                    "invalid-answers", "Add a Kid for one of the people a sign-in just found.",
                    errors=[f"{self.place}, username, id: should be a sign-in api/{self.name} "
                            "just kept and the id of one it found"])
            try:
                status, out = add_kid(self.context, answer.name,
                                      lambda: self._name_kid(place, key[1], person, answer.name.strip()))
            except (OSError, ValueError, yaml.YAMLError):
                return HTTPStatus.CONFLICT, setup_save.outcome(
                    "save-failed", "The Kid couldn't be saved. Try again.")
        if out.get("result") != "added":
            return status, out
        return status, {**out, "signins": self.signins()}

    def _name_kid(self, place: str, username: str, person: str, kid: str) -> None:
        """Names the Kid `kid` for the student or child `person` of the sign-in as `username` at
        `place` in the config, the other sign-ins and lines as they were. Raises ValueError when
        that sign-in is gone."""
        path = self.context.config
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        section = data.get(self.name) if isinstance(data.get(self.name), dict) else {}
        signins = [s for s in section.get("signins") or []
                   if isinstance(s, dict) and self.of(str(s.get(self.place, ""))) == place
                   and str(s.get("username", "")).strip().casefold() == username]
        if not signins:
            raise ValueError(f"the {self.name} sign-in is gone")
        for signin in signins:
            kids = signin.get("kids") if isinstance(signin.get("kids"), dict) else {}
            signin["kids"] = {**kids, person: kid}
        new = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
        Config.model_validate(yaml.safe_load(new))
        setup_save._write_private(path, new)

    def done(self, raw: Any) -> Reply:
        """Moves setup on once a sign-in is kept, as one added before from a terminal is too."""
        parse(Nothing, raw)
        if not self.signins():
            return HTTPStatus.BAD_REQUEST, setup_save.outcome(
                "invalid-answers", f"Add a sign-in first, or skip {self.name}.",
                errors=["answers: should be {}, once a sign-in is kept"])
        return self.context.connect.complete(self.name)

    def signins(self) -> list[dict[str, Any]]:
        """Each sign-in kept, for the page to list: where, the username and the Kids it matched.
        Never the password."""
        raise NotImplementedError

    def of(self, given: str) -> str | None:
        """The school or city as the config keeps it, from what the family typed or picked."""
        raise NotImplementedError

    def refusal(self, given: str) -> str:
        """Why what the family typed or picked, which `of` takes for none, isn't a school or city."""
        return f"not-a-{self.place}"


def _kids(cfg: Config, matched: dict[str, str]) -> list[str]:
    """The Household's Kids a sign-in's students or children matched, in the Kids' order."""
    return [k.name for k in cfg.kids if k.name in matched.values()]


class Eepos(_SignIns):
    name, place, people, steps = "eepos", "school", "students", setup_eepos
    SignIn, Removal, Kid = EeposSignIn, EeposRemoval, EeposKid

    def signins(self) -> list[dict[str, Any]]:
        cfg = self.context.model()
        return [{"school": s.school, "address": eepos.address(s.school), "username": s.username,
                 "kids": _kids(cfg, s.kids)} for s in cfg.eepos.signins]

    def of(self, given: str) -> str | None:
        return eepos.school_of(given)

    def refusal(self, given: str) -> str:
        return "school-website" if eepos.is_website(given) else super().refusal(given)


class Evaka(_SignIns):
    name, place, people, steps = "evaka", "city", "children", setup_evaka
    SignIn, Removal, Kid = EvakaSignIn, EvakaRemoval, EvakaKid

    def state(self, progress: dict[str, Any]) -> dict[str, Any]:
        cities = [{"name": c, "label": CITY_NAMES.get(c, c.capitalize())}
                  for c in sorted(evaka.CITIES, key=lambda c: CITY_NAMES.get(c, c))]
        state = super().state(progress)
        return {self.name: {**state[self.name], "cities": cities}}

    def signins(self) -> list[dict[str, Any]]:
        cfg = self.context.model()
        return [{"city": s.city, "label": CITY_NAMES.get(s.city, s.city),
                 "address": evaka.host_of(s.city), "username": s.username,
                 "kids": _kids(cfg, s.kids)} for s in cfg.evaka.signins]

    def of(self, given: str) -> str | None:
        return evaka.city_of(given)
