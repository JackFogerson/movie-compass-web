#!/usr/bin/env sh
set -eu

DEPLOY_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
BACKUP_DIR="$DEPLOY_DIR/backups"
mkdir -p "$BACKUP_DIR"

STAMP=$(date -u +%Y%m%dT%H%M%SZ)
docker compose --project-directory "$DEPLOY_DIR" -f "$DEPLOY_DIR/compose.yaml" \
  exec -T db pg_dump -U movie -d movie_recommender -Fc \
  > "$BACKUP_DIR/movie-compass-$STAMP.dump"

find "$BACKUP_DIR" -type f -name 'movie-compass-*.dump' -mtime +14 -delete
echo "Backup saved: $BACKUP_DIR/movie-compass-$STAMP.dump"
