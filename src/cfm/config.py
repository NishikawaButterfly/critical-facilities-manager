"""Runtime configuration loaded from environment variables."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="CFM_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Critical Facilities Manager API"
    app_version: str = "0.2.0"
    api_prefix: str = "/api/v1"
    database_url: str = "sqlite:///./cfm.db"
    db_auto_upgrade: bool = False
    docs_enabled: bool = True
    # Serve the bundled web frontend at /ui (and redirect / to it). Turn it
    # off for deployments that want the API surface and nothing else.
    web_enabled: bool = True
    cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:3000",
            "http://localhost:5173",
        ]
    )
    # Failed token authentications per source before the source is locked
    # out; zero disables the limiter entirely.
    auth_max_failures: int = Field(default=10, ge=0)
    # Sliding window, in seconds, within which failures accumulate.
    auth_failure_window_seconds: int = Field(default=60, ge=1)
    # How long, in seconds, a locked source stays rejected with 429.
    auth_lockout_seconds: int = Field(default=300, ge=1)
    # Trust exactly one operator-controlled reverse proxy in front of the
    # app: attribute authentication failures to the rightmost entry of
    # X-Forwarded-For (the address the proxy appended) instead of the
    # socket peer. Off by default because the header is client-writable.
    trusted_proxy: bool = False
    # Root directory of the content-addressed evidence store, created on
    # demand. Local filesystem only for now; an S3-compatible remote
    # backend is future work behind the same store interface.
    evidence_dir: Path = Path("./evidence")
    # Upper bound, in mebibytes, on one uploaded evidence file. The default
    # comfortably covers site photos, scanned certificates, and PDF test
    # reports while keeping the memory that download verification buffers
    # bounded; deployments that need large videos should raise it (and its
    # costs) deliberately.
    evidence_max_mb: int = Field(default=25, ge=1)

    @field_validator("api_prefix")
    @classmethod
    def validate_api_prefix(cls, value: str) -> str:
        if (
            not value
            or value == "/"
            or not value.startswith("/")
            or value.endswith("/")
            or "//" in value
        ):
            raise ValueError(
                "api_prefix must be a non-root path such as '/api/v1' without a trailing slash"
            )
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
