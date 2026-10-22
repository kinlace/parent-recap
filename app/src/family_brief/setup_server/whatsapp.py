"""WhatsApp's connect step: reading WhatsApp with the scheduled job's permission, and showing
the Python to allow in Full Disk Access."""
from __future__ import annotations

import os
import threading
from http import HTTPStatus
from typing import Any

from .. import ops, setup_steps, setup_whatsapp
from ..setup_connect import Nothing, Reply, parse
from .context import Action, Context, Phase, nothing_to_give

# What one read of WhatsApp can say, as `setup whatsapp` does, and whether the button opened
# Finder, with the Python to allow, and Full Disk Access.
WHATSAPP_RESULTS = ("readable", "syncing", "no-permission", "waiting", "not-installed",
                    "unreadable", "bg-failed")
WHATSAPP_OPEN_RESULTS = ("opened", "not-opened")


class WhatsApp(Phase):
    def __init__(self, context: Context) -> None:
        super().__init__(context)
        self._lock = threading.Lock()  # one read at a time, since each is a launchd job

    def routes(self) -> dict[str, Action]:
        return {"api/whatsapp/check": self.check_whatsapp,
                "api/whatsapp/open": self.open_full_disk_access}

    def state(self, progress: dict[str, Any]) -> dict[str, Any]:
        # The name of the Python file Finder selects for Full Disk Access, such as python3.13,
        # for every text that names it. Not its path: that names the Mac's user.
        return {"whatsapp": {"python": os.path.basename(ops.full_disk_access_python())}}

    def check_whatsapp(self, raw: Any) -> Reply:
        """Reads WhatsApp once through a `bg` job, with the scheduled job's permission, as `setup
        whatsapp` does. The page checks again every few seconds, so once the family has given
        the permission, WhatsApp is done by itself, and the groups found are kept in setup's
        progress for the check page. Errors aren't returned: they can name the Mac's files."""
        parse(Nothing, raw)
        with self._lock:
            # setup_whatsapp.read_through_bg, by the name its tests replace until #423.
            read = setup_steps.read_whatsapp_through_bg(
                str(self.context.config), setup_whatsapp.WHATSAPP_DAYS,
                self.context.timeouts.whatsapp_read)
        if read["result"] != "readable":
            return HTTPStatus.OK, {"result": read["result"]}
        # Right after signing in, WhatsApp's database is readable before the chats have synced
        # (#460): the page checks again until a group shows, and a family with none skips.
        if not read["chats"]:
            return HTTPStatus.OK, {"result": "syncing"}
        # A read takes a while, and the family may have skipped WhatsApp or opened another Source
        # meanwhile: their choice stands.
        return self.context.connect.complete(
            "whatsapp", {"progress": {"whatsapp_chats": read["chats"]}}, keep_skipped=True,
            move_on=lambda progress: progress["source"] == "whatsapp",
            reply={"result": "readable", "chats": read["chats"]})

    def open_full_disk_access(self, raw: Any) -> Reply:
        """Selects the scheduled job's Python in Finder and opens Full Disk Access next to it, as
        `setup whatsapp` does, for the family to drag it in and turn its switch on."""
        if raw != {}:
            return HTTPStatus.BAD_REQUEST, nothing_to_give()
        _, failed = ops.show_python_for_full_disk_access()
        return HTTPStatus.OK, {"result": "not-opened" if failed else "opened"}
