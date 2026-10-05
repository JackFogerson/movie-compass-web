from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import delete, select

from app.db.models import ProfileArtifact, User
from app.db.session import SessionLocal


def save_profile_artifact(user: str, artifact_type: str, artifact_key: str, payload: dict) -> bool:
    """Upsert one generated profile document; return false for an unknown profile."""
    with SessionLocal() as session:
        owner = session.scalar(select(User).where(User.slug == user))
        if owner is None:
            return False
        artifact = session.scalar(
            select(ProfileArtifact).where(
                ProfileArtifact.user_id == owner.id,
                ProfileArtifact.artifact_type == artifact_type,
                ProfileArtifact.artifact_key == artifact_key,
            )
        )
        if artifact is None:
            artifact = ProfileArtifact(
                user_id=owner.id,
                artifact_type=artifact_type,
                artifact_key=artifact_key,
                payload_json="{}",
            )
            session.add(artifact)
        artifact.payload_json = json.dumps(payload)
        artifact.updated_at = datetime.now(UTC)
        session.commit()
    return True


def load_profile_artifact(user: str, artifact_type: str, artifact_key: str) -> dict | None:
    with SessionLocal() as session:
        payload = session.scalar(
            select(ProfileArtifact.payload_json)
            .join(User, User.id == ProfileArtifact.user_id)
            .where(
                User.slug == user,
                ProfileArtifact.artifact_type == artifact_type,
                ProfileArtifact.artifact_key == artifact_key,
            )
        )
    if payload is None:
        return None
    try:
        value = json.loads(payload)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def list_profile_artifacts(user: str, artifact_type: str) -> dict[str, dict]:
    with SessionLocal() as session:
        rows = session.execute(
            select(ProfileArtifact.artifact_key, ProfileArtifact.payload_json)
            .join(User, User.id == ProfileArtifact.user_id)
            .where(User.slug == user, ProfileArtifact.artifact_type == artifact_type)
        ).all()
    values = {}
    for key, payload in rows:
        try:
            value = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            values[str(key)] = value
    return values


def has_profile_artifact(user: str, artifact_type: str, artifact_key: str) -> bool:
    with SessionLocal() as session:
        return (
            session.scalar(
                select(ProfileArtifact.id)
                .join(User, User.id == ProfileArtifact.user_id)
                .where(
                    User.slug == user,
                    ProfileArtifact.artifact_type == artifact_type,
                    ProfileArtifact.artifact_key == artifact_key,
                )
            )
            is not None
        )


def delete_profile_artifacts(user_id: int) -> None:
    with SessionLocal() as session:
        session.execute(delete(ProfileArtifact).where(ProfileArtifact.user_id == user_id))
        session.commit()
