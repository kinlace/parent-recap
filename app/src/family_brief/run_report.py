"""Sending the Run Report (ADR 0048): after each evening run that wrote one (last_run.py), the
family's computer sends it to the maintainers' gateway, `POST /v1/report` at llm.gateway_url, the
address the Brief already goes to. It goes from inside the program, never through a shell,
PowerShell or `curl` (#591), with the Household's Gateway Key, or, without one, under a random id
the program makes once and keeps (`id`). Config.sends_run_report says whether it is sent at all.

What is sent is the file the run wrote, byte for byte: it is copied into OUTBOX first, so a parent
or their agent reads there exactly what leaves, and is deleted once the gateway took it. A report
the gateway didn't answer, or turned away for now, waits there for the next run, which sends it
before its own, until it is KEEP_DAYS old. The program reads nothing of the gateway's answer but
its status. Sending never fails the run or touches the Brief: it comes after the Brief, and every
failure is a line in the log."""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import gateway, last_run
from .config import Config

log = logging.getLogger(__name__)

OUTBOX = "run-reports"   # beside state.json: the reports waiting, the report id and when one was sent
ID_FILE = "id"
SENT_FILE = "sent"
REPORT_ID_HEADER = "x-report-id"
# A report not sent within this long of its run is dropped: it says little the newer ones don't.
KEEP_DAYS = 7
# How long one report waits for the gateway: the Brief is out, but the evening's update follows.
TIMEOUT = 15
TAKEN = 200
# The gateway will never take this report as it is (a field it doesn't take, or too big): it is
# dropped rather than sent again each evening.
REFUSED_FOR_GOOD = frozenset({400, 413})


def outbox(cfg: Config) -> Path:
    return cfg.resolved_state_path().with_name(OUTBOX)


def waiting(cfg: Config) -> list[Path]:
    """The reports waiting to be sent, oldest first."""
    folder = outbox(cfg)
    return sorted(folder.glob("*.json")) if folder.is_dir() else []


def queue(cfg: Config) -> Path | None:
    """Copies the Run Report the run just wrote into the outbox, when the Household sends it.
    Returns the copy, or None when there is none to send."""
    if not cfg.sends_run_report():
        return None
    try:
        raw = last_run.path(cfg).read_bytes()
        folder = outbox(cfg)
        folder.mkdir(parents=True, exist_ok=True)
        # Named by when it was queued, then a count within that moment, so the oldest sorts first.
        stamp = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S%fZ}"
        copy = next(p for n in range(1000) if not (p := folder / f"{stamp}-{n:03d}.json").exists())
        copy.write_bytes(raw)
        return copy
    except Exception as e:  # noqa: BLE001 - the run's result stands without its report
        log.warning("The Run Report couldn't be queued to send: %s", type(e).__name__)
        return None


def send(cfg: Config, timeout: float = TIMEOUT) -> None:
    """Sends the reports waiting in the outbox, oldest first, dropping those over KEEP_DAYS old.
    Stops at the first the gateway doesn't take, which waits for the next run with those after
    it. Never raises."""
    try:
        if not cfg.sends_run_report():
            return
        reports = waiting(cfg)
        if not reports:
            return
        headers = _who(cfg)
        for report in reports:
            if not _send_one(cfg, report, headers, timeout):
                return
    except Exception as e:  # noqa: BLE001 - the run's result stands without its report
        log.warning("The Run Report wasn't sent: %s", type(e).__name__)


def _send_one(cfg: Config, report: Path, headers: dict[str, str], timeout: float) -> bool:
    """Sends one waiting report. False when the rest should wait for the next run."""
    raw = report.read_bytes()
    if _too_old(raw):
        log.info("A Run Report over %d days old wasn't sent and is dropped", KEEP_DAYS)
        report.unlink(missing_ok=True)
        return True
    try:
        status = gateway.post_report(cfg.llm.gateway_url, headers, raw, timeout).status_code
    except (TimeoutError, gateway.GatewayError) as e:
        log.info("The Run Report waits for the next run: %s", e)
        return False
    if status == TAKEN:
        report.unlink(missing_ok=True)
        _note_sent(cfg)
        log.info("Run Report sent to the gateway")
        return True
    if status in REFUSED_FOR_GOOD:
        log.warning("The gateway refused a Run Report (HTTP %d); it is dropped", status)
        report.unlink(missing_ok=True)
        return True
    log.info("The gateway didn't take the Run Report (HTTP %d); it waits for the next run", status)
    return False


def _too_old(raw: bytes) -> bool:
    """Whether the run the report is of ended KEEP_DAYS or more ago; a report whose time can't be
    read counts as too old."""
    try:
        at = datetime.fromisoformat(json.loads(raw)["at"])
        return datetime.now(timezone.utc) - at >= timedelta(days=KEEP_DAYS)
    except (ValueError, KeyError, TypeError):
        return True


def _who(cfg: Config) -> dict[str, str]:
    """Whom the report is from: the Gateway Key when one is stored and can be read, else the
    report id."""
    try:
        return {"x-api-key": gateway.key()}
    except gateway.NoKey:
        return {REPORT_ID_HEADER: report_id(cfg)}


def report_id(cfg: Config) -> str:
    """The random id this computer reports under without a Gateway Key: 32 hex digits, made the
    first time and kept. It names nobody."""
    path = outbox(cfg) / ID_FILE
    try:
        kept = path.read_text(encoding="ascii").strip()
        if len(kept) == 32 and all(c in "0123456789abcdef" for c in kept):
            return kept
    except (OSError, ValueError):
        pass
    made = uuid.uuid4().hex
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(made, encoding="ascii")
    return made


def _note_sent(cfg: Config) -> None:
    (outbox(cfg) / SENT_FILE).write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")


def last_sent(cfg: Config) -> datetime | None:
    """When the gateway last took a Run Report from this computer."""
    try:
        return datetime.fromisoformat((outbox(cfg) / SENT_FILE).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


# ── What doctor says of it

@dataclass(frozen=True)
class Status:
    on: bool
    set_in_config: bool
    hosted: bool
    last_sent: datetime | None
    waiting: int

    def line(self, now: datetime | None = None) -> str:
        if self.hosted:
            return "not sent: a Hosted Household's runs are read on the server"
        if not self.on:
            return ("turned off (run_report.enabled in the config)" if self.set_in_config
                    else "off, as the Brief goes from your own Gmail (run_report.enabled turns it on)")
        said = "on: after each evening run, how it ended goes to the maintainers' gateway"
        if self.last_sent is None:
            said += "; none sent yet"
        else:
            hours = ((now or datetime.now(timezone.utc)) - self.last_sent).total_seconds() / 3600
            said += f"; the last one was sent {hours:.0f} hours ago"
        if self.waiting:
            said += f"; {self.waiting} waiting to be sent with the next run's"
        return said


def status(cfg: Config) -> Status:
    return Status(cfg.sends_run_report(), cfg.run_report.enabled is not None, cfg.hosted,
                  last_sent(cfg), len(waiting(cfg)))
