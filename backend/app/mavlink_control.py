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

# Commands are sent to the autopilot component; heartbeats from GCS/routers often use other components.
_AP_COMP = mavutil.mavlink.MAV_COMP_ID_AUTOPILOT1

# Manual light test: 0% -> 1100 us (off), 100% -> 1900 us (full)
MANUAL_LIGHT_PWM_MIN = 1100
MANUAL_LIGHT_PWM_MAX = 1900


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


def _wait_vehicle_heartbeat(m: mavutil.mavlink_connection, timeout_s: float) -> None:
    """Pick target system/component from a vehicle autopilot heartbeat (not GCS/router)."""
    deadline = time.monotonic() + timeout_s
    fallback_sys = None
    fallback_comp = None
    while time.monotonic() < deadline:
        msg = m.recv_match(type="HEARTBEAT", blocking=True, timeout=1)
        if msg is None:
            continue
        if msg.get_type() != "HEARTBEAT":
            continue
        if msg.autopilot == mavutil.mavlink.MAV_AUTOPILOT_INVALID:
            continue
        sysid = msg.get_srcSystem()
        compid = msg.get_srcComponent()
        if fallback_sys is None:
            fallback_sys, fallback_comp = sysid, compid
        # Prefer obvious vehicle types (ArduSub / surface / rover / copter)
        if msg.type in (
            mavutil.mavlink.MAV_TYPE_SUBMARINE,
            mavutil.mavlink.MAV_TYPE_SURFACE_BOAT,
            mavutil.mavlink.MAV_TYPE_ROVER,
            mavutil.mavlink.MAV_TYPE_QUADROTOR,
            mavutil.mavlink.MAV_TYPE_HEXAROTOR,
            mavutil.mavlink.MAV_TYPE_OCTOROTOR,
            mavutil.mavlink.MAV_TYPE_TRICOPTER,
            mavutil.mavlink.MAV_TYPE_COAXIAL,
            mavutil.mavlink.MAV_TYPE_HELICOPTER,
        ):
            m.target_system = sysid
            m.target_component = compid
            logger.info(
                "MAVLink vehicle target sys=%s comp=%s (type=%s autopilot=%s)",
                sysid,
                compid,
                msg.type,
                msg.autopilot,
            )
            return
    if fallback_sys is not None:
        m.target_system = fallback_sys
        m.target_component = fallback_comp
        logger.warning(
            "MAVLink using first non-invalid heartbeat sys=%s comp=%s (no vehicle type match)",
            fallback_sys,
            fallback_comp,
        )
        return
    raise TimeoutError("No usable HEARTBEAT (autopilot not INVALID) within timeout")


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
        _wait_vehicle_heartbeat(m, timeout_s=timeout_s)
        _master = m
        _master_conn_str = connection_string
        return m


def _command_long_send(
    master: mavutil.mavlink_connection,
    command: int,
    p1: float,
    p2: float,
    p3: float,
    p4: float,
    p5: float,
    p6: float,
    p7: float,
) -> None:
    """Send COMMAND_LONG to the autopilot component (not the heartbeat source if that was a GCS)."""
    master.mav.command_long_send(
        master.target_system,
        _AP_COMP,
        command,
        0,
        p1,
        p2,
        p3,
        p4,
        p5,
        p6,
        p7,
    )


def _expect_command_ack(
    master: mavutil.mavlink_connection,
    expect_command: int,
    *,
    timeout_s: float = 3.0,
) -> None:
    """Wait for COMMAND_ACK for expect_command; raise if rejected or timeout."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        ack = master.recv_match(type="COMMAND_ACK", blocking=True, timeout=0.5)
        if ack is None:
            continue
        if ack.command != expect_command:
            continue
        res = ack.result
        if res in (
            mavutil.mavlink.MAV_RESULT_ACCEPTED,
            mavutil.mavlink.MAV_RESULT_IN_PROGRESS,
        ):
            logger.info("COMMAND_ACK ok command=%s result=%s", expect_command, res)
            return
        raise RuntimeError(f"MAVLink command {expect_command} rejected: result={res}")
    logger.warning("COMMAND_ACK timeout for command=%s (command may still have executed)", expect_command)


def set_camera_tilt_pitch_deg(master: mavutil.mavlink_connection, pitch_deg: float, limits: tuple[float, float]) -> None:
    lo, hi = limits
    pitch_deg = max(lo, min(hi, pitch_deg))
    cmd = mavutil.mavlink.MAV_CMD_DO_GIMBAL_MANAGER_TILTPAN
    _command_long_send(master, cmd, pitch_deg, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    logger.info("MAV_CMD_DO_GIMBAL_MANAGER_TILTPAN pitch_deg=%s (clamped to %s..%s)", pitch_deg, lo, hi)
    _expect_command_ack(master, cmd, timeout_s=3.0)


def set_camera_tilt_pitch(settings: AppSettings, pitch_deg: float) -> None:
    m = get_master(settings.mavlink_connection)
    set_camera_tilt_pitch_deg(m, pitch_deg, (settings.tilt_pitch_min_deg, settings.tilt_pitch_max_deg))


def center_camera_tilt(settings: AppSettings) -> None:
    set_camera_tilt_pitch(settings, 0.0)


def brightness_to_pwm(brightness_pct: int, pwm_min: int, pwm_max: int) -> int:
    b = max(0, min(100, brightness_pct))
    return int(round(pwm_min + (pwm_max - pwm_min) * (b / 100.0)))


def set_light_pwm(settings: AppSettings, brightness_pct: int) -> None:
    """Map 0-100% to settings light_pwm_min..max (recipes / saved behaviour)."""
    m = get_master(settings.mavlink_connection)
    pwm = brightness_to_pwm(brightness_pct, settings.light_pwm_min, settings.light_pwm_max)
    _do_set_servo_us(m, settings.light_servo_channel, pwm)


def set_light_manual_brightness(settings: AppSettings, brightness_pct: int) -> None:
    """Manual UI: 0% -> 1100 us off, 100% -> 1900 us full brightness."""
    m = get_master(settings.mavlink_connection)
    pwm = brightness_to_pwm(brightness_pct, MANUAL_LIGHT_PWM_MIN, MANUAL_LIGHT_PWM_MAX)
    _do_set_servo_us(m, settings.light_servo_channel, pwm)


def _do_set_servo_us(master: mavutil.mavlink_connection, servo_instance: int, pwm_us: int) -> None:
    """MAV_CMD_DO_SET_SERVO: param1 = servo output instance, param2 = PWM microseconds."""
    pwm_us = int(max(800, min(2200, pwm_us)))
    cmd = mavutil.mavlink.MAV_CMD_DO_SET_SERVO
    _command_long_send(
        master,
        cmd,
        float(servo_instance),
        float(pwm_us),
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
    )
    logger.info("MAV_CMD_DO_SET_SERVO instance=%s pwm_us=%s", servo_instance, pwm_us)
    _expect_command_ack(master, cmd, timeout_s=3.0)


def mavlink_status(settings: AppSettings) -> dict:
    """Lightweight status without forcing long blocking connect."""
    try:
        m = get_master(settings.mavlink_connection, timeout_s=3.0)
        return {
            "connected": True,
            "target_system": m.target_system,
            "target_component": m.target_component,
            "command_component": _AP_COMP,
            "connection": settings.mavlink_connection,
        }
    except Exception as e:
        return {"connected": False, "error": str(e), "connection": settings.mavlink_connection}
