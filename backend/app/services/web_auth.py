from __future__ import annotations

import secrets
from dataclasses import dataclass

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pwdlib import PasswordHash

COOKIE_NAME = "movie_compass_session"
CSRF_COOKIE_NAME = "movie_compass_csrf"
CSRF_HEADER_NAME = "x-movie-compass-csrf"
_PASSWORD_HASH = PasswordHash.recommended()


@dataclass(frozen=True)
class SessionIdentity:
    account_id: int
    email: str
    session_version: int


def normalize_email(value: str) -> str:
    return value.strip().casefold()


def hash_password(value: str) -> str:
    return _PASSWORD_HASH.hash(value)


def verify_password(value: str, encoded: str) -> bool:
    return _PASSWORD_HASH.verify(value, encoded)


def create_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def create_session_token(
    secret: str, account_id: int, email: str, session_version: int = 1
) -> str:
    serializer = URLSafeTimedSerializer(secret, salt="movie-compass-session")
    return serializer.dumps(
        {
            "account_id": account_id,
            "email": email,
            "session_version": session_version,
        }
    )


def read_session_token(secret: str, token: str, max_age_seconds: int) -> SessionIdentity | None:
    serializer = URLSafeTimedSerializer(secret, salt="movie-compass-session")
    try:
        payload = serializer.loads(token, max_age=max_age_seconds)
        return SessionIdentity(
            int(payload["account_id"]),
            str(payload["email"]),
            int(payload.get("session_version", 1)),
        )
    except (BadSignature, SignatureExpired, KeyError, TypeError, ValueError):
        return None
