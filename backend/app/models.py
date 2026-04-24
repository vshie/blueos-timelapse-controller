"""Pydantic models for settings and recipes."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class AppSettings(BaseModel):
    """Persisted global settings (stored as config.json)."""

    default_rtsp_url: str = Field(default="", description="Default RTSP URL for capture")
    mavlink_connection: str = Field(
        default="udpout:host.docker.internal:14550",
        description=(
            "pymavlink connection string. With BlueOS bridge networking the container reaches the host "
            "via host.docker.internal; udpout: registers as a client with mavlink-server on UDP 14550."
        ),
    )
    light_servo_channel: int = Field(default=13, ge=1, le=32)
    light_pwm_min: int = Field(default=1100, ge=800, le=2200)
    light_pwm_max: int = Field(default=1900, ge=800, le=2200)
    tilt_pitch_min_deg: float = Field(default=-70.0, description="Gimbal tilt min (deg), e.g. -70")
    tilt_pitch_max_deg: float = Field(default=70.0, description="Gimbal tilt max (deg), e.g. 70; 0 = center")
    gstreamer_latency_ms: int = Field(default=300, ge=0, le=5000)
    use_tcp_rtsp: bool = Field(default=True, description="Use RTSP over TCP (rtspsrc protocols=tcp)")
    timezone: str = Field(
        default="",
        description="IANA timezone (e.g. 'Pacific/Honolulu'). Empty = use container/host local time.",
    )

    @model_validator(mode="after")
    def pwm_range(self):
        if self.light_pwm_max < self.light_pwm_min:
            raise ValueError("light_pwm_max must be >= light_pwm_min")
        return self

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, v: str) -> str:
        if not v:
            return ""
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(v)
        except ZoneInfoNotFoundError as e:
            raise ValueError(
                f'unknown IANA timezone "{v}"; expected values like "Pacific/Honolulu" or "UTC"'
            ) from e
        return v


class RecipeActions(BaseModel):
    center_camera_tilt: bool = False
    light_brightness_pct: int | None = Field(
        default=None,
        description="If set, command light to this brightness (0-100); if null, do not change light",
    )
    take_snapshot: bool = False
    record_video_minutes: float | None = Field(
        default=None,
        description="If set and > 0, record for this many minutes",
    )

    @field_validator("light_brightness_pct")
    @classmethod
    def light_pct_range(cls, v: int | None) -> int | None:
        if v is None:
            return None
        if v < 0 or v > 100:
            raise ValueError("light_brightness_pct must be between 0 and 100")
        return v

    @field_validator("record_video_minutes")
    @classmethod
    def record_positive_or_none(cls, v: float | None) -> float | None:
        if v is not None and v <= 0:
            return None
        return v


class Recipe(BaseModel):
    id: str = ""
    name: str = "Untitled"
    enabled: bool = True
    days_of_week: list[int] = Field(
        default_factory=lambda: [0, 1, 2, 3, 4, 5, 6],
        description="Weekdays 0=Monday .. 6=Sunday (Python date.weekday)",
    )
    times_local: list[str] = Field(
        default_factory=lambda: ["12:00"],
        description='Local times "HH:MM" (24h)',
    )
    rtsp_url: str | None = Field(default=None, description="Override default RTSP if set")
    filename_prefix: str = Field(default="", max_length=64)
    actions: RecipeActions = Field(default_factory=RecipeActions)

    @field_validator("days_of_week")
    @classmethod
    def valid_weekdays(cls, v: list[int]) -> list[int]:
        out = sorted({int(d) for d in v})
        for d in out:
            if d < 0 or d > 6:
                raise ValueError("days_of_week must be in 0..6")
        if not out:
            raise ValueError("at least one weekday required")
        return out

    @field_validator("times_local")
    @classmethod
    def valid_times(cls, v: list[str]) -> list[str]:
        """Accept lenient HH:MM 24h input (e.g. '8:00', '08:00', '08:00 ') and normalise to 'HH:MM'."""
        import re

        if not v:
            raise ValueError("at least one time required")
        pat = re.compile(r"^\s*([01]?\d|2[0-3]):([0-5]?\d)\s*$")
        out: set[str] = set()
        for t in v:
            m = pat.match(t)
            if not m:
                raise ValueError(f'invalid time "{t}", expected HH:MM 24h (e.g. 08:00 or 8:00)')
            hh, mm = int(m.group(1)), int(m.group(2))
            out.add(f"{hh:02d}:{mm:02d}")
        return sorted(out)


class SchedulerStateResponse(BaseModel):
    state: Literal["idle", "waiting", "running", "failed", "complete"] = "idle"
    message: str = ""
    current_recipe_id: str | None = None
    current_recipe_name: str | None = None
    last_run_at_iso: str | None = None
    next_wake_iso: str | None = None
