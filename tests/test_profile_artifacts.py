from __future__ import annotations

from pathlib import Path

from app.db.models import ProfileArtifact, User
from app.db.session import SessionLocal
from app.services.profile_artifacts import (
    delete_profile_artifacts,
    has_profile_artifact,
    load_profile_artifact,
    save_profile_artifact,
)
from app.services.recommendation_reports import (
    available_recommendation_scopes,
    load_recommendation_report,
)
from sqlalchemy import select


def _profile(slug: str) -> int:
    with SessionLocal() as session:
        owner = User(slug=slug, display_name=slug.title())
        session.add(owner)
        session.commit()
        session.refresh(owner)
        return owner.id


def _artifact_root(tmp_path: Path) -> Path:
    artifact = tmp_path / "movielens-32m-test"
    artifact.mkdir()
    (artifact / "manifest.json").write_text("{}", encoding="utf-8")
    return tmp_path


def test_recommendation_report_survives_without_profile_files(tmp_path: Path) -> None:
    slug = "persistent-profile"
    _profile(slug)
    payload = {
        "generated_at": "2026-09-23T00:00:00Z",
        "ranking_metrics": {"coverage": {"recommendations_returned": 1}},
        "available_candidate_years": {"minimum": 1976, "maximum": 1976},
        "recommendations": [
            {
                "title": "Through the Looking Glass",
                "year": 1976,
                "genres": ["Horror"],
            }
        ],
        "lowest_recommendations": [],
    }

    assert save_profile_artifact(slug, "recommendation", "all", payload) is True
    report = load_recommendation_report(_artifact_root(tmp_path), slug)
    scopes = available_recommendation_scopes(tmp_path, slug)

    assert report["recommendations"][0]["title"] == "Through the Looking Glass"
    assert scopes["scopes"] == [
        {
            "scope": "all",
            "generated_at": "2026-09-23T00:00:00Z",
            "available_candidate_years": {"minimum": 1976, "maximum": 1976},
            "recommendations": 1,
        }
    ]


def test_profile_artifacts_are_isolated_and_deletable() -> None:
    first_id = _profile("artifact-owner-one")
    _profile("artifact-owner-two")
    save_profile_artifact("artifact-owner-one", "review_policy", "current", {"enabled": True})
    save_profile_artifact(
        "artifact-owner-two", "review_policy", "current", {"enabled": False}
    )

    assert load_profile_artifact("artifact-owner-one", "review_policy", "current") == {
        "enabled": True
    }
    assert load_profile_artifact("artifact-owner-two", "review_policy", "current") == {
        "enabled": False
    }

    delete_profile_artifacts(first_id)

    assert has_profile_artifact("artifact-owner-one", "review_policy", "current") is False
    assert has_profile_artifact("artifact-owner-two", "review_policy", "current") is True
    with SessionLocal() as session:
        assert session.scalar(
            select(ProfileArtifact.id).where(ProfileArtifact.user_id == first_id)
        ) is None
