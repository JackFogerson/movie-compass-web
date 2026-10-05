from importlib import import_module

from app.core.config import get_settings
from app.db.base import Base
from app.db.models import Account, User
from app.main import app
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


def _csrf_headers(client: TestClient) -> dict[str, str]:
    return {"X-Movie-Compass-CSRF": client.cookies["movie_compass_csrf"]}


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
    assert client.get("/profiles").json() == {"profiles": [], "shared_profiles": []}

    assert client.post("/auth/logout").status_code == 403
    assert client.post("/auth/logout", headers=_csrf_headers(client)).status_code == 200
    assert client.get("/profiles").status_code == 401

    logged_in = client.post(
        "/auth/login",
        json={"email": "fan@example.com", "password": "a-strong-test-password"},
    )
    assert logged_in.status_code == 200
    assert client.get("/profiles").status_code == 200


def test_production_registration_requires_emailed_code(monkeypatch) -> None:
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
    monkeypatch.setattr(settings, "registration_email_verification", True)
    monkeypatch.setattr(settings, "resend_api_key", "resend-test-key")
    monkeypatch.setattr(settings, "email_from", "Movie Compass <noreply@example.com>")
    main_module.login_rate_limiter.clear()
    delivered = {}

    def capture_code(**message) -> None:
        delivered.update(message)

    monkeypatch.setattr(main_module, "send_email_verification_code", capture_code)
    credentials = {
        "email": "verify-me@example.com",
        "password": "a-strong-test-password",
    }
    client = TestClient(app)
    registered = client.post(
        "/auth/register",
        json={"display_name": "Verify Me", **credentials},
    )

    assert registered.status_code == 202
    assert registered.json() == {
        "email": credentials["email"],
        "verification_required": True,
    }
    assert client.get("/profiles").status_code == 401
    assert client.post("/auth/login", json=credentials).status_code == 403

    verified = client.post(
        "/auth/verify-email",
        json={
            "email": credentials["email"],
            "verification_code": delivered["code"],
        },
    )
    assert verified.status_code == 200
    assert verified.json()["email_verified"] is True
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
    account_ids = {}
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
        account_ids[name] = response.json()["id"]

    requested = alice.post(
        "/friends/request",
        json={"email": "bob@example.com"},
        headers=_csrf_headers(alice),
    )
    assert requested.status_code == 201
    friendship_id = requested.json()["friendship_id"]
    assert alice.get("/friends").json()["outgoing"][0]["display_name"] == "Bob"
    assert bob.get("/friends").json()["incoming"][0]["display_name"] == "Alice"

    accepted = bob.post(f"/friends/{friendship_id}/accept", headers=_csrf_headers(bob))
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "accepted"
    assert alice.get("/friends").json()["friends"][0]["display_name"] == "Bob"
    assert bob.get("/friends").json()["friends"][0]["display_name"] == "Alice"

    with session_factory() as session:
        session.add(
            User(
                slug="alice-profile",
                display_name="Alice's Movies",
                owner_account_id=account_ids["Alice"],
            )
        )
        session.commit()
    shared = alice.post(
        f"/friends/{friendship_id}/shares",
        json={"profile_slug": "alice-profile"},
        headers=_csrf_headers(alice),
    )
    assert shared.status_code == 201
    profile_id = shared.json()["profile_id"]
    assert (
        alice.get("/friends").json()["friends"][0]["shared_profiles"][0]["slug"] == "alice-profile"
    )
    assert bob.get("/profiles").json()["shared_profiles"] == [
        {
            "slug": "alice-profile",
            "display_name": "Alice's Movies",
            "permission": "movie_night",
            "shared_by": "Alice",
        }
    ]
    main_module._require_movie_night_profiles(account_ids["Bob"], ["alice-profile"])

    unshared = alice.delete(
        f"/friends/{friendship_id}/shares/{profile_id}",
        headers=_csrf_headers(alice),
    )
    assert unshared.status_code == 200
    assert bob.get("/profiles").json()["shared_profiles"] == []

    removed = alice.delete(f"/friends/{friendship_id}", headers=_csrf_headers(alice))
    assert removed.status_code == 200
    assert bob.get("/friends").json() == {"friends": [], "incoming": [], "outgoing": []}


def test_repeated_login_attempts_are_rate_limited(monkeypatch) -> None:
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
    monkeypatch.setattr(settings, "web_login_attempts", 2)
    monkeypatch.setattr(settings, "web_login_window_seconds", 60)
    main_module.login_rate_limiter.clear()

    client = TestClient(app)
    credentials = {"email": "limited@example.com", "password": "a-strong-test-password"}
    assert (
        client.post("/auth/register", json={"display_name": "Limited", **credentials}).status_code
        == 201
    )
    assert client.post("/auth/logout", headers=_csrf_headers(client)).status_code == 200

    for _ in range(2):
        response = client.post(
            "/auth/login",
            json={"email": credentials["email"], "password": "incorrect-password"},
        )
        assert response.status_code == 401

    blocked = client.post("/auth/login", json=credentials)
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) > 0


def test_account_deletion_requires_password_and_removes_personal_data(monkeypatch) -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    main_module = import_module("app.main")
    monkeypatch.setattr(main_module, "SessionLocal", session_factory)
    monkeypatch.setattr(main_module, "_delete_profile_files", lambda _slug: [])
    settings = get_settings()
    monkeypatch.setattr(settings, "web_auth_required", True)

    client = TestClient(app)
    registered = client.post(
        "/auth/register",
        json={
            "display_name": "Delete Me",
            "email": "delete@example.com",
            "password": "a-strong-test-password",
        },
    )
    assert registered.status_code == 201
    account_id = registered.json()["id"]
    with session_factory() as session:
        session.add(
            User(
                slug="delete-profile",
                display_name="Delete Profile",
                owner_account_id=account_id,
            )
        )
        session.commit()

    rejected = client.request(
        "DELETE",
        "/auth/account",
        headers=_csrf_headers(client),
        json={"password": "incorrect-password", "confirmation": "DELETE"},
    )
    assert rejected.status_code == 401

    deleted = client.request(
        "DELETE",
        "/auth/account",
        headers=_csrf_headers(client),
        json={"password": "a-strong-test-password", "confirmation": "DELETE"},
    )
    assert deleted.status_code == 200
    assert deleted.json()["profiles_deleted"] == 1
    assert client.get("/auth/me").status_code == 401
    with session_factory() as session:
        assert session.query(Account).count() == 0
        assert session.query(User).count() == 0


def test_signed_in_account_can_change_password(monkeypatch) -> None:
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

    client = TestClient(app)
    credentials = {
        "email": "change-password@example.com",
        "password": "original-test-password",
    }
    assert (
        client.post(
            "/auth/register", json={"display_name": "Password Changer", **credentials}
        ).status_code
        == 201
    )

    rejected = client.put(
        "/auth/password",
        headers=_csrf_headers(client),
        json={
            "current_password": "incorrect-password",
            "new_password": "replacement-test-password",
        },
    )
    assert rejected.status_code == 401

    changed = client.put(
        "/auth/password",
        headers=_csrf_headers(client),
        json={
            "current_password": credentials["password"],
            "new_password": "replacement-test-password",
        },
    )
    assert changed.status_code == 200
    assert changed.json() == {"changed": True}

    old_login = TestClient(app).post("/auth/login", json=credentials)
    assert old_login.status_code == 401
    new_login = TestClient(app).post(
        "/auth/login",
        json={
            "email": credentials["email"],
            "password": "replacement-test-password",
        },
    )
    assert new_login.status_code == 200


def test_recovery_code_resets_password_once_and_revokes_old_sessions(monkeypatch) -> None:
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
    monkeypatch.setattr(settings, "resend_api_key", "resend-test-key")
    monkeypatch.setattr(settings, "email_from", "Movie Compass <noreply@example.com>")
    main_module.login_rate_limiter.clear()
    delivered = {}

    def capture_code(**message) -> None:
        delivered.update(message)

    monkeypatch.setattr(main_module, "send_password_reset_code", capture_code)

    original = {
        "email": "recover-me@example.com",
        "password": "original-test-password",
    }
    owner = TestClient(app)
    assert (
        owner.post("/auth/register", json={"display_name": "Recover Me", **original}).status_code
        == 201
    )
    older_session = TestClient(app)
    assert older_session.post("/auth/login", json=original).status_code == 200

    requested = TestClient(app).post(
        "/auth/recover/request",
        json={"email": original["email"]},
    )
    assert requested.status_code == 202
    assert requested.json() == {"accepted": True}
    code = delivered["code"]
    assert len(code) == 6 and code.isdigit()

    recovered = TestClient(app)
    reset = recovered.post(
        "/auth/recover",
        json={
            "email": original["email"],
            "recovery_code": code,
            "new_password": "recovered-test-password",
        },
    )
    assert reset.status_code == 200
    assert recovered.get("/profiles").status_code == 200
    assert older_session.get("/profiles").status_code == 401

    reused = TestClient(app).post(
        "/auth/recover",
        json={
            "email": original["email"],
            "recovery_code": code,
            "new_password": "another-test-password",
        },
    )
    assert reused.status_code == 401
    assert TestClient(app).post("/auth/login", json=original).status_code == 401
    assert (
        TestClient(app)
        .post(
            "/auth/login",
            json={
                "email": original["email"],
                "password": "recovered-test-password",
            },
        )
        .status_code
        == 200
    )
