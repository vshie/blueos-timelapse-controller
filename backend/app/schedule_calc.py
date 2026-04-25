"""Schedule calculation helpers (next-run resolution for recipes)."""

from __future__ import annotations

import datetime as dt

from app.models import AppSettings, Recipe
from app.timeutil import now_local

_SEARCH_DAYS = 8


def _parse_hm(s: str) -> tuple[int, int] | None:
    try:
        hh_s, mm_s = s.split(":", 1)
        hh, mm = int(hh_s), int(mm_s)
    except (ValueError, AttributeError):
        return None
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return None
    return hh, mm


def next_run_at(
    recipe: Recipe,
    settings: AppSettings,
    *,
    now: dt.datetime | None = None,
) -> dt.datetime | None:
    """Return the next datetime (in the device timezone) at which `recipe` would fire.

    Considers `recipe.enabled`, `recipe.days_of_week`, and `recipe.times_local`.
    Returns None if disabled or if the recipe has no times/days configured.
    Searches up to 8 days ahead so a recipe that only fires on Sundays still resolves.
    """
    if not recipe.enabled:
        return None
    if not recipe.times_local or not recipe.days_of_week:
        return None

    ref = now if now is not None else now_local(settings)
    tz = ref.tzinfo

    times: list[tuple[int, int]] = []
    for s in recipe.times_local:
        parsed = _parse_hm(s)
        if parsed is not None:
            times.append(parsed)
    if not times:
        return None
    times.sort()

    days = set(int(d) for d in recipe.days_of_week)

    today_date = ref.date()
    for offset in range(_SEARCH_DAYS):
        d = today_date + dt.timedelta(days=offset)
        if d.weekday() not in days:
            continue
        for hh, mm in times:
            candidate = dt.datetime(d.year, d.month, d.day, hh, mm, 0, tzinfo=tz)
            if candidate > ref:
                return candidate
    return None
