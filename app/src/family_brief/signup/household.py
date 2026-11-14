"""`parent-recap signup household`: what the sign-up service runs as a new Hosted Household's own
user, with the server's shared copy of the program (ADR 0031, ADR 0035). It reads, as JSON on
stdin:

  {"language": "fi", "city": "Espoo", "evening": "21:05",
   "recipients": [{"address": "parent@example.com", "language": "fi"}],
   "kids": [{"name": "Mia Virtanen", "school": "Kilon koulu", "class_name": "3B"}],
   "wilma": {"tenant": {"url": "https://espoo.inschool.fi", "name": "Espoon kaupunki"},
             "username": "mia.parent", "password": "…", "students": [{"studentNumber": "1001",
                                                                      "name": "Mia Virtanen"}]},
   "myclub": {"Mia Virtanen": ["https://…myclub.fi/…"]},
   "eepos": {"school": "emo", "username": "parent@example.com", "password": "…",
             "kids": {"11": "Mia Virtanen"}},
   "evaka": {"city": "espoo", "username": "parent@example.com", "password": "…",
             "kids": {"1a2b…": "Mia Virtanen"}, "devices": {"__Host-evaka-device-user-…": "…"}},
   "gateway_key": "sk-bf-…"}

and makes the Household: its config, through `setup save` as the setup page saves one, marked
`hosted` so its Brief's footer says it comes from the hosted version, with the Kids Wilma listed,
the Recipients, the gateway as its AI, Wilma on (off, with no "wilma", for a family without it),
WhatsApp off and the calendar as an .ics in the
email; the MyClub links of each Kid in `myclub`, one per club (#810), which the service checked,
saved as `setup myclub` saves them; the Eepos sign-in the service checked (#769), its password in the secrets store and the
rest in the config, as `setup eepos` saves one; the eVaka sign-in the service checked (#790), saved
as `setup evaka` saves one, with the device cookie eVaka set on that check; the Gateway Key in the
secrets store; the wilma CLI's sign-in the service checked, for the shared copy's CLI (installed with it, so only a copy without one installs it here); and the
evening jobs at `evening`, which run the shared copy too. It prints one line of
JSON, `{"result": "created"}` or why not, which never holds the password, a link or the key.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

import yaml

from .. import gateway, ops, setup_save, setup_wilma, sources
from ..config import Config
from ..setup_report import outcome
from ..system import jobs, secrets

# MyClub's, Eepos's and eVaka's code is in their folders (ADR 0051); the sign-up reaches it through
# their records until the sign-up is split by Source.
myclub, myclub_links = sources.MYCLUB.code("collector"), sources.MYCLUB.code("config").myclub_links
eepos, setup_eepos = sources.EEPOS.code("collector"), sources.EEPOS.code("setup")
EeposSignin = sources.EEPOS.code("config").EeposSignin
evaka, setup_evaka = sources.EVAKA.code("collector"), sources.EVAKA.code("setup")
EvakaSignin = sources.EVAKA.code("config").EvakaSignin


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Tenant(_Strict):
    url: str
    name: str


class WilmaSignIn(_Strict):
    tenant: Tenant
    username: str = Field(pattern=r"\S")
    password: str = Field(min_length=1)
    students: list[dict[str, Any]] | None = None


class EeposSignIn(EeposSignin):
    """The Eepos sign-in the service checked, with its password and the Kids matched by id."""
    model_config = ConfigDict(extra="forbid")
    username: str = Field(pattern=r"\S")
    password: str = Field(min_length=1)


class EvakaSignIn(EvakaSignin):
    """The eVaka sign-in the service checked, with its password, the Kids matched by id and the
    device cookies eVaka set on that check."""
    model_config = ConfigDict(extra="forbid")
    username: str = Field(pattern=r"\S")
    password: str = Field(min_length=1)
    devices: dict[str, str] = {}


class Household(_Strict):
    language: str
    city: str | None = None
    evening: str
    recipients: list[dict[str, Any]] = Field(min_length=1)
    kids: list[dict[str, Any]] = Field(min_length=1)
    wilma: WilmaSignIn | None = None  # none for a family without Wilma (#760)
    # Each Kid's links, by name, one per club (#810), each a secret (ADR 0007); one link alone is
    # taken too, as before.
    myclub: dict[str, list[str] | str] = {}
    eepos: EeposSignIn | None = None  # one school for now (#769)
    evaka: EvakaSignIn | None = None  # one city for now (#790)
    gateway_key: str = Field(pattern=r"^\S+$")

    @model_validator(mode="after")
    def _reads_something(self) -> "Household":
        linked = any(myclub_links(urls) for urls in self.myclub.values())
        if self.wilma is None and not linked and self.eepos is None and self.evaka is None:
            raise ValueError("without Wilma, at least one MyClub link, an Eepos sign-in or an "
                             "eVaka sign-in is needed")
        return self


def cmd_household(args: argparse.Namespace) -> int:
    config = Path(os.path.expanduser(args.config or "~/.family/config.yaml"))
    try:
        household = Household.model_validate(json.loads(sys.stdin.read()))
    except ValueError as e:  # pydantic's ValidationError is one; neither quotes the values here
        errors = (setup_save.invalid(e)["errors"] if isinstance(e, ValidationError)
                  else ["not JSON"])
        return _print(outcome("invalid-answers", errors=errors))
    out = create(config, household)
    return _print(out)


def create(config: Path, h: Household) -> dict[str, Any]:
    saved = setup_save.save(config, {
        "language": h.language, **({"city": h.city} if h.city else {}), "hosted": True,
        "kids": h.kids, "recipients": h.recipients, "ai": "gateway",
        "evening": h.evening,
        "sources": {"wilma": {"enabled": h.wilma is not None}, "whatsapp": {"enabled": False}},
    })
    if saved["result"] != "saved":
        return saved
    for kid, urls in h.myclub.items():
        try:
            myclub.save_links(config, kid, ["".join(u.split()) for u in myclub_links(urls)])
        except (OSError, ValueError) as e:  # names the Kid and the config, never the link
            return outcome("myclub-not-saved", reason=str(e)[:300])
    try:
        secrets.put(gateway.ACCOUNT, h.gateway_key)
    except secrets.SecretsError as e:
        return outcome("key-not-stored", reason=str(e))
    if h.wilma is not None:
        stored = _store_wilma(h.wilma)
        if stored is not None:
            return stored
    if h.eepos is not None:
        stored = _store_eepos(config, h.eepos)
        if stored is not None:
            return stored
    if h.evaka is not None:
        stored = _store_evaka(config, h.evaka)
        if stored is not None:
            return stored
    try:
        ops.install_jobs(Config.load(config))
    except jobs.NotStarted as e:
        return outcome("jobs-not-installed", reason=str(e))
    return outcome("created")


def _store_wilma(w: WilmaSignIn) -> dict[str, Any] | None:
    """The wilma CLI's sign-in the service checked, or why it wasn't stored."""
    got = setup_wilma.install()
    if got["result"] != "installed":
        return outcome("wilma-not-installed", reason=got.get("reason") or got["result"])
    before = [p["id"] for p in setup_wilma.signins() if isinstance(p.get("id"), str)]
    try:
        profile_id = setup_wilma.save_signin(w.tenant.model_dump(), w.username, w.password,
                                             w.students)
    except OSError as e:  # the CLI's config's, never the password
        return outcome("wilma-not-stored", reason=str(e)[:300])
    setup_wilma.record_profile(profile_id, before)
    return None


def _store_eepos(config: Path, e: EeposSignIn) -> dict[str, Any] | None:
    """The Eepos sign-in the service checked, or why it wasn't stored."""
    try:
        secrets.put(eepos.secret_name(e.school, e.username), e.password)
    except secrets.SecretsError as err:
        return outcome("eepos-not-stored", reason=str(err))
    try:
        setup_eepos._save(config, EeposSignin(**e.model_dump(exclude={"password"})))
    except (OSError, ValueError, yaml.YAMLError) as err:  # the config's, never the password
        return outcome("eepos-not-saved", reason=str(err)[:300])
    return None


def _store_evaka(config: Path, e: EvakaSignIn) -> dict[str, Any] | None:
    """The eVaka sign-in the service checked, or why it wasn't stored."""
    try:
        secrets.put(evaka.secret_name(e.city, e.username), e.password)
    except secrets.SecretsError as err:
        return outcome("evaka-not-stored", reason=str(err))
    try:
        setup_evaka._save(config, EvakaSignin(**e.model_dump(exclude={"password", "devices"})))
        if e.devices:
            evaka._keep_devices(evaka.devices_path(Config.load(config)), evaka.host_of(e.city),
                                e.devices)
    except (OSError, ValueError, yaml.YAMLError) as err:  # the config's, never the password
        return outcome("evaka-not-saved", reason=str(err)[:300])
    return None


def _print(out: dict[str, Any]) -> int:
    print(json.dumps(out, ensure_ascii=False))
    return 0 if out["result"] == "created" else 1
