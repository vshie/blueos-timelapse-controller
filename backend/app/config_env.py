"""Runtime configuration from environment."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TIMELAPSE_", env_file=".env", extra="ignore")

    data_dir: str = "/data"
    static_dir: str | None = None  # if None, resolved relative to package
    host: str = "0.0.0.0"
    port: int = 9876


@lru_cache
def get_settings() -> Settings:
    return Settings()
