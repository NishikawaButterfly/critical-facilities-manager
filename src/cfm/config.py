"""Runtime configuration loaded from environment variables."""

from __future__ import annotations

from functools import lru_cache

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
    app_version: str = "0.1.0"
    api_prefix: str = "/api/v1"
    database_url: str = "sqlite:///./cfm.db"
    docs_enabled: bool = True
    cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:3000",
            "http://localhost:5173",
        ]
    )

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
