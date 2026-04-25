"""Background scheduler: run recipes at configured weekdays and local times."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from app import capture, mavlink_control
from app.models import AppSettings, Recipe, SchedulerStateResponse
from app.storage import Storage
from app.timeutil import now_local

logger = logging.getLogger(__name__)


@dataclass
class SchedulerService:
    storage_factory: Callable[[], Any]
    _thread: threading.Thread | None = None
    _stop: threading.Event = field(default_factory=threading.Event)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    state: SchedulerStateResponse = field(default_factory=SchedulerStateResponse)
    # Dedupe: (recipe_id, date_str, time_str) -> monotonic timestamp when fired
    _fired: dict[tuple[str, str, str], float] = field(default_factory=dict)
    _last_cleanup_mono: float = field(default_factory=time.monotonic)

    def get_state(self) -> SchedulerStateResponse:
        with self._lock:
            return self.state.model_copy()

    def _set_state(self, **kwargs) -> None:
        with self._lock:
            for k, v in kwargs.items():
                setattr(self.state, k, v)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="timelapse-scheduler", daemon=True)
        self._thread.start()
        logger.info("scheduler thread started")

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        self._thread = None

    def _cleanup_fired(self) -> None:
        now = time.monotonic()
        if now - self._last_cleanup_mono < 3600:
            return
        self._last_cleanup_mono = now
        # drop entries older than 48h
        cutoff = now - 48 * 3600
        dead = [k for k, t in self._fired.items() if t < cutoff]
        for k in dead:
            self._fired.pop(k, None)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as e:
                logger.exception("scheduler tick: %s", e)
                self._set_state(
                    state="failed",
                    message=str(e),
                    current_recipe_id=None,
                    current_recipe_name=None,
                )
            self._stop.wait(5)

    def _tick(self) -> None:
        self._cleanup_fired()
        storage = self.storage_factory()
        settings = storage.load_settings()
        recipes = [r for r in storage.list_recipes() if r.enabled]
        if not recipes:
            self._set_state(state="idle", message="No enabled recipes", next_wake_iso=None)
            return

        now = now_local(settings)
        today_wd = now.weekday()
        date_str = now.strftime("%Y-%m-%d")
        hm = now.strftime("%H:%M")

        for recipe in recipes:
            if today_wd not in recipe.days_of_week:
                continue
            for tslot in recipe.times_local:
                if tslot != hm:
                    continue
                key = (recipe.id, date_str, tslot)
                if key in self._fired:
                    continue
                self._fired[key] = time.monotonic()
                self._run_recipe(storage, settings, recipe)
                return

            self._set_state(
                state="waiting",
                message="Scheduler active (polling every 5s)",
                next_wake_iso=None,
            )

    def _run_recipe(self, storage: Storage, settings: AppSettings, recipe: Recipe) -> None:
        self._set_state(
            state="running",
            message="Executing recipe",
            current_recipe_id=recipe.id,
            current_recipe_name=recipe.name,
            last_run_at_iso=now_local(settings).isoformat(timespec="seconds"),
            current_action=None,
            current_action_started_at_iso=None,
        )
        url = (recipe.rtsp_url or "").strip() or settings.default_rtsp_url
        if not url:
            self._set_state(
                state="failed",
                message="No RTSP URL configured",
                current_recipe_id=recipe.id,
                current_action=None,
                current_action_started_at_iso=None,
            )
            return

        def _begin(action: str) -> None:
            self._set_state(
                current_action=action,
                current_action_started_at_iso=now_local(settings).isoformat(timespec="seconds"),
            )

        acts = recipe.actions
        wants_tilt = acts.camera_tilt_pitch_deg is not None or acts.center_camera_tilt
        wants_light = acts.light_brightness_pct is not None

        prior_tilt_deg: float | None = None
        prior_light_pwm_us: int | None = None
        if settings.restore_state_after_recipe and (wants_tilt or wants_light):
            _begin("snapshot_prior")
            try:
                master = mavlink_control.get_master(settings.mavlink_connection)
                if wants_tilt:
                    prior_tilt_deg = mavlink_control.read_mount_pitch_deg(master)
                    if prior_tilt_deg is None:
                        logger.warning("snapshot_prior: gimbal pitch unavailable; will skip tilt restore")
                    else:
                        logger.info("snapshot_prior tilt_deg=%.3f", prior_tilt_deg)
                if wants_light:
                    prior_light_pwm_us = mavlink_control.read_servo_pwm(
                        master, settings.light_servo_channel
                    )
                    if prior_light_pwm_us is None:
                        logger.warning(
                            "snapshot_prior: SERVO_OUTPUT_RAW servo%d unavailable; will skip light restore",
                            settings.light_servo_channel,
                        )
                    else:
                        logger.info(
                            "snapshot_prior light pwm_us=%d (servo %d)",
                            prior_light_pwm_us,
                            settings.light_servo_channel,
                        )
            except Exception as e:
                logger.warning("snapshot_prior failed: %s", e)

        run_error: Exception | None = None
        try:
            if acts.camera_tilt_pitch_deg is not None:
                _begin("tilt")
                mavlink_control.set_camera_tilt_pitch(settings, float(acts.camera_tilt_pitch_deg))
            elif acts.center_camera_tilt:
                _begin("tilt")
                mavlink_control.center_camera_tilt(settings)
            if acts.light_brightness_pct is not None:
                _begin("light")
                mavlink_control.set_light_pwm(settings, acts.light_brightness_pct)

            cap_dir = storage.captures_dir
            base = capture.stamp_basename(recipe.filename_prefix)

            if acts.take_snapshot:
                _begin("snapshot")
                snap = cap_dir / f"{base}.jpg"
                capture.capture_snapshot(
                    url,
                    snap,
                    latency_ms=settings.gstreamer_latency_ms,
                    tcp=settings.use_tcp_rtsp,
                )

            if acts.record_video_minutes and acts.record_video_minutes > 0:
                dur_s = float(acts.record_video_minutes) * 60.0
                ts_path = cap_dir / f"{base}.ts"
                mp4_path = cap_dir / f"{base}.mp4"
                _begin("recording")
                capture.record_for_duration(
                    url,
                    ts_path,
                    duration_s=dur_s,
                    latency_ms=settings.gstreamer_latency_ms,
                    tcp=settings.use_tcp_rtsp,
                )
                _begin("remux")
                ok, final_path = capture.finalise_recording(ts_path, mp4_path, duration_s=dur_s)
                if not ok:
                    logger.warning("remux failed, keeping .ts: %s", final_path)
        except Exception as e:
            run_error = e
            logger.exception("recipe failed")
        finally:
            restore_errors: list[str] = []
            if prior_tilt_deg is not None or prior_light_pwm_us is not None:
                _begin("restore")
                logger.info("restoring prior state")
                if prior_tilt_deg is not None:
                    try:
                        mavlink_control.set_camera_tilt_pitch(settings, float(prior_tilt_deg))
                    except Exception as e:
                        logger.error("restore tilt failed: %s", e)
                        restore_errors.append(f"tilt restore: {e}")
                if prior_light_pwm_us is not None:
                    try:
                        mavlink_control.set_light_pwm_us(settings, int(prior_light_pwm_us))
                    except Exception as e:
                        logger.error("restore light failed: %s", e)
                        restore_errors.append(f"light restore: {e}")

            if run_error is not None:
                msg = str(run_error)
                if restore_errors:
                    msg += " (restore issues: " + "; ".join(restore_errors) + ")"
                self._set_state(
                    state="failed",
                    message=msg,
                    current_recipe_id=recipe.id,
                    current_recipe_name=recipe.name,
                    current_action=None,
                    current_action_started_at_iso=None,
                )
            else:
                msg = "Recipe finished"
                if restore_errors:
                    msg += " (restore issues: " + "; ".join(restore_errors) + ")"
                self._set_state(
                    state="complete",
                    message=msg,
                    current_recipe_id=recipe.id,
                    current_recipe_name=recipe.name,
                    current_action=None,
                    current_action_started_at_iso=None,
                )
