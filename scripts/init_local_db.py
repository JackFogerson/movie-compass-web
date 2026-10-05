import sys
from pathlib import Path
from shutil import copyfile

# Always initialize this checkout's schema, even when the local and website
# editions share a virtual environment containing another editable app package.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))
sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import get_settings  # noqa: E402
from app.db import models  # noqa: E402,F401
from app.db.base import Base  # noqa: E402
from sqlalchemy import create_engine, inspect, text  # noqa: E402


def main() -> None:
    settings = get_settings()
    if not settings.database_url.startswith("sqlite"):
        raise RuntimeError("Local schema initialization is only for SQLite; use Alembic otherwise")
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.processed_data_dir.mkdir(parents=True, exist_ok=True)
    for name in (
        "tmdb-rich-details.json",
        "display-metadata.json",
        "tmdb-catalog.sqlite3",
        "tmdb-catalog-manifest.json",
    ):
        cache = settings.processed_data_dir / name
        if not cache.exists():
            bootstrap = settings.data_dir / "bootstrap" / name
            if bootstrap.is_file():
                copyfile(bootstrap, cache)
            elif cache.suffix == ".json":
                cache.write_text("{}\n", encoding="utf-8")
    engine = create_engine(settings.database_url)
    Base.metadata.create_all(engine)
    account_columns = {column["name"] for column in inspect(engine).get_columns("accounts")}
    with engine.begin() as connection:
        if "recovery_code_hash" not in account_columns:
            connection.execute(
                text("ALTER TABLE accounts ADD COLUMN recovery_code_hash VARCHAR(500)")
            )
        if "session_version" not in account_columns:
            connection.execute(
                text(
                    "ALTER TABLE accounts ADD COLUMN session_version INTEGER "
                    "NOT NULL DEFAULT 1"
                )
            )
        if "recovery_code_expires_at" not in account_columns:
            connection.execute(
                text("ALTER TABLE accounts ADD COLUMN recovery_code_expires_at DATETIME")
            )
    print(f"Initialized local database: {settings.database_url}")


if __name__ == "__main__":
    main()
