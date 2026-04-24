"""Background scheduler: run recipes at configured weekdays and local times."""

from __future__ import annotations

import datetime as dt
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from app import capture, mavlink_control
from app.models import AppSettings, Recipe, SchedulerStateResponse
from app.storage import Storage

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

        now_local = dt.datetime.now().astimezone()
        today_wd = now_local.weekday()
        date_str = now_local.strftime("%Y-%m-%d")
        hm = now_local.strftime("%H:%M")

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
            last_run_at_iso=dt.datetime.now().astimezone().isoformat(),
        )
        url = (recipe.rtsp_url or "").strip() or settings.default_rtsp_url
        if not url:
            self._set_state(state="failed", message="No RTSP URL configured", current_recipe_id=recipe.id)
            return

        acts = recipe.actions
        try:
            if acts.center_camera_tilt:
                mavlink_control.center_camera_tilt(settings)
            if acts.light_brightness_pct is not None:
                mavlink_control.set_light_pwm(settings, acts.light_brightness_pct)

            cap_dir = storage.captures_dir
            base = capture.stamp_basename(recipe.filename_prefix)

            if acts.take_snapshot:
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
                capture.record_for_duration(
                    url,
                    ts_path,
                    duration_s=dur_s,
                    latency_ms=settings.gstreamer_latency_ms,
                    tcp=settings.use_tcp_rtsp,
                )
                ok, final_path = capture.finalise_recording(ts_path, mp4_path, duration_s=dur_s)
                if not ok:
                    logger.warning("remux failed, keeping .ts: %s", final_path)

            self._set_state(
                state="complete",
                message="Recipe finished",
                current_recipe_id=recipe.id,
                current_recipe_name=recipe.name,
            )
        except Exception as e:
            logger.exception("recipe failed")
            self._set_state(
                state="failed",
                message=str(e),
                current_recipe_id=recipe.id,
                current_recipe_name=recipe.name,
            )
