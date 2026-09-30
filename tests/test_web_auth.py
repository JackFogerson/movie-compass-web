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


def test_friend_requests_can_be_accepted_and_removed(monkeypatch) -> None:
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

    alice = TestClient(app)
    bob = TestClient(app)
    for client, name, email in (
        (alice, "Alice", "alice@example.com"),
        (bob, "Bob", "bob@example.com"),
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

    requested = alice.post("/friends/request", json={"email": "bob@example.com"})
    assert requested.status_code == 201
    friendship_id = requested.json()["friendship_id"]
    assert alice.get("/friends").json()["outgoing"][0]["display_name"] == "Bob"
    assert bob.get("/friends").json()["incoming"][0]["display_name"] == "Alice"

    accepted = bob.post(f"/friends/{friendship_id}/accept")
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "accepted"
    assert alice.get("/friends").json()["friends"][0]["display_name"] == "Bob"
    assert bob.get("/friends").json()["friends"][0]["display_name"] == "Alice"

    removed = alice.delete(f"/friends/{friendship_id}")
    assert removed.status_code == 200
    assert bob.get("/friends").json() == {"friends": [], "incoming": [], "outgoing": []}
