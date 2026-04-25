"""Tests for `next_run_at` schedule calculation."""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from app.models import AppSettings, Recipe
from app.schedule_calc import next_run_at


HNL = ZoneInfo("Pacific/Honolulu")


def _settings() -> AppSettings:
    return AppSettings(timezone="Pacific/Honolulu")


def _recipe(
    *,
    enabled: bool = True,
    days_of_week: list[int] | None = None,
    times_local: list[str] | None = None,
) -> Recipe:
    return Recipe(
        id="r1",
        name="t",
        enabled=enabled,
        days_of_week=days_of_week if days_of_week is not None else [0, 1, 2, 3, 4, 5, 6],
        times_local=times_local if times_local is not None else ["08:00", "14:00"],
    )


def test_next_run_today_morning_returns_08():
    r = _recipe()
    s = _settings()
    now = dt.datetime(2026, 4, 24, 7, 30, 0, tzinfo=HNL)
    nxt = next_run_at(r, s, now=now)
    assert nxt == dt.datetime(2026, 4, 24, 8, 0, 0, tzinfo=HNL)


def test_next_run_after_last_today_rolls_to_tomorrow():
    r = _recipe()
    s = _settings()
    now = dt.datetime(2026, 4, 24, 15, 0, 0, tzinfo=HNL)
    nxt = next_run_at(r, s, now=now)
    assert nxt == dt.datetime(2026, 4, 25, 8, 0, 0, tzinfo=HNL)


def test_disabled_recipe_returns_none():
    r = _recipe(enabled=False)
    s = _settings()
    now = dt.datetime(2026, 4, 24, 7, 30, 0, tzinfo=HNL)
    assert next_run_at(r, s, now=now) is None


def test_sunday_only_from_monday_returns_next_sunday_earliest_time():
    r = _recipe(days_of_week=[6], times_local=["08:00", "14:00"])
    s = _settings()
    monday = dt.datetime(2026, 4, 20, 9, 0, 0, tzinfo=HNL)
    assert monday.weekday() == 0
    nxt = next_run_at(r, s, now=monday)
    assert nxt == dt.datetime(2026, 4, 26, 8, 0, 0, tzinfo=HNL)
    assert nxt is not None and nxt.weekday() == 6


def test_empty_times_returns_none():
    r = Recipe(
        id="r1", name="t", enabled=True, days_of_week=[0, 1, 2, 3, 4, 5, 6], times_local=["12:00"]
    )
    r.times_local = []
    s = _settings()
    now = dt.datetime(2026, 4, 24, 7, 30, 0, tzinfo=HNL)
    assert next_run_at(r, s, now=now) is None


def test_exact_match_returns_next_slot_not_now():
    r = _recipe()
    s = _settings()
    now = dt.datetime(2026, 4, 24, 8, 0, 0, tzinfo=HNL)
    nxt = next_run_at(r, s, now=now)
    assert nxt == dt.datetime(2026, 4, 24, 14, 0, 0, tzinfo=HNL)
