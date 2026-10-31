"""The household host on the maintainers' server (ADR 0031): Linux users made and removed by a
root-owned helper the service's user may run through sudo, commands run as a Household through
`sudo -u`, and Gateway Keys minted and revoked on Bifrost's admin API on localhost.

The service's user may run, with sudo, only the helper (`household-user add|remove <name>`,
which takes no name but a Household's) and, as a Household's user, only the commands the service
runs, with exactly their arguments, as the sudoers file gateway-setup.sh installs says
(ops/gateway/signup.sudoers, #645): the release's install.sh by bash's absolute path, and the
Household's `parent-recap` with `signup household`, `run --first-brief --lookback-hours 72`,
`signup health` or `schedule uninstall`. So the service can't read a Household's secrets; it can
still rewrite its config, its Recipients among them, and send its first Brief. Bifrost's admin login comes from the service's environment
and goes only into the admin API's Authorization header.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import requests

from .host import USER, HostError

HELPER = "/usr/local/lib/parent-recap-signup/household-user"
ADMIN = "http://127.0.0.1:8080/api/governance/virtual-keys"
HELPER_SECONDS = 120
# A Household's Gateway Key, as the README's runbook creates one by hand.
KEY = {"is_active": True,
       "provider_configs": [{"provider": "anthropic", "weight": 1,
                             "allowed_models": ["parent-recap"], "key_ids": ["*"]}],
       "budgets": [{"max_limit": 0.10, "reset_duration": "1d"}]}
PAGE = 100  # Bifrost's virtual-key list returns at most 100 a page


def _checked(name: str) -> str:
    if not USER.fullmatch(name):
        raise ValueError(f"not a Household's user: {name!r}")
    return name


class LinuxHost:
    def __init__(self, admin_user: str, admin_password: str) -> None:
        self._auth = (admin_user, admin_password)

    @classmethod
    def from_env(cls) -> "LinuxHost":
        missing = [v for v in ("BIFROST_ADMIN_USERNAME", "BIFROST_ADMIN_PASSWORD")
                   if not os.environ.get(v)]
        if missing:
            raise HostError(f"{' and '.join(missing)} not set in the service's environment")
        return cls(os.environ["BIFROST_ADMIN_USERNAME"], os.environ["BIFROST_ADMIN_PASSWORD"])

    # ---------------------------------------------------------------- users

    def create_user(self, name: str) -> Path:
        out = self._helper("add", name)
        lines = out.strip().splitlines()
        if not lines or not lines[-1].startswith("/"):
            raise HostError(f"{HELPER} add didn't say where {name}'s home is")
        return Path(lines[-1])

    def home(self, name: str) -> Path | None:
        import pwd  # Linux's, so the module still imports where the adapters' tests run
        try:
            return Path(pwd.getpwnam(_checked(name)).pw_dir)
        except KeyError:
            return None

    def remove_user(self, name: str) -> None:
        self._helper("remove", name)

    def _helper(self, verb: str, name: str) -> str:
        r = self._sudo([HELPER, verb, _checked(name)], "", HELPER_SECONDS)
        if r.returncode != 0:
            raise HostError(f"{HELPER} {verb} {name} failed ({r.returncode}): "
                            f"{(r.stderr or r.stdout).strip()[-300:]}")
        return r.stdout

    def run_as(self, name: str, argv: list[str], stdin: str, timeout: int) -> tuple[int, str]:
        r = self._sudo(["-u", _checked(name), "-H", "--", *argv], stdin, timeout)
        return r.returncode, r.stdout + r.stderr

    @staticmethod
    def _sudo(args: list[str], stdin: str, timeout: int) -> subprocess.CompletedProcess:
        try:
            # From /, since the service's own folder is no Household's to read.
            return subprocess.run(["sudo", "-n", *args], input=stdin, capture_output=True,
                                  text=True, encoding="utf-8", errors="replace", cwd="/",
                                  timeout=timeout)
        except subprocess.TimeoutExpired:
            raise HostError(f"{args[0] if args[0] != '-u' else args[1]}'s command didn't end "
                            f"within {timeout} seconds") from None
        except OSError as e:
            raise HostError(f"sudo couldn't be run: {e.strerror}") from None

    # ---------------------------------------------------------------- Gateway Keys

    def mint_key(self, name: str) -> str:
        r = self._admin("POST", ADMIN, json={"name": _checked(name), **KEY})
        try:
            value = r.json()["virtual_key"]["value"]
        except (ValueError, KeyError, TypeError):
            value = None
        if not isinstance(value, str) or not value:
            raise HostError("the gateway's admin API answered without a key")
        return value

    def revoke_key(self, name: str) -> bool:
        _checked(name)
        found = False
        offset = 0
        while True:
            try:
                keys = self._admin("GET", f"{ADMIN}?limit={PAGE}&offset={offset}").json() \
                    .get("virtual_keys") or []
            except (ValueError, AttributeError):
                raise HostError("the gateway's admin API listed its keys in a shape not "
                                "known") from None
            for key in keys:
                if isinstance(key, dict) and key.get("name") == name and key.get("id"):
                    self._admin("DELETE", f"{ADMIN}/{key['id']}")
                    found = True
            if len(keys) < PAGE:
                return found
            offset += PAGE

    def _admin(self, method: str, url: str, **kwargs) -> requests.Response:
        try:
            r = requests.request(method, url, auth=self._auth, timeout=15, **kwargs)
        except requests.RequestException as e:
            raise HostError(f"the gateway's admin API couldn't be reached "
                            f"({e.__class__.__name__})") from None
        if r.status_code >= 300:  # its body may quote the request, so only the status
            raise HostError(f"the gateway's admin API answered {method} with HTTP {r.status_code}")
        return r
