"""`parent-recap signup household`: what the sign-up service runs as a new Hosted Household's own
user once the program is installed there (ADR 0031). It reads, as JSON on stdin:

  {"language": "fi", "city": "Espoo", "evening": "21:05",
   "recipients": [{"address": "parent@example.com", "language": "fi"}],
   "kids": [{"name": "Mia Virtanen", "school": "Kilon koulu", "class_name": "3B"}],
   "wilma": {"tenant": {"url": "https://espoo.inschool.fi", "name": "Espoon kaupunki"},
             "username": "mia.parent", "password": "…", "students": [{"studentNumber": "1001",
                                                                      "name": "Mia Virtanen"}]},
   "myclub": {"Mia Virtanen": "https://…myclub.fi/…"},
   "gateway_key": "sk-bf-…"}

and makes the Household: its config, through `setup save` as the setup page saves one, with the
Kids Wilma listed, the Recipients, the gateway as its AI and its sender, Wilma on, WhatsApp off and
the calendar as an .ics in the email; the MyClub link of each Kid in `myclub`, which the service
checked, saved as `setup myclub` saves one; the Gateway Key in the secrets store; the wilma CLI
with the sign-in the service checked; and the evening jobs at `evening`. It prints one line of
JSON, `{"result": "created"}` or why not, which never holds the password, a link or the key.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .. import gateway, ops, setup_save, setup_wilma
from ..collectors import myclub
from ..config import Config
from ..setup_report import outcome
from ..system import jobs, secrets


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


class Household(_Strict):
    language: str
    city: str | None = None
    evening: str
    recipients: list[dict[str, Any]] = Field(min_length=1)
    kids: list[dict[str, Any]] = Field(min_length=1)
    wilma: WilmaSignIn
    myclub: dict[str, str] = {}  # each link a secret (ADR 0007), by its Kid's name
    gateway_key: str = Field(pattern=r"^\S+$")


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
        "language": h.language, **({"city": h.city} if h.city else {}),
        "kids": h.kids, "recipients": h.recipients, "sender": "gateway", "ai": "gateway",
        "evening": h.evening,
        "sources": {"wilma": {"enabled": True}, "whatsapp": {"enabled": False}},
    })
    if saved["result"] != "saved":
        return saved
    for kid, url in h.myclub.items():
        try:
            myclub.save_link(config, kid, "".join(url.split()))
        except (OSError, ValueError) as e:  # names the Kid and the config, never the link
            return outcome("myclub-not-saved", reason=str(e)[:300])
    try:
        secrets.put(gateway.ACCOUNT, h.gateway_key)
    except secrets.SecretsError as e:
        return outcome("key-not-stored", reason=str(e))
    got = setup_wilma.install()
    if got["result"] != "installed":
        return outcome("wilma-not-installed", reason=got.get("reason") or got["result"])
    path = setup_wilma.config_path()
    before = setup_wilma.profile_ids(path)
    profile_id = setup_wilma.write_profile(path, h.wilma.tenant.model_dump(), h.wilma.username,
                                           h.wilma.password, h.wilma.students)
    setup_wilma.record_profile(profile_id, before)
    try:
        ops.install_jobs(Config.load(config))
    except jobs.NotStarted as e:
        return outcome("jobs-not-installed", reason=str(e))
    return outcome("created")


def _print(out: dict[str, Any]) -> int:
    print(json.dumps(out, ensure_ascii=False))
    return 0 if out["result"] == "created" else 1
