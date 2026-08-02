from __future__ import annotations

import pytest
from pydantic import ValidationError

from cfm.config import Settings


def test_default_settings() -> None:
    settings = Settings(_env_file=None)
    assert settings.api_prefix == "/api/v1"
    assert settings.database_url == "sqlite:///./cfm.db"
    assert settings.docs_enabled is True
    assert settings.app_version == "0.1.0"


@pytest.mark.parametrize("prefix", ["", "/", "api", "/api/", "/api//v1"])
def test_api_prefix_rejects_invalid_values(prefix: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, api_prefix=prefix)


def test_environment_prefix_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CFM_DATABASE_URL", "postgresql+psycopg://cfm:cfm@db:5432/cfm")
    monkeypatch.setenv("CFM_DOCS_ENABLED", "false")
    settings = Settings(_env_file=None)
    assert settings.database_url == "postgresql+psycopg://cfm:cfm@db:5432/cfm"
    assert settings.docs_enabled is False
