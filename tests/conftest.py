import atexit
import os
import tempfile
from pathlib import Path

os.environ["WEB_AUTH_REQUIRED"] = "false"
os.environ.setdefault("TMDB_API_KEY", "test-key")

# A clean website checkout intentionally contains no local database.  Keep the
# test suite self-contained instead of silently trying the production Postgres
# default when a developer has not created an .env file yet.
test_database = Path(tempfile.gettempdir()) / f"movie-compass-tests-{os.getpid()}.db"
os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{test_database.as_posix()}"

from app.db.models import Base  # noqa: E402
from app.db.session import engine  # noqa: E402

Base.metadata.create_all(bind=engine)


@atexit.register
def _remove_test_database() -> None:
    engine.dispose()
    test_database.unlink(missing_ok=True)
