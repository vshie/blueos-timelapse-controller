"""Snapshot prior tilt/light state and restore it after each recipe."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, call

import pytest

from app import mavlink_control
from app.models import AppSettings, Recipe, RecipeActions
from app.scheduler_service import SchedulerService


class _FakeStorage:
    def __init__(self, tmp_path: Path) -> None:
        self.captures_dir = tmp_path / "captures"
        self.captures_dir.mkdir(parents=True, exist_ok=True)


def _make_recipe(
    *,
    light: int | None = None,
    tilt: float | None = None,
    snapshot: bool = False,
    record_minutes: float | None = None,
) -> Recipe:
    return Recipe(
        id="r1",
        name="t",
        enabled=True,
        days_of_week=[0, 1, 2, 3, 4, 5, 6],
        times_local=["12:00"],
        rtsp_url="rtsp://test/stream",
        filename_prefix="t",
        actions=RecipeActions(
            camera_tilt_pitch_deg=tilt,
            light_brightness_pct=light,
            take_snapshot=snapshot,
            record_video_minutes=record_minutes,
        ),
    )


@pytest.fixture
def patched(monkeypatch, tmp_path):
    storage = _FakeStorage(tmp_path)
    svc = SchedulerService(storage_factory=lambda: storage)

    fake_master = MagicMock(name="master")
    monkeypatch.setattr(mavlink_control, "get_master", MagicMock(return_value=fake_master))

    set_tilt = MagicMock()
    set_light_pct = MagicMock()
    set_light_us = MagicMock()
    center_tilt = MagicMock()
    monkeypatch.setattr(mavlink_control, "set_camera_tilt_pitch", set_tilt)
    monkeypatch.setattr(mavlink_control, "set_light_pwm", set_light_pct)
    monkeypatch.setattr(mavlink_control, "set_light_pwm_us", set_light_us)
    monkeypatch.setattr(mavlink_control, "center_camera_tilt", center_tilt)

    read_pitch = MagicMock(return_value=12.5)
    read_servo = MagicMock(return_value=1340)
    monkeypatch.setattr(mavlink_control, "read_mount_pitch_deg", read_pitch)
    monkeypatch.setattr(mavlink_control, "read_servo_pwm", read_servo)

    from app import capture as capture_mod

    snap_mock = MagicMock()
    rec_mock = MagicMock()
    fin_mock = MagicMock(return_value=(True, tmp_path / "out.mp4"))
    monkeypatch.setattr(capture_mod, "capture_snapshot", snap_mock)
    monkeypatch.setattr(capture_mod, "record_for_duration", rec_mock)
    monkeypatch.setattr(capture_mod, "finalise_recording", fin_mock)

    return {
        "svc": svc,
        "storage": storage,
        "set_tilt": set_tilt,
        "set_light_pct": set_light_pct,
        "set_light_us": set_light_us,
        "center_tilt": center_tilt,
        "read_pitch": read_pitch,
        "read_servo": read_servo,
        "snap_mock": snap_mock,
        "rec_mock": rec_mock,
        "fin_mock": fin_mock,
        "master": fake_master,
    }


def test_light_only_recipe_snapshots_and_restores_only_light(patched):
    settings = AppSettings(default_rtsp_url="rtsp://test/stream", restore_state_after_recipe=True)
    recipe = _make_recipe(light=80)
    patched["svc"]._run_recipe(patched["storage"], settings, recipe)

    patched["read_servo"].assert_called_once_with(patched["master"], settings.light_servo_channel)
    patched["read_pitch"].assert_not_called()
    patched["set_light_pct"].assert_called_once_with(settings, 80)
    patched["set_light_us"].assert_called_once_with(settings, 1340)
    patched["set_tilt"].assert_not_called()
    patched["center_tilt"].assert_not_called()
    assert patched["svc"].get_state().state == "complete"


def test_tilt_only_recipe_snapshots_and_restores_only_tilt(patched):
    settings = AppSettings(default_rtsp_url="rtsp://test/stream", restore_state_after_recipe=True)
    recipe = _make_recipe(tilt=-30.0)
    patched["svc"]._run_recipe(patched["storage"], settings, recipe)

    patched["read_pitch"].assert_called_once_with(patched["master"])
    patched["read_servo"].assert_not_called()
    assert patched["set_tilt"].call_args_list == [
        call(settings, -30.0),
        call(settings, 12.5),
    ]
    patched["set_light_pct"].assert_not_called()
    patched["set_light_us"].assert_not_called()
    assert patched["svc"].get_state().state == "complete"


def test_both_light_and_tilt_recipe_snapshots_and_restores_both(patched):
    settings = AppSettings(default_rtsp_url="rtsp://test/stream", restore_state_after_recipe=True)
    recipe = _make_recipe(light=70, tilt=20.0, snapshot=True)
    patched["svc"]._run_recipe(patched["storage"], settings, recipe)

    patched["read_pitch"].assert_called_once_with(patched["master"])
    patched["read_servo"].assert_called_once_with(patched["master"], settings.light_servo_channel)
    assert patched["set_tilt"].call_args_list == [
        call(settings, 20.0),
        call(settings, 12.5),
    ]
    patched["set_light_pct"].assert_called_once_with(settings, 70)
    patched["set_light_us"].assert_called_once_with(settings, 1340)
    assert patched["svc"].get_state().state == "complete"


def test_setting_disabled_skips_snapshot_and_restore(patched):
    settings = AppSettings(default_rtsp_url="rtsp://test/stream", restore_state_after_recipe=False)
    recipe = _make_recipe(light=80, tilt=15.0)
    patched["svc"]._run_recipe(patched["storage"], settings, recipe)

    patched["read_pitch"].assert_not_called()
    patched["read_servo"].assert_not_called()
    patched["set_tilt"].assert_called_once_with(settings, 15.0)
    patched["set_light_pct"].assert_called_once_with(settings, 80)
    patched["set_light_us"].assert_not_called()
    assert patched["svc"].get_state().state == "complete"


def test_servo_snapshot_returning_none_skips_light_restore(patched):
    patched["read_servo"].return_value = None
    settings = AppSettings(default_rtsp_url="rtsp://test/stream", restore_state_after_recipe=True)
    recipe = _make_recipe(light=80)
    patched["svc"]._run_recipe(patched["storage"], settings, recipe)

    patched["read_servo"].assert_called_once_with(patched["master"], settings.light_servo_channel)
    patched["set_light_pct"].assert_called_once_with(settings, 80)
    patched["set_light_us"].assert_not_called()
    assert patched["svc"].get_state().state == "complete"


def test_recording_failure_still_runs_restore(patched):
    patched["rec_mock"].side_effect = RuntimeError("rtsp died")
    settings = AppSettings(default_rtsp_url="rtsp://test/stream", restore_state_after_recipe=True)
    recipe = _make_recipe(light=80, tilt=10.0, record_minutes=0.1)
    patched["svc"]._run_recipe(patched["storage"], settings, recipe)

    patched["set_light_us"].assert_called_once_with(settings, 1340)
    assert patched["set_tilt"].call_args_list == [
        call(settings, 10.0),
        call(settings, 12.5),
    ]
    st = patched["svc"].get_state()
    assert st.state == "failed"
    assert "rtsp died" in st.message
