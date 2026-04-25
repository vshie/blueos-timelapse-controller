"""Unit tests for models and helpers."""

import datetime as dt

import pytest
from pydantic import ValidationError

from app.models import AppSettings, Recipe, RecipeActions
from app.mavlink_control import brightness_to_pwm
from app.timeutil import now_local


def test_brightness_to_pwm():
    assert brightness_to_pwm(0, 1100, 1900) == 1100
    assert brightness_to_pwm(100, 1100, 1900) == 1900
    assert brightness_to_pwm(50, 1000, 2000) == 1500


def test_app_settings_pwm_order():
    with pytest.raises(ValidationError):
        AppSettings(light_pwm_min=1900, light_pwm_max=1100)


def test_app_settings_min_free_space_default_is_two_gb():
    s = AppSettings()
    assert s.min_free_space_gb == 2.0


def test_app_settings_min_free_space_negative_rejected():
    with pytest.raises(ValidationError):
        AppSettings(min_free_space_gb=-1.0)


def test_recipe_weekday_validation():
    with pytest.raises(ValidationError):
        Recipe(name="x", days_of_week=[0, 8], times_local=["12:00"])


def test_recipe_time_format():
    with pytest.raises(ValidationError):
        Recipe(name="x", days_of_week=[0], times_local=["25:00"])


def test_recipe_time_lenient_parsing():
    """Single-digit hours/minutes are accepted and normalised to HH:MM."""
    r = Recipe(name="x", days_of_week=[0], times_local=["8:00", " 9:5", "23:59"])
    assert r.times_local == ["08:00", "09:05", "23:59"]


def test_recipe_multiple_times_dedup_sorted():
    r = Recipe(name="x", days_of_week=[0], times_local=["14:30", "08:00", "08:00"])
    assert r.times_local == ["08:00", "14:30"]


def test_app_settings_timezone_empty_allowed():
    s = AppSettings()
    assert s.timezone == ""
    s2 = AppSettings(timezone="")
    assert s2.timezone == ""


def test_app_settings_timezone_invalid_rejected():
    with pytest.raises(ValidationError):
        AppSettings(timezone="Not/AZone")


def test_app_settings_timezone_pacific_honolulu_accepted():
    s = AppSettings(timezone="Pacific/Honolulu")
    assert s.timezone == "Pacific/Honolulu"


def test_now_local_with_honolulu_returns_minus_ten_offset():
    s = AppSettings(timezone="Pacific/Honolulu")
    t = now_local(s)
    assert t.tzinfo is not None
    assert t.utcoffset() == dt.timedelta(hours=-10)


def test_now_local_empty_timezone_returns_aware_datetime():
    s = AppSettings()
    t = now_local(s)
    assert t.tzinfo is not None


def test_recipe_actions_tilt_zero_accepted():
    a = RecipeActions(camera_tilt_pitch_deg=0)
    assert a.camera_tilt_pitch_deg == 0.0
    assert a.center_camera_tilt is False


def test_recipe_actions_tilt_negative_accepted():
    a = RecipeActions(camera_tilt_pitch_deg=-45.0)
    assert a.camera_tilt_pitch_deg == -45.0


def test_recipe_actions_tilt_out_of_range_rejected():
    with pytest.raises(ValidationError):
        RecipeActions(camera_tilt_pitch_deg=120)


def test_recipe_actions_legacy_center_migrates_to_zero():
    a = RecipeActions(camera_tilt_pitch_deg=None, center_camera_tilt=True)
    assert a.camera_tilt_pitch_deg == 0.0
    assert a.center_camera_tilt is False


def test_recipe_actions_explicit_pitch_overrides_legacy_bool():
    a = RecipeActions(camera_tilt_pitch_deg=10, center_camera_tilt=True)
    assert a.camera_tilt_pitch_deg == 10.0
    assert a.center_camera_tilt is False
