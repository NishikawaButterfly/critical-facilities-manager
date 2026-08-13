from __future__ import annotations

import pytest
from pydantic import ValidationError

from cfm import __version__
from cfm.config import Settings


def test_default_settings() -> None:
    settings = Settings(_env_file=None)
    assert settings.api_prefix == "/api/v1"
    assert settings.database_url == "sqlite:///./cfm.db"
    assert settings.db_auto_upgrade is False
    assert settings.docs_enabled is True
    # Asserted against the package's own version so a release bumps one
    # place; the point of the setting is that the API reports what is
    # installed, not that it reports any particular literal.
    assert settings.app_version == __version__


@pytest.mark.parametrize("prefix", ["", "/", "api", "/api/", "/api//v1"])
def test_api_prefix_rejects_invalid_values(prefix: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, api_prefix=prefix)


def test_auth_throttle_defaults() -> None:
    settings = Settings(_env_file=None)
    assert settings.auth_max_failures == 10
    assert settings.auth_failure_window_seconds == 60
    assert settings.auth_lockout_seconds == 300
    assert settings.trusted_proxy is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("auth_max_failures", -1),
        ("auth_failure_window_seconds", 0),
        ("auth_lockout_seconds", 0),
    ],
)
def test_auth_throttle_rejects_invalid_values(field: str, value: int) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})


def test_environment_prefix_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CFM_DATABASE_URL", "postgresql+psycopg://cfm:cfm@db:5432/cfm")
    monkeypatch.setenv("CFM_DOCS_ENABLED", "false")
    settings = Settings(_env_file=None)
    assert settings.database_url == "postgresql+psycopg://cfm:cfm@db:5432/cfm"
    assert settings.docs_enabled is False
