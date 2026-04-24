"""GStreamer-based RTSP probe, snapshot, and recording."""

from __future__ import annotations

import logging
import shutil
import signal
import subprocess
import time
from pathlib import Path

logger = logging.getLogger(__name__)

RECORD_STARTUP_BUFFER_S = 2.5
"""Extra wall-clock seconds given to the pipeline to cover RTSP handshake + jitterbuffer
priming before we send EOS. After remux we trim back to the caller-requested duration."""

RECORD_EOS_GRACE_S = 15.0
"""Seconds we wait for gst-launch to drain and exit after SIGINT-triggered EOS."""


def _which(name: str) -> str | None:
    return shutil.which(name)


def _rtspsrc_args(url: str, *, latency_ms: int, tcp: bool) -> list[str]:
    proto = "tcp" if tcp else "udp"
    return [
        "rtspsrc",
        f"location={url}",
        f"latency={latency_ms}",
        f"protocols={proto}",
        "drop-on-latency=true",
        "!",
    ]


def probe_rtsp(url: str, *, latency_ms: int = 300, tcp: bool = True, timeout_s: float = 15.0) -> dict:
    """Try a short GStreamer pipeline to verify RTSP is readable."""
    if not url:
        return {"ok": False, "error": "empty RTSP URL"}
    gst_launch = _which("gst-launch-1.0")
    if not gst_launch:
        return {"ok": False, "error": "gst-launch-1.0 not found"}
    cmd = [
        gst_launch,
        "-q",
        "-e",
        *_rtspsrc_args(url, latency_ms=latency_ms, tcp=tcp),
        "decodebin",
        "!",
        "videoconvert",
        "!",
        "fakesink",
        "sync=false",
        "async=false",
    ]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
        ok = p.returncode == 0
        err = (p.stderr or p.stdout or "").strip()[-2000:]
        return {"ok": ok, "returncode": p.returncode, "stderr_tail": err}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"probe timeout after {timeout_s}s"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def capture_snapshot(
    url: str,
    out_path: Path,
    *,
    latency_ms: int = 300,
    tcp: bool = True,
    timeout_s: float = 45.0,
) -> None:
    """Save one JPEG frame. Uses ffmpeg for reliable single-frame RTSP grabs; video uses GStreamer."""
    del latency_ms  # RTSP latency handled by ffmpeg internally
    ffmpeg = _which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found (required for snapshot)")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()
    transport = "tcp" if tcp else "udp"
    cmd = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-rtsp_transport",
        transport,
        "-i",
        url,
        "-frames:v",
        "1",
        "-q:v",
        "2",
        str(out_path),
    ]
    logger.info("snapshot ffmpeg -> %s", out_path)
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    if p.returncode != 0 or not out_path.exists():
        msg = (p.stderr or p.stdout or "snapshot failed")[-4000:]
        raise RuntimeError(msg)


def record_video_ts(
    url: str,
    out_ts: Path,
    *,
    latency_ms: int = 300,
    tcp: bool = True,
) -> subprocess.Popen:
    """Start background recording to MPEG-TS. Caller must EOS via `stop_recording`."""
    gst_launch = _which("gst-launch-1.0")
    if not gst_launch:
        raise RuntimeError("gst-launch-1.0 not found")
    out_ts.parent.mkdir(parents=True, exist_ok=True)
    if out_ts.exists():
        out_ts.unlink()
    cmd = [
        gst_launch,
        "-e",
        *_rtspsrc_args(url, latency_ms=latency_ms, tcp=tcp),
        "rtph264depay",
        "!",
        "h264parse",
        "!",
        "mpegtsmux",
        "!",
        "filesink",
        f"location={str(out_ts)}",
        "sync=false",
    ]
    logger.info("record start -> %s", out_ts)
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)


def stop_recording(proc: subprocess.Popen, *, grace_s: float = RECORD_EOS_GRACE_S) -> None:
    """Signal gst-launch to emit EOS and drain. Uses SIGINT because `-e` only
    catches SIGINT (not SIGTERM) to trigger a clean EOS+shutdown; this is what
    keeps trailing frames instead of truncating the muxer."""
    if proc.poll() is not None:
        return
    try:
        proc.send_signal(signal.SIGINT)
    except Exception:
        logger.exception("SIGINT to gst-launch failed; falling back to terminate")
        proc.terminate()
    try:
        proc.wait(timeout=grace_s)
        return
    except subprocess.TimeoutExpired:
        logger.warning("gst-launch did not exit after %.1fs of EOS; terminating", grace_s)
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        proc.kill()


def record_for_duration(
    url: str,
    out_ts: Path,
    duration_s: float,
    *,
    latency_ms: int = 300,
    tcp: bool = True,
    startup_buffer_s: float = RECORD_STARTUP_BUFFER_S,
) -> None:
    """Record to `out_ts` for `duration_s + startup_buffer_s` wall-clock seconds,
    then send a clean EOS so trailing frames are flushed. The caller is expected to
    remux with `remux_ts_to_mp4(..., trim_to_s=duration_s)` to trim back to the
    exact requested duration."""
    proc = record_video_ts(url, out_ts, latency_ms=latency_ms, tcp=tcp)
    try:
        target = max(0.1, duration_s + max(0.0, startup_buffer_s))
        t0 = time.monotonic()
        while time.monotonic() - t0 < target:
            if proc.poll() is not None:
                break
            time.sleep(0.25)
    finally:
        stop_recording(proc)


def remux_ts_to_mp4(
    ts_path: Path,
    mp4_path: Path,
    *,
    timeout_s: float = 600.0,
    trim_to_s: float | None = None,
) -> bool:
    """Remux TS to MP4 (stream copy). If `trim_to_s` is set, clamp output length
    with ffmpeg `-t`. Returns True on success (mp4 exists and ffmpeg rc==0)."""
    ffmpeg = _which("ffmpeg")
    if not ffmpeg:
        logger.warning("ffmpeg not found; skip remux")
        return False
    if not ts_path.exists() or ts_path.stat().st_size == 0:
        logger.warning("ts missing/empty, skip remux: %s", ts_path)
        return False
    cmd: list[str] = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(ts_path),
        "-c",
        "copy",
    ]
    if trim_to_s is not None and trim_to_s > 0:
        cmd += ["-t", f"{trim_to_s:.3f}"]
    cmd.append(str(mp4_path))
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s, check=False)
    except subprocess.TimeoutExpired:
        logger.exception("ffmpeg remux timed out after %.0fs", timeout_s)
        return False
    if p.returncode != 0 or not mp4_path.exists():
        logger.warning(
            "ffmpeg remux failed rc=%s: %s",
            p.returncode,
            (p.stderr or p.stdout or "")[-1000:],
        )
        return False
    return True


def finalise_recording(
    ts_path: Path,
    mp4_path: Path,
    duration_s: float | None,
    *,
    timeout_s: float = 600.0,
) -> tuple[bool, Path]:
    """Remux `.ts` into `.mp4` trimmed to `duration_s`. On success delete the `.ts`
    and return (True, mp4). On failure keep the `.ts` and return (False, ts).
    This keeps the source data available when the remux step itself errors."""
    ok = remux_ts_to_mp4(ts_path, mp4_path, timeout_s=timeout_s, trim_to_s=duration_s)
    if ok:
        try:
            ts_path.unlink(missing_ok=True)
        except Exception:
            logger.exception("failed to remove transient ts: %s", ts_path)
        return True, mp4_path
    return False, ts_path


def stamp_basename(prefix: str) -> str:
    ts = time.strftime("%Y%m%d_%H%M%S")
    pfx = "".join(c for c in prefix if c.isalnum() or c in ("-", "_"))[:48]
    return f"{pfx}_{ts}" if pfx else ts
