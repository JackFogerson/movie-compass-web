FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=10000

WORKDIR /app
COPY pyproject.toml alembic.ini ./
COPY backend ./backend
COPY ingestion ./ingestion
COPY recommendation ./recommendation
COPY ml ./ml
COPY data/bootstrap ./data/bootstrap
RUN python -m pip install --no-cache-dir .

CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port ${PORT}"]
