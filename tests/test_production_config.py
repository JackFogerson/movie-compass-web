import pytest
from app.core.config import (
    Settings,
    production_configuration_errors,
    validate_production_configuration,
)


def production_settings(**overrides) -> Settings:
    values = {
        "app_env": "production",
        "database_url": "postgresql+psycopg://movie:secret@database/movie_compass",
        "tmdb_api_key": "tmdb-test-key",
        "resend_api_key": "resend-test-key",
        "email_from": "Movie Compass <noreply@example.com>",
        "web_session_secret": "a-unique-production-secret-with-32-characters",
        "web_cookie_secure": True,
        "web_auth_required": True,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_safe_production_configuration_is_accepted() -> None:
    settings = production_settings()

    assert production_configuration_errors(settings) == []
    validate_production_configuration(settings)


def test_persistent_sqlite_can_be_explicitly_enabled_for_single_vm_beta() -> None:
    settings = production_settings(
        database_url="sqlite+pysqlite:////var/lib/movie-compass/movie-compass.sqlite3",
        allow_sqlite_production=True,
    )

    assert production_configuration_errors(settings) == []


@pytest.mark.parametrize(
    "database_url",
    ["sqlite:///:memory:", "sqlite:///relative.sqlite3"],
)
def test_sqlite_production_rejects_nonpersistent_paths(database_url: str) -> None:
    settings = production_settings(
        database_url=database_url,
        allow_sqlite_production=True,
    )

    errors = production_configuration_errors(settings)
    assert any("absolute persistent SQLite" in error for error in errors)


def test_unsafe_production_configuration_reports_every_problem() -> None:
    settings = production_settings(
        database_url="sqlite:///temporary.sqlite3",
        tmdb_api_key="",
        web_session_secret="short",
        web_cookie_secure=False,
        web_auth_required=False,
        web_session_days=365,
        web_login_attempts=0,
        resend_api_key="",
        email_from="",
        password_reset_code_minutes=0,
    )

    errors = production_configuration_errors(settings)

    assert len(errors) == 10
    with pytest.raises(RuntimeError, match="Unsafe production configuration"):
        validate_production_configuration(settings)


def test_development_configuration_remains_flexible() -> None:
    settings = Settings(
        _env_file=None,
        app_env="development",
        database_url="sqlite:///local.sqlite3",
        tmdb_api_key=None,
    )

    assert production_configuration_errors(settings) == []
