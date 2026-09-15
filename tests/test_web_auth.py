from importlib import import_module

from app.core.config import get_settings
from app.db.base import Base
from app.main import app
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


def test_account_registration_login_and_profile_isolation(monkeypatch) -> None:
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
    monkeypatch.setattr(
        settings,
        "web_session_secret",
        "test-secret-that-is-long-and-random-enough",
    )

    anonymous = TestClient(app)
    assert anonymous.get("/profiles").status_code == 401

    client = TestClient(app)
    registered = client.post(
        "/auth/register",
        json={
            "display_name": "Movie Fan",
            "email": "Fan@Example.com",
            "password": "a-strong-test-password",
        },
    )
    assert registered.status_code == 201
    assert registered.json()["email"] == "fan@example.com"
    assert client.get("/auth/me").json()["display_name"] == "Movie Fan"
    assert client.get("/profiles").json() == {"profiles": []}

    assert client.post("/auth/logout").status_code == 200
    assert client.get("/profiles").status_code == 401

    logged_in = client.post(
        "/auth/login",
        json={"email": "fan@example.com", "password": "a-strong-test-password"},
    )
    assert logged_in.status_code == 200
    assert client.get("/profiles").status_code == 200
