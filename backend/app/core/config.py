from functools import lru_cache
from pathlib import Path, PurePosixPath

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    log_level: str = "INFO"
    database_url: str = "postgresql+psycopg://movie:movie@localhost:5432/movie_recommender"
    tmdb_api_key: str | None = None
    openai_api_key: str | None = None
    data_dir: Path = Field(default=Path("data"))
    ml_artifacts_dir: Path = Field(default=Path("ml/artifacts"))
    tmdb_cache_ttl_seconds: int = 2_592_000
    web_session_secret: str = "change-me-before-deployment"
    web_session_days: int = 30
    web_cookie_secure: bool = False
    web_auth_required: bool = True
    web_login_attempts: int = 10
    web_login_window_seconds: int = 900
    web_profile_imports_per_hour: int = 10
    resend_api_key: str | None = None
    email_from: str | None = None
    password_reset_code_minutes: int = 15
    registration_email_verification: bool = False
    allow_sqlite_production: bool = False

    @property
    def raw_data_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def processed_data_dir(self) -> Path:
        return self.data_dir / "processed"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def production_configuration_errors(settings: Settings) -> list[str]:
    if settings.app_env.casefold() != "production":
        return []
    errors = []
    database_url = settings.database_url.strip()
    database_is_persistent = database_url.casefold().startswith(("postgresql://", "postgresql+"))
    if settings.allow_sqlite_production and database_url.casefold().startswith("sqlite"):
        sqlite_database = make_url(database_url).database
        database_is_persistent = bool(
            sqlite_database
            and sqlite_database != ":memory:"
            and (
                Path(sqlite_database).is_absolute() or PurePosixPath(sqlite_database).is_absolute()
            )
        )
    if not database_is_persistent:
        errors.append(
            "DATABASE_URL must use PostgreSQL, or an absolute persistent SQLite path with "
            "ALLOW_SQLITE_PRODUCTION=true"
        )
    if not settings.web_auth_required:
        errors.append("WEB_AUTH_REQUIRED must be true")
    if not settings.web_cookie_secure:
        errors.append("WEB_COOKIE_SECURE must be true")
    secret = settings.web_session_secret.strip()
    if len(secret) < 32 or secret in {
        "change-me-before-deployment",
        "replace-with-at-least-32-random-characters",
    }:
        errors.append("WEB_SESSION_SECRET must be a unique secret of at least 32 characters")
    if not (settings.tmdb_api_key or "").strip():
        errors.append("TMDB_API_KEY must be configured")
    if not (settings.resend_api_key or "").strip():
        errors.append("RESEND_API_KEY must be configured")
    if not (settings.email_from or "").strip():
        errors.append("EMAIL_FROM must be configured")
    if not settings.registration_email_verification:
        errors.append("REGISTRATION_EMAIL_VERIFICATION must be true")
    if not 5 <= settings.password_reset_code_minutes <= 60:
        errors.append("PASSWORD_RESET_CODE_MINUTES must be between 5 and 60")
    if not 1 <= settings.web_session_days <= 90:
        errors.append("WEB_SESSION_DAYS must be between 1 and 90")
    if (
        min(
            settings.web_login_attempts,
            settings.web_login_window_seconds,
            settings.web_profile_imports_per_hour,
        )
        < 1
    ):
        errors.append("Web rate limits must be positive")
    return errors


def validate_production_configuration(settings: Settings) -> None:
    errors = production_configuration_errors(settings)
    if errors:
        raise RuntimeError("Unsafe production configuration: " + "; ".join(errors))
