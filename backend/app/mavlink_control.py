"""MAVLink vehicle control via pymavlink."""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING

from pymavlink import mavutil

if TYPE_CHECKING:
    from app.models import AppSettings

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_master: mavutil.mavlink_connection | None = None
_master_conn_str: str | None = None


def disconnect() -> None:
    global _master, _master_conn_str
    with _lock:
        if _master is not None:
            try:
                _master.close()
            except Exception as e:
                logger.debug("close mavlink: %s", e)
        _master = None
        _master_conn_str = None


def get_master(connection_string: str, *, timeout_s: float = 10.0) -> mavutil.mavlink_connection:
    """Return a connected mavlink connection (singleton per connection string)."""
    global _master, _master_conn_str
    with _lock:
        if _master is not None and _master_conn_str == connection_string:
            return _master
        if _master is not None:
            try:
                _master.close()
            except Exception:
                pass
        logger.info("MAVLink connecting: %s", connection_string)
        m = mavutil.mavlink_connection(connection_string)
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout_s:
            hb = m.wait_heartbeat(timeout=1)
            if hb:
                logger.info(
                    "MAVLink heartbeat from system=%s component=%s",
                    m.target_system,
                    m.target_component,
                )
                _master = m
                _master_conn_str = connection_string
                return m
        m.close()
        raise TimeoutError(f"No MAVLink heartbeat within {timeout_s}s on {connection_string}")


def set_camera_tilt_pitch_deg(master: mavutil.mavlink_connection, pitch_deg: float, limits: tuple[float, float]) -> None:
    lo, hi = limits
    pitch_deg = max(lo, min(hi, pitch_deg))
    master.mav.command_long_send(
        master.target_system,
        master.target_component,
        mavutil.mavlink.MAV_CMD_DO_GIMBAL_MANAGER_TILTPAN,
        0,
        pitch_deg,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
    )


def center_camera_tilt(settings: AppSettings) -> None:
    m = get_master(settings.mavlink_connection)
    set_camera_tilt_pitch_deg(
        m,
        0.0,
        (settings.tilt_pitch_min_deg, settings.tilt_pitch_max_deg),
    )


def brightness_to_pwm(brightness_pct: int, pwm_min: int, pwm_max: int) -> int:
    b = max(0, min(100, brightness_pct))
    return int(round(pwm_min + (pwm_max - pwm_min) * (b / 100.0)))


def set_light_pwm(settings: AppSettings, brightness_pct: int) -> None:
    m = get_master(settings.mavlink_connection)
    pwm = brightness_to_pwm(brightness_pct, settings.light_pwm_min, settings.light_pwm_max)
    master = m
    master.mav.command_long_send(
        master.target_system,
        master.target_component,
        mavutil.mavlink.MAV_CMD_DO_SET_SERVO,
        0,
        float(settings.light_servo_channel),
        float(pwm),
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
    )


def mavlink_status(settings: AppSettings) -> dict:
    """Lightweight status without forcing long blocking connect."""
    try:
        m = get_master(settings.mavlink_connection, timeout_s=3.0)
        return {
            "connected": True,
            "target_system": m.target_system,
            "target_component": m.target_component,
            "connection": settings.mavlink_connection,
        }
    except Exception as e:
        return {"connected": False, "error": str(e), "connection": settings.mavlink_connection}
