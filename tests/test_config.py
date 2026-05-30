from __future__ import annotations

from paprika_mcp.config import get_settings


def test_retry_settings_can_be_configured_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("PAPRIKA_EMAIL", "user@example.test")
    monkeypatch.setenv("PAPRIKA_PASSWORD", "secret")
    monkeypatch.setenv("PAPRIKA_MAX_RETRIES", "5")
    monkeypatch.setenv("PAPRIKA_RETRY_BACKOFF_BASE", "0.5")
    monkeypatch.setenv("PAPRIKA_RETRY_BACKOFF_MAX", "5.0")
    monkeypatch.setenv("PAPRIKA_RETRY_JITTER", "0.1")

    settings = get_settings()

    assert settings.paprika_max_retries == 5
    assert settings.paprika_retry_backoff_base == 0.5
    assert settings.paprika_retry_backoff_max == 5.0
    assert settings.paprika_retry_jitter == 0.1


def test_relative_db_path_resolves_from_current_directory(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    settings = get_settings(
        {
            "email": "user@example.test",
            "password": "secret",
            "db_path": "data/paprika.sqlite",
        }
    )

    assert settings.paprika_db_path == tmp_path / "data" / "paprika.sqlite"
