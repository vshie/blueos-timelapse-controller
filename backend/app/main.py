"""FastAPI entry: REST API, static Vue UI, BlueOS register_service."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.config_env import get_settings
from app.models import AppSettings, Recipe, SchedulerStateResponse
from app.scheduler_service import SchedulerService
from app.storage import Storage
from app.timeutil import now_local

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_storage: Storage | None = None
_scheduler: SchedulerService | None = None


def get_storage() -> Storage:
    assert _storage is not None
    return _storage


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _storage, _scheduler
    s = get_settings()
    data = s.data_dir
    Path(data).mkdir(parents=True, exist_ok=True)
    _storage = Storage(data)
    _storage.ensure_dirs()
    _scheduler = SchedulerService(storage_factory=get_storage)
    _scheduler.start()
    logger.info("Timelapse Controller started, data_dir=%s", data)
    yield
    if _scheduler:
        _scheduler.stop()
    logger.info("shutdown complete")


app = FastAPI(title="Timelapse Controller", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class RtspProbeBody(BaseModel):
    url: str = ""
    latency_ms: int | None = None
    use_tcp: bool | None = None


class ManualLightBody(BaseModel):
    """0% = 1100 us (off), 100% = 1900 us (full) for the manual light test."""

    brightness_pct: int = Field(ge=0, le=100)


class ManualTiltPitchBody(BaseModel):
    """Tilt in degrees; 0 = center, clamped to settings tilt range (default -70..70)."""

    pitch_deg: float = Field(ge=-90.0, le=90.0)


class ManualRecordBody(BaseModel):
    seconds: float = Field(default=30.0, gt=0, le=7200)


_WEEKDAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


@app.get("/api/v1/status")
def api_status():
    st = _scheduler.get_state() if _scheduler is not None else SchedulerStateResponse()
    settings = get_storage().load_settings()
    from app import mavlink_control

    now = now_local(settings)
    tz_label = now.strftime("%Z") or settings.timezone or now.strftime("%z")
    return {
        "scheduler": st.model_dump(),
        "mavlink": mavlink_control.mavlink_status(settings),
        "device_time": {
            "iso": now.isoformat(timespec="seconds"),
            "hm": now.strftime("%H:%M"),
            "date": now.strftime("%Y-%m-%d"),
            "weekday": _WEEKDAY_LABELS[now.weekday()],
            "weekday_index": now.weekday(),
            "tz": tz_label,
            "tz_name": settings.timezone or "",
        },
        "settings_summary": {
            "default_rtsp_url_set": bool(settings.default_rtsp_url),
            "mavlink_connection": settings.mavlink_connection,
        },
    }


@app.get("/api/v1/settings", response_model=AppSettings)
def get_settings_api():
    return get_storage().load_settings()


@app.put("/api/v1/settings", response_model=AppSettings)
def put_settings_api(body: AppSettings):
    get_storage().save_settings(body)
    return body


@app.get("/api/v1/recipes", response_model=list[Recipe])
def list_recipes():
    return get_storage().list_recipes()


@app.post("/api/v1/recipes", response_model=Recipe)
def create_recipe(body: Recipe):
    body.id = ""
    return get_storage().save_recipe(body)


@app.get("/api/v1/recipes/{recipe_id}", response_model=Recipe)
def get_recipe(recipe_id: str):
    r = get_storage().load_recipe(recipe_id)
    if not r:
        raise HTTPException(404, "recipe not found")
    return r


@app.put("/api/v1/recipes/{recipe_id}", response_model=Recipe)
def update_recipe(recipe_id: str, body: Recipe):
    body.id = recipe_id
    return get_storage().save_recipe(body)


@app.delete("/api/v1/recipes/{recipe_id}")
def delete_recipe(recipe_id: str):
    if not get_storage().delete_recipe(recipe_id):
        raise HTTPException(404, "recipe not found")
    return {"ok": True}


@app.post("/api/v1/probe-rtsp")
def probe_rtsp(body: RtspProbeBody):
    settings = get_storage().load_settings()
    url = body.url.strip() or settings.default_rtsp_url
    lat = body.latency_ms if body.latency_ms is not None else settings.gstreamer_latency_ms
    tcp = settings.use_tcp_rtsp if body.use_tcp is None else body.use_tcp
    from app import capture

    return capture.probe_rtsp(url, latency_ms=lat, tcp=tcp)


@app.post("/api/v1/manual/tilt-center")
def manual_tilt_center():
    settings = get_storage().load_settings()
    from app import mavlink_control

    try:
        mavlink_control.center_camera_tilt(settings)
        return {"ok": True, "pitch_deg": 0.0}
    except Exception as e:
        raise HTTPException(500, str(e)) from e


@app.post("/api/v1/manual/tilt")
def manual_tilt(body: ManualTiltPitchBody):
    settings = get_storage().load_settings()
    from app import mavlink_control

    lo, hi = settings.tilt_pitch_min_deg, settings.tilt_pitch_max_deg
    pitch = max(lo, min(hi, body.pitch_deg))
    try:
        mavlink_control.set_camera_tilt_pitch(settings, pitch)
        return {"ok": True, "pitch_deg": pitch}
    except Exception as e:
        raise HTTPException(500, str(e)) from e


@app.post("/api/v1/manual/light")
def manual_light(body: ManualLightBody):
    settings = get_storage().load_settings()
    from app import mavlink_control

    try:
        mavlink_control.set_light_manual_brightness(settings, body.brightness_pct)
        pwm = mavlink_control.brightness_to_pwm(
            body.brightness_pct,
            mavlink_control.MANUAL_LIGHT_PWM_MIN,
            mavlink_control.MANUAL_LIGHT_PWM_MAX,
        )
        return {"ok": True, "brightness_pct": body.brightness_pct, "pwm_us": pwm, "servo": settings.light_servo_channel}
    except Exception as e:
        raise HTTPException(500, str(e)) from e


@app.post("/api/v1/manual/snapshot")
def manual_snapshot():
    settings = get_storage().load_settings()
    if not settings.default_rtsp_url:
        raise HTTPException(400, "Set default_rtsp_url in settings first")
    from app import capture

    base = capture.stamp_basename("manual")
    out = get_storage().captures_dir / f"{base}.jpg"
    try:
        capture.capture_snapshot(
            settings.default_rtsp_url,
            out,
            latency_ms=settings.gstreamer_latency_ms,
            tcp=settings.use_tcp_rtsp,
        )
        return {"ok": True, "path": str(out)}
    except Exception as e:
        raise HTTPException(500, str(e)) from e


@app.post("/api/v1/manual/record")
def manual_record(body: ManualRecordBody):
    settings = get_storage().load_settings()
    if not settings.default_rtsp_url:
        raise HTTPException(400, "Set default_rtsp_url in settings first")
    from app import capture

    base = capture.stamp_basename("manual")
    cap_dir = get_storage().captures_dir
    ts_path = cap_dir / f"{base}.ts"
    mp4_path = cap_dir / f"{base}.mp4"
    capture.record_for_duration(
        settings.default_rtsp_url,
        ts_path,
        duration_s=float(body.seconds),
        latency_ms=settings.gstreamer_latency_ms,
        tcp=settings.use_tcp_rtsp,
    )
    ok, final_path = capture.finalise_recording(ts_path, mp4_path, duration_s=float(body.seconds))
    if not ok:
        return {
            "ok": False,
            "ts": str(final_path),
            "mp4": None,
            "error": "remux failed; raw .ts preserved",
        }
    return {"ok": True, "mp4": str(final_path)}


def _static_dir() -> Path:
    env = get_settings().static_dir
    if env:
        return Path(env)
    return Path(__file__).resolve().parent / "static"


@app.get("/register_service")
def register_service():
    p = _static_dir() / "register_service"
    if p.exists():
        return FileResponse(p, media_type="application/json")
    return JSONResponse(
        {
            "name": "Timelapse Controller",
            "description": "Scheduled RTSP capture with MAVLink tilt and light control",
            "icon": "mdi-camera-timer",
            "company": "vshie",
            "version": "0.1.0",
            "webpage": "https://github.com/vshie/blueos-timelapse-controller",
            "api": "",
            "works_in_relative_paths": True,
        }
    )


static = _static_dir()
if static.exists():
    app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def spa_fallback(full_path: str):
        if full_path.startswith("api/") or full_path.startswith("register_service"):
            raise HTTPException(404)
        index = static / "index.html"
        if index.exists():
            return FileResponse(index)
        raise HTTPException(404, "UI not built; run frontend build")
