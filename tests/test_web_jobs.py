from importlib import import_module
from pathlib import Path

from app.core.config import get_settings
from app.db.base import Base
from app.db.models import WebJob
from app.main import app
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


def csrf_headers(client: TestClient) -> dict[str, str]:
    return {"X-Movie-Compass-CSRF": client.cookies["movie_compass_csrf"]}


def test_profile_import_job_is_private_persistent_and_completes(
    tmp_path: Path, monkeypatch
) -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    main_module = import_module("app.main")
    monkeypatch.setattr(main_module, "SessionLocal", session_factory)
    settings = get_settings()
    monkeypatch.setattr(settings, "web_auth_required", True)
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    main_module.profile_import_rate_limiter.clear()

    owner = TestClient(app)
    stranger = TestClient(app)
    for client, name, email in (
        (owner, "Owner", "job-owner@example.com"),
        (stranger, "Stranger", "job-stranger@example.com"),
    ):
        response = client.post(
            "/auth/register",
            json={
                "display_name": name,
                "email": email,
                "password": "a-strong-test-password",
            },
        )
        assert response.status_code == 201

    real_worker = main_module._run_import_job
    monkeypatch.setattr(main_module, "_run_import_job", lambda *_args: None)
    queued = owner.post(
        "/profiles/import/start",
        headers=csrf_headers(owner),
        data={"user": "job-profile"},
        files={"archive": ("letterboxd.zip", b"temporary archive", "application/zip")},
    )
    assert queued.status_code == 202
    job_id = queued.json()["job_id"]
    assert owner.get(f"/jobs/{job_id}").json()["status"] == "queued"
    assert stranger.get(f"/jobs/{job_id}").status_code == 404

    async def completed_import(_request, user, _archive):
        return {"user": user, "import": {"movies_staged": 1}}

    monkeypatch.setattr(main_module, "import_profile", completed_import)
    archive_path = settings.data_dir / "jobs" / f"{job_id}.zip"
    real_worker(job_id, str(archive_path), "job-profile", 1)

    completed = owner.get(f"/jobs/{job_id}").json()
    assert completed["status"] == "succeeded"
    assert completed["result"]["import"]["movies_staged"] == 1
    assert not archive_path.exists()
    with session_factory() as session:
        assert session.get(WebJob, job_id).completed_at is not None
