"""Create a consistent online backup of the production SQLite database."""

from __future__ import annotations

import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: backup-sqlite.py SOURCE_DATABASE BACKUP_DIRECTORY")

    source = Path(sys.argv[1]).resolve()
    destination_directory = Path(sys.argv[2]).resolve()
    destination_directory.mkdir(parents=True, exist_ok=True)
    destination = destination_directory / f"movie-compass-{datetime.now(UTC):%Y%m%d-%H%M%S}.sqlite3"

    with sqlite3.connect(source) as source_connection:
        with sqlite3.connect(destination) as destination_connection:
            source_connection.backup(destination_connection)

    backups = sorted(destination_directory.glob("movie-compass-*.sqlite3"), reverse=True)
    for expired in backups[14:]:
        expired.unlink()
    print(destination)


if __name__ == "__main__":
    main()
