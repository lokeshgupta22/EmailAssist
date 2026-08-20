"""Settings must be overridable per environment and safe by default."""

import pytest

from app.config import Settings


def test_defaults_are_local_only():
    settings = Settings()

    assert settings.ollama_host.startswith(
        "http://127.0.0.1"
    ), "the model endpoint must default to loopback so no data can leave the machine"
    assert settings.model_name
    assert settings.request_timeout_seconds > 0


def test_attachment_limits_have_safe_defaults():
    settings = Settings()

    assert 0 < settings.max_attachment_bytes <= 25 * 1024 * 1024
    assert settings.max_total_attachment_bytes >= settings.max_attachment_bytes
    assert settings.extraction_timeout_seconds > 0


def test_allowed_mime_types_are_an_allowlist():
    settings = Settings()

    assert settings.allowed_mime_types == frozenset(
        {
            "application/pdf",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }
    )


def test_env_variables_override_defaults(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("EMAIL_AGENT_MODEL_NAME", "llama3.2:3b")
    monkeypatch.setenv("EMAIL_AGENT_MAX_ATTACHMENT_BYTES", "1024")

    settings = Settings()

    assert settings.model_name == "llama3.2:3b"
    assert settings.max_attachment_bytes == 1024


def test_total_budget_cannot_be_smaller_than_single_file_budget():
    with pytest.raises(ValueError, match="max_total_attachment_bytes"):
        Settings(max_attachment_bytes=1000, max_total_attachment_bytes=500)


def test_get_settings_is_cached():
    from app.config import get_settings

    assert get_settings() is get_settings()
