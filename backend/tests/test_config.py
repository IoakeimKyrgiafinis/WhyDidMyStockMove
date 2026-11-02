from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from whymoved.config import JobSettings, Settings
from whymoved.main import create_app

REQUIRED = ("WHYMOVED_ALPACA_KEY_ID", "WHYMOVED_ALPACA_SECRET_KEY", "WHYMOVED_SEC_USER_AGENT")


def test_cors_origin_list_splits_and_strips() -> None:
    settings = Settings(cors_origins="https://a.example, https://b.example ,")

    assert settings.cors_origin_list == ["https://a.example", "https://b.example"]


def test_cors_header_for_default_origin() -> None:
    client = TestClient(create_app())

    response = client.get("/health", headers={"Origin": "http://localhost:5173"})

    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_job_settings_fail_fast_without_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in REQUIRED:
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ValidationError):
        JobSettings(_env_file=None)


def test_job_settings_accept_valid_values() -> None:
    settings = JobSettings(
        alpaca_key_id="key-123",
        alpaca_secret_key="secret-456",
        sec_user_agent=" Jane Doe jane@example.com ",
        _env_file=None,
    )

    assert settings.alpaca_key_id.get_secret_value() == "key-123"
    assert settings.sec_user_agent == "Jane Doe jane@example.com"
    assert settings.alpaca_feed == "iex"


def test_job_settings_do_not_leak_secrets_in_logs() -> None:
    settings = JobSettings(
        alpaca_key_id="key-123",
        alpaca_secret_key="secret-456",
        sec_user_agent="Jane Doe jane@example.com",
        _env_file=None,
    )

    assert "key-123" not in repr(settings)
    assert "secret-456" not in str(settings)


def test_job_settings_reject_a_user_agent_without_contact_details() -> None:
    with pytest.raises(ValidationError, match="contact email"):
        JobSettings(
            alpaca_key_id="key-123",
            alpaca_secret_key="secret-456",
            sec_user_agent="my-app",
            _env_file=None,
        )


def test_job_settings_reject_an_unknown_feed() -> None:
    with pytest.raises(ValidationError):
        JobSettings(
            alpaca_key_id="key-123",
            alpaca_secret_key="secret-456",
            sec_user_agent="Jane Doe jane@example.com",
            alpaca_feed="nasdaq",
            _env_file=None,
        )
