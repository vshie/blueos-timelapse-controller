"""GStreamer-based RTSP probe, snapshot, and recording."""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
from pathlib import Path

logger = logging.getLogger(__name__)


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
    """Start background recording to MPEG-TS. Caller must terminate/wait."""
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


def remux_ts_to_mp4(ts_path: Path, mp4_path: Path, *, timeout_s: float = 600.0) -> None:
    ffmpeg = _which("ffmpeg")
    if not ffmpeg:
        logger.warning("ffmpeg not found; skip remux")
        return
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(ts_path),
        "-c",
        "copy",
        str(mp4_path),
    ]
    subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s, check=False)


def stamp_basename(prefix: str) -> str:
    ts = time.strftime("%Y%m%d_%H%M%S")
    pfx = "".join(c for c in prefix if c.isalnum() or c in ("-", "_"))[:48]
    return f"{pfx}_{ts}" if pfx else ts
