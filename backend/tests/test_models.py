"""Unit tests for models and helpers."""

import pytest
from pydantic import ValidationError

from app.models import AppSettings, Recipe
from app.mavlink_control import brightness_to_pwm


def test_brightness_to_pwm():
    assert brightness_to_pwm(0, 1100, 1900) == 1100
    assert brightness_to_pwm(100, 1100, 1900) == 1900
    assert brightness_to_pwm(50, 1000, 2000) == 1500


def test_app_settings_pwm_order():
    with pytest.raises(ValidationError):
        AppSettings(light_pwm_min=1900, light_pwm_max=1100)


def test_recipe_weekday_validation():
    with pytest.raises(ValidationError):
        Recipe(name="x", days_of_week=[0, 8], times_local=["12:00"])


def test_recipe_time_format():
    with pytest.raises(ValidationError):
        Recipe(name="x", days_of_week=[0], times_local=["25:00"])
