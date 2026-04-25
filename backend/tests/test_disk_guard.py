"""Disk-space guard for recipe captures."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app import capture as capture_mod
from app import mavlink_control, scheduler_service
from app.models import AppSettings, Recipe, RecipeActions
from app.scheduler_service import SchedulerService


class _FakeStorage:
    def __init__(self, tmp_path: Path) -> None:
        self.captures_dir = tmp_path / "captures"
        self.captures_dir.mkdir(parents=True, exist_ok=True)


def _make_recipe() -> Recipe:
    return Recipe(
        id="rdisk",
        name="disk",
        enabled=True,
        days_of_week=[0, 1, 2, 3, 4, 5, 6],
        times_local=["12:00"],
        rtsp_url="rtsp://test/stream",
        filename_prefix="d",
        actions=RecipeActions(
            camera_tilt_pitch_deg=0.0,
            light_brightness_pct=80,
            take_snapshot=True,
            record_video_minutes=0.1,
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

    monkeypatch.setattr(mavlink_control, "read_mount_pitch_deg", MagicMock(return_value=12.5))
    monkeypatch.setattr(mavlink_control, "read_servo_pwm", MagicMock(return_value=1340))

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
        "snap_mock": snap_mock,
        "rec_mock": rec_mock,
        "fin_mock": fin_mock,
    }


def test_disk_low_skips_snapshot_and_record_but_runs_tilt_and_light(patched, monkeypatch):
    monkeypatch.setattr(scheduler_service, "disk_free_bytes", lambda _path: int(1.5 * (1024 ** 3)))
    settings = AppSettings(default_rtsp_url="rtsp://test/stream", min_free_space_gb=2.0)
    recipe = _make_recipe()

    patched["svc"]._run_recipe(patched["storage"], settings, recipe)

    patched["snap_mock"].assert_not_called()
    patched["rec_mock"].assert_not_called()
    patched["fin_mock"].assert_not_called()
    patched["set_tilt"].assert_any_call(settings, 0.0)
    patched["set_light_pct"].assert_called_once_with(settings, 80)

    st = patched["svc"].get_state()
    assert st.state == "complete"
    assert "threshold" in st.message
    assert "free=1.61 GB" in st.message
    assert "threshold=2.00 GB" in st.message


def test_disk_ok_runs_capture_actions(patched, monkeypatch):
    monkeypatch.setattr(scheduler_service, "disk_free_bytes", lambda _path: int(5 * (1024 ** 3)))
    settings = AppSettings(default_rtsp_url="rtsp://test/stream", min_free_space_gb=2.0)
    recipe = _make_recipe()

    patched["svc"]._run_recipe(patched["storage"], settings, recipe)

    patched["snap_mock"].assert_called_once()
    patched["rec_mock"].assert_called_once()
    patched["fin_mock"].assert_called_once()
    st = patched["svc"].get_state()
    assert st.state == "complete"
    assert "threshold" not in st.message
