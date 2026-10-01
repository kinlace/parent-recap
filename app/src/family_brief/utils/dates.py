from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def lookback_start(hours: int) -> datetime:
    return now_utc() - timedelta(hours=hours)


def to_local(dt: datetime, tz: str) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ZoneInfo(tz))


def today_str(tz: str) -> str:
    return to_local(now_utc(), tz).strftime("%Y-%m-%d")
