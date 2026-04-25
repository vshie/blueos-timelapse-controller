"""MAVLink vehicle control via pymavlink."""

from __future__ import annotations

import logging
import math
import threading
import time
from typing import TYPE_CHECKING

from pymavlink import mavutil

if TYPE_CHECKING:
    from app.models import AppSettings

logger = logging.getLogger(__name__)

# Single I/O lock serialises all sends/recvs on the shared mavlink connection
# (pymavlink's mav.* are not thread-safe). It also protects connection creation.
_lock = threading.Lock()
_master: mavutil.mavlink_connection | None = None
_master_conn_str: str | None = None

# Commands are sent to the autopilot component; heartbeats from GCS/routers often use other components.
_AP_COMP = mavutil.mavlink.MAV_COMP_ID_AUTOPILOT1

# Source identity we present to the MAVLink network for our outgoing packets.
_SRC_SYS = 255
_SRC_COMP = mavutil.mavlink.MAV_COMP_ID_USER1

def _resolve_mavlink_const(*names: str) -> int | None:
    """Return the first attribute on mavutil.mavlink that exists, else None.

    Lets us tolerate dialect differences (e.g. PITCHYAW renamed from TILTPAN, ROVER vs GROUND_ROVER).
    """
    for n in names:
        v = getattr(mavutil.mavlink, n, None)
        if v is not None:
            return v
    return None


# MAV_CMD_DO_GIMBAL_MANAGER_TILTPAN (the name BlueOS Cockpit / camera setup page uses)
# was renamed to MAV_CMD_DO_GIMBAL_MANAGER_PITCHYAW in newer MAVLink dialects.
# Both share the same numeric command id (1000) and param layout, so either symbol
# produces an identical COMMAND_LONG on the wire.
_CMD_GIMBAL_TILTPAN = _resolve_mavlink_const(
    "MAV_CMD_DO_GIMBAL_MANAGER_TILTPAN",
    "MAV_CMD_DO_GIMBAL_MANAGER_PITCHYAW",
)

# Vehicle MAV_TYPE values vary by dialect (e.g. MAV_TYPE_ROVER vs MAV_TYPE_GROUND_ROVER);
# resolve dynamically and skip unknowns so a single dialect missing one constant doesn't break us.
_VEHICLE_MAV_TYPES = {
    t
    for t in (
        getattr(mavutil.mavlink, name, None)
        for name in (
            "MAV_TYPE_SUBMARINE",
            "MAV_TYPE_SURFACE_BOAT",
            "MAV_TYPE_GROUND_ROVER",
            "MAV_TYPE_ROVER",
            "MAV_TYPE_QUADROTOR",
            "MAV_TYPE_HEXAROTOR",
            "MAV_TYPE_OCTOROTOR",
            "MAV_TYPE_TRICOPTER",
            "MAV_TYPE_COAXIAL",
            "MAV_TYPE_HELICOPTER",
            "MAV_TYPE_FIXED_WING",
        )
    )
    if t is not None
}

# Manual light test: 0% -> 1100 us (off), 100% -> 1900 us (full)
MANUAL_LIGHT_PWM_MIN = 1100
MANUAL_LIGHT_PWM_MAX = 1900

_hb_stop = threading.Event()
_hb_thread: threading.Thread | None = None


def _send_local_heartbeat(m: mavutil.mavlink_connection) -> None:
    """Emit a GCS heartbeat. Required for udpout/tcp upstreams (mavlink-server / mavlink-router)
    to register us as a client and start forwarding traffic to our endpoint."""
    m.mav.heartbeat_send(
        mavutil.mavlink.MAV_TYPE_GCS,
        mavutil.mavlink.MAV_AUTOPILOT_INVALID,
        0,
        0,
        0,
    )


def _heartbeat_loop(m: mavutil.mavlink_connection) -> None:
    """Keep our registration alive with the upstream router by sending 1 Hz heartbeats."""
    while not _hb_stop.wait(1.0):
        try:
            with _lock:
                if _master is not m:
                    return
                _send_local_heartbeat(m)
        except Exception as e:
            logger.debug("heartbeat send: %s", e)
            return


def _stop_heartbeat_thread() -> None:
    global _hb_thread
    _hb_stop.set()
    if _hb_thread is not None:
        _hb_thread.join(timeout=2)
    _hb_thread = None


def _start_heartbeat_thread(m: mavutil.mavlink_connection) -> None:
    global _hb_thread
    _stop_heartbeat_thread()
    _hb_stop.clear()
    _hb_thread = threading.Thread(target=_heartbeat_loop, args=(m,), name="mavlink-hb", daemon=True)
    _hb_thread.start()


def disconnect() -> None:
    global _master, _master_conn_str
    _stop_heartbeat_thread()
    with _lock:
        if _master is not None:
            try:
                _master.close()
            except Exception as e:
                logger.debug("close mavlink: %s", e)
        _master = None
        _master_conn_str = None


def _wait_vehicle_heartbeat(m: mavutil.mavlink_connection, timeout_s: float) -> None:
    """Pick target system/component from a vehicle autopilot heartbeat (not GCS/router).

    Sends our own heartbeat once per second while waiting so the upstream router
    (mavlink-server / mavlink-router) registers us as a client and forwards traffic.
    """
    _send_local_heartbeat(m)
    deadline = time.monotonic() + timeout_s
    next_hb = time.monotonic() + 1.0
    fallback_sys = None
    fallback_comp = None
    while time.monotonic() < deadline:
        msg = m.recv_match(type="HEARTBEAT", blocking=True, timeout=0.5)
        now = time.monotonic()
        if now >= next_hb:
            try:
                _send_local_heartbeat(m)
            except Exception as e:
                logger.debug("heartbeat send during wait: %s", e)
            next_hb = now + 1.0
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
        if msg.type in _VEHICLE_MAV_TYPES:
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
    """Return a connected mavlink connection (singleton per connection string).

    Caller must hold no locks; this acquires the I/O lock for the duration of connect.
    """
    global _master, _master_conn_str
    with _lock:
        if _master is not None and _master_conn_str == connection_string:
            return _master
        if _master is not None:
            try:
                _master.close()
            except Exception:
                pass
            _master = None
            _master_conn_str = None
        logger.info("MAVLink connecting: %s", connection_string)
        m = mavutil.mavlink_connection(
            connection_string,
            source_system=_SRC_SYS,
            source_component=_SRC_COMP,
        )
        _wait_vehicle_heartbeat(m, timeout_s=timeout_s)
        _master = m
        _master_conn_str = connection_string
    _start_heartbeat_thread(_master)
    return _master


def _command_long_send_locked(
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
    """Send COMMAND_LONG to the autopilot component. Caller MUST hold _lock."""
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


def _expect_command_ack_locked(
    master: mavutil.mavlink_connection,
    expect_command: int,
    *,
    timeout_s: float = 3.0,
) -> None:
    """Wait for COMMAND_ACK for expect_command. Caller MUST hold _lock."""
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


def _send_command_with_ack(
    master: mavutil.mavlink_connection,
    command: int,
    params: tuple[float, float, float, float, float, float, float],
    *,
    ack_timeout_s: float = 3.0,
) -> None:
    """Send a COMMAND_LONG and wait for COMMAND_ACK, holding the I/O lock for both."""
    with _lock:
        _command_long_send_locked(master, command, *params)
        _expect_command_ack_locked(master, command, timeout_s=ack_timeout_s)


def set_camera_tilt_pitch_deg(master: mavutil.mavlink_connection, pitch_deg: float, limits: tuple[float, float]) -> None:
    if _CMD_GIMBAL_TILTPAN is None:
        raise RuntimeError(
            "Neither MAV_CMD_DO_GIMBAL_MANAGER_TILTPAN nor MAV_CMD_DO_GIMBAL_MANAGER_PITCHYAW exists in this MAVLink dialect"
        )
    lo, hi = limits
    pitch_deg = max(lo, min(hi, pitch_deg))
    # Logged as TILTPAN to match BlueOS Cockpit / camera-setup nomenclature; on the wire this is
    # COMMAND_LONG cmd=1000 with param1=pitch — identical to what Cockpit sends.
    logger.info("MAV_CMD_DO_GIMBAL_MANAGER_TILTPAN pitch_deg=%s (clamped to %s..%s)", pitch_deg, lo, hi)
    _send_command_with_ack(master, _CMD_GIMBAL_TILTPAN, (pitch_deg, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0))


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


def set_light_pwm_us(settings: AppSettings, pwm_us: int) -> None:
    """Set light servo to an exact PWM µs value (used to restore prior snapshotted PWM)."""
    m = get_master(settings.mavlink_connection)
    _do_set_servo_us(m, settings.light_servo_channel, int(pwm_us))


def set_light_manual_brightness(settings: AppSettings, brightness_pct: int) -> None:
    """Manual UI: 0% -> 1100 us off, 100% -> 1900 us full brightness."""
    m = get_master(settings.mavlink_connection)
    pwm = brightness_to_pwm(brightness_pct, MANUAL_LIGHT_PWM_MIN, MANUAL_LIGHT_PWM_MAX)
    _do_set_servo_us(m, settings.light_servo_channel, pwm)


def _do_set_servo_us(master: mavutil.mavlink_connection, servo_instance: int, pwm_us: int) -> None:
    """MAV_CMD_DO_SET_SERVO: param1 = servo output instance, param2 = PWM microseconds."""
    pwm_us = int(max(800, min(2200, pwm_us)))
    cmd = mavutil.mavlink.MAV_CMD_DO_SET_SERVO
    logger.info("MAV_CMD_DO_SET_SERVO instance=%s pwm_us=%s", servo_instance, pwm_us)
    _send_command_with_ack(
        master,
        cmd,
        (float(servo_instance), float(pwm_us), 0.0, 0.0, 0.0, 0.0, 0.0),
    )


def _request_message_interval_locked(
    master: mavutil.mavlink_connection,
    message_id: int,
    interval_us: int,
) -> None:
    """Best-effort SET_MESSAGE_INTERVAL; ignore ACK to keep the receive window for the caller."""
    try:
        _command_long_send_locked(
            master,
            mavutil.mavlink.MAV_CMD_SET_MESSAGE_INTERVAL,
            float(message_id),
            float(interval_us),
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
        )
    except Exception as e:
        logger.debug("SET_MESSAGE_INTERVAL request failed for id=%s: %s", message_id, e)


def read_servo_pwm(
    master: mavutil.mavlink_connection,
    servo_channel: int,
    *,
    timeout_s: float = 1.5,
) -> int | None:
    """Return current PWM µs for SERVO_OUTPUT_RAW channel `servo_channel` (1-16).

    ArduSub streams SERVO_OUTPUT_RAW at ~2 Hz by default, so a short receive window
    is normally enough. Returns None on timeout.
    """
    if servo_channel < 1 or servo_channel > 16:
        logger.warning("read_servo_pwm: channel %s out of 1..16 range", servo_channel)
        return None
    field = f"servo{servo_channel}_raw"
    deadline = time.monotonic() + timeout_s
    requested_stream = False
    try:
        with _lock:
            while time.monotonic() < deadline:
                msg = master.recv_match(type="SERVO_OUTPUT_RAW", blocking=True, timeout=0.5)
                if msg is None:
                    if not requested_stream:
                        _request_message_interval_locked(
                            master,
                            mavutil.mavlink.MAVLINK_MSG_ID_SERVO_OUTPUT_RAW,
                            200000,
                        )
                        requested_stream = True
                    continue
                pwm = getattr(msg, field, None)
                if pwm is None:
                    continue
                try:
                    pwm_int = int(pwm)
                except (TypeError, ValueError):
                    continue
                if pwm_int <= 0:
                    continue
                return pwm_int
    except Exception as e:
        logger.warning("read_servo_pwm error: %s", e)
        return None
    logger.warning("read_servo_pwm timeout on servo%s_raw after %.2fs", servo_channel, timeout_s)
    return None


def read_mount_pitch_deg(
    master: mavutil.mavlink_connection,
    *,
    timeout_s: float = 1.5,
) -> float | None:
    """Return current gimbal pitch in degrees, snapshotted from MAVLink.

    Tries MOUNT_STATUS (pointing_a is centi-degrees), falls back to
    GIMBAL_DEVICE_ATTITUDE_STATUS (quaternion -> euler pitch). Returns None on timeout.
    """
    deadline = time.monotonic() + timeout_s
    requested_stream = False
    types = ["MOUNT_STATUS", "GIMBAL_DEVICE_ATTITUDE_STATUS"]
    try:
        with _lock:
            while time.monotonic() < deadline:
                msg = master.recv_match(type=types, blocking=True, timeout=0.5)
                if msg is None:
                    if not requested_stream:
                        ms_id = getattr(mavutil.mavlink, "MAVLINK_MSG_ID_MOUNT_STATUS", None)
                        gd_id = getattr(
                            mavutil.mavlink,
                            "MAVLINK_MSG_ID_GIMBAL_DEVICE_ATTITUDE_STATUS",
                            None,
                        )
                        if ms_id is not None:
                            _request_message_interval_locked(master, ms_id, 200000)
                        if gd_id is not None:
                            _request_message_interval_locked(master, gd_id, 200000)
                        requested_stream = True
                    continue
                t = msg.get_type()
                if t == "MOUNT_STATUS":
                    pa = getattr(msg, "pointing_a", None)
                    if pa is None:
                        continue
                    return float(pa) / 100.0
                if t == "GIMBAL_DEVICE_ATTITUDE_STATUS":
                    q = getattr(msg, "q", None)
                    if not q or len(q) < 4:
                        continue
                    w, x, y, z = float(q[0]), float(q[1]), float(q[2]), float(q[3])
                    sinp = 2.0 * (w * y - z * x)
                    sinp = max(-1.0, min(1.0, sinp))
                    return math.degrees(math.asin(sinp))
    except Exception as e:
        logger.warning("read_mount_pitch_deg error: %s", e)
        return None
    logger.warning("read_mount_pitch_deg timeout after %.2fs", timeout_s)
    return None


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
