"""Time helpers that honour the user-configured IANA timezone."""

from __future__ import annotations

import datetime as dt

from app.models import AppSettings


def now_local(settings: AppSettings) -> dt.datetime:
    """Return the current time in the user's configured timezone.

    Falls back to the container/host local time when the setting is empty or
    the zone cannot be resolved (e.g. tzdata unavailable).
    """
    tz_name = (settings.timezone or "").strip()
    if tz_name:
        try:
            from zoneinfo import ZoneInfo

            return dt.datetime.now(ZoneInfo(tz_name))
        except Exception:
            pass
    return dt.datetime.now().astimezone()
