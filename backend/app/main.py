import asyncio
import csv
import gzip
import io
import json
import logging
import re
import secrets
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from shutil import rmtree
from statistics import median
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Annotated
from uuid import uuid4

import typer
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from tenacity import RetryError

from app.cli.recommend import main as generate_recommendations
from app.core.config import get_settings, validate_production_configuration
from app.core.logging import configure_logging
from app.db.models import (
    Account,
    Friendship,
    ImportMapping,
    ImportRun,
    Movie,
    ProfileShare,
    User,
    UserMovieInteraction,
    WebJob,
)
from app.db.session import SessionLocal
from app.services.display_metadata import enrich_display_metadata
from app.services.email_delivery import (
    EmailDeliveryError,
    send_email_verification_code,
    send_password_reset_code,
)
from app.services.group_recommendations import (
    clear_group_recommendation_cache,
    generate_group_recommendations,
)
from app.services.letterboxd_import import import_letterboxd_archive
from app.services.local_catalog_mapping import map_pending_from_artifact
from app.services.profile_accuracy import profile_accuracy as evaluate_profile_accuracy
from app.services.profile_artifacts import delete_profile_artifacts, has_profile_artifact
from app.services.profile_export import build_profile_archive, restore_profile_archive
from app.services.profile_stats import build_taste_breakdown, movie_category_labels
from app.services.rate_limit import SlidingWindowRateLimiter
from app.services.recommendation_reports import (
    RecommendationReportNotFound,
    _latest_artifact,
    available_recommendation_scopes,
    load_recommendation_report,
)
from app.services.review_policy import load_review_policy, refresh_review_policy
from app.services.tmdb_mapping import map_pending_letterboxd, resolve_letterboxd_links
from app.services.web_auth import (
    COOKIE_NAME,
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    create_csrf_token,
    create_session_token,
    hash_password,
    normalize_email,
    read_session_token,
    verify_password,
)
from ingestion.letterboxd.parser import normalize_title
from ingestion.tmdb.client import (
    TmdbClient,
    TmdbError,
    is_tv_catalog_id,
    normalize_tv_details,
    normalize_tv_search_result,
    rank_title_search_results,
)
from ingestion.tmdb.daily_export import load_catalog_summary
from ingestion.tmdb.details_cache import load_or_fetch_details

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    validate_production_configuration(settings)
    yield


app = FastAPI(title="Personal Movie Recommender", version="0.1.0", lifespan=lifespan)
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")
login_rate_limiter = SlidingWindowRateLimiter()
profile_import_rate_limiter = SlidingWindowRateLimiter()


class GroupRecommendationRequest(BaseModel):
    users: list[str] = Field(min_length=2, max_length=4)
    year_min: int | None = Field(default=None, ge=1870, le=2200)
    year_max: int | None = Field(default=None, ge=1870, le=2200)
    runtime_min: int | None = Field(default=None, ge=1, le=600)
    runtime_max: int | None = Field(default=None, ge=1, le=600)
    popularity: str = "all"
    genre: str | None = Field(default=None, max_length=60)
    media_type: str = Field(default="all", pattern=r"^(all|movie|tv)$")
    country: str = Field(default="US", pattern=r"^[A-Z]{2}$")
    include_watched: bool = False
    limit: int = Field(default=20, ge=1, le=30)


class GroupMovieSearchRequest(GroupRecommendationRequest):
    query: str = Field(min_length=2, max_length=120)
    year: int | None = Field(default=None, ge=1870, le=2200)


class ProfileUpdateRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=100)


class ProfileDeleteRequest(BaseModel):
    confirmation: str = Field(min_length=1, max_length=100)


class AccountCredentials(BaseModel):
    email: str = Field(min_length=5, max_length=320)
    password: str = Field(min_length=10, max_length=200)


class AccountRegistration(AccountCredentials):
    display_name: str = Field(min_length=1, max_length=100)


class EmailVerificationRequest(BaseModel):
    email: str = Field(min_length=5, max_length=320)
    verification_code: str = Field(pattern=r"^\d{6}$")


class EmailVerificationCodeRequest(BaseModel):
    email: str = Field(min_length=5, max_length=320)


class AccountDeleteRequest(BaseModel):
    password: str = Field(min_length=10, max_length=200)
    confirmation: str = Field(min_length=1, max_length=20)


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=10, max_length=200)
    new_password: str = Field(min_length=10, max_length=200)


class PasswordResetCodeRequest(BaseModel):
    email: str = Field(min_length=5, max_length=320)


class PasswordRecoveryRequest(BaseModel):
    email: str = Field(min_length=5, max_length=320)
    recovery_code: str = Field(pattern=r"^\d{6}$")
    new_password: str = Field(min_length=10, max_length=200)


class FriendRequest(BaseModel):
    email: str = Field(min_length=5, max_length=320)


class ProfileShareRequest(BaseModel):
    profile_slug: str = Field(min_length=1, max_length=100)


class ManualRatingRequest(BaseModel):
    tmdb_id: int
    title: str = Field(min_length=1, max_length=500)
    year: int | None = Field(default=None, ge=1870, le=2200)
    rating: float = Field(ge=0.5, le=5.0, multiple_of=0.5)
    review_text: str | None = Field(default=None, max_length=20_000)


def _with_display_metadata(report: dict, country: str) -> dict:
    return enrich_display_metadata(
        report,
        api_key=settings.tmdb_api_key,
        cache_path=settings.processed_data_dir / "display-metadata.json",
        country=country,
    )


def _load_profile_detail_cache() -> dict[str, dict]:
    """Merge model metadata with display-only fields such as US certification."""
    merged: dict[str, dict] = {}
    for cache_name in ("tmdb-rich-details.json", "display-metadata.json"):
        cache_path = settings.processed_data_dir / cache_name
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for raw_tmdb_id, details in cached.items():
            if not str(raw_tmdb_id).lstrip("-").isdigit() or not isinstance(details, dict):
                continue
            merged.setdefault(str(raw_tmdb_id), {}).update(
                {key: value for key, value in details.items() if value is not None}
            )
    return merged


def _local_movie_search_ids(query: str, year: int | None, limit: int) -> list[int]:
    """Search the bundled linked catalog when the live TMDB search is unavailable."""
    try:
        artifact = _latest_artifact(settings.ml_artifacts_dir)
        manifest = json.loads((artifact / "manifest.json").read_text(encoding="utf-8"))
        catalog_path = artifact / manifest["files"]["catalog"]
    except (RecommendationReportNotFound, FileNotFoundError, KeyError, json.JSONDecodeError):
        return []

    wanted = normalize_title(query)
    matches: list[tuple[int, int, int]] = []
    opener = gzip.open if catalog_path.suffix == ".gz" else open
    with opener(catalog_path, "rt", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            raw_tmdb_id = row.get("tmdb_id")
            if not raw_tmdb_id or not raw_tmdb_id.replace(".0", "", 1).isdigit():
                continue
            title = str(row.get("clean_title") or row.get("title") or "")
            normalized_title = normalize_title(title)
            if not wanted or wanted not in normalized_title:
                continue
            raw_year = str(row.get("year") or "")
            movie_year = int(float(raw_year)) if raw_year else None
            if year is not None and movie_year != year:
                continue
            if normalized_title == wanted:
                match_quality = 0
            elif normalized_title.startswith(wanted):
                match_quality = 1
            else:
                match_quality = 2
            matches.append((match_quality, -(movie_year or 0), int(float(raw_tmdb_id))))
    for cache_name in ("tmdb-rich-details.json", "display-metadata.json"):
        cache_path = settings.processed_data_dir / cache_name
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for raw_tmdb_id, details in cached.items():
            if details.get("missing") is True or not str(raw_tmdb_id).lstrip("-").isdigit():
                continue
            release = str(details.get("release_date") or "")
            movie_year = int(release[:4]) if release[:4].isdigit() else None
            if year is not None and movie_year != year:
                continue
            titles = {details.get("title"), details.get("original_title")}
            normalized_titles = {
                normalize_title(str(title)) for title in titles if str(title or "").strip()
            }
            matching_titles = [title for title in normalized_titles if wanted in title]
            if not matching_titles:
                continue
            best_quality = min(
                0 if title == wanted else 1 if title.startswith(wanted) else 2
                for title in matching_titles
            )
            matches.append((best_quality, -(movie_year or 0), int(raw_tmdb_id)))
    matches.sort()
    ordered = []
    for _, _, tmdb_id in matches:
        if tmdb_id not in ordered:
            ordered.append(tmdb_id)
        if len(ordered) >= limit:
            break
    return ordered


def _local_movie_search_results(query: str, year: int | None, limit: int) -> list[dict]:
    """Return display-ready matches from bundled catalog and metadata caches."""
    ordered_ids = _local_movie_search_ids(query, year, limit)
    if not ordered_ids:
        return []
    wanted_ids = set(ordered_ids)
    by_id: dict[int, dict] = {}
    for cache_name in ("tmdb-rich-details.json", "display-metadata.json"):
        cache_path = settings.processed_data_dir / cache_name
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for raw_tmdb_id, details in cached.items():
            if not str(raw_tmdb_id).lstrip("-").isdigit() or int(raw_tmdb_id) not in wanted_ids:
                continue
            tmdb_id = int(raw_tmdb_id)
            current = by_id.setdefault(tmdb_id, {})
            current.update({key: value for key, value in details.items() if value is not None})

    missing_ids = wanted_ids - set(by_id)
    if missing_ids:
        try:
            artifact = _latest_artifact(settings.ml_artifacts_dir)
            manifest = json.loads((artifact / "manifest.json").read_text(encoding="utf-8"))
            catalog_path = artifact / manifest["files"]["catalog"]
            opener = gzip.open if catalog_path.suffix == ".gz" else open
            with opener(catalog_path, "rt", encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle):
                    raw_tmdb_id = str(row.get("tmdb_id") or "")
                    if not raw_tmdb_id.replace(".0", "", 1).isdigit():
                        continue
                    tmdb_id = int(float(raw_tmdb_id))
                    if tmdb_id not in missing_ids:
                        continue
                    raw_year = str(row.get("year") or "")
                    movie_year = int(float(raw_year)) if raw_year else None
                    by_id[tmdb_id] = {
                        "id": tmdb_id,
                        "title": row.get("clean_title") or row.get("title") or "Untitled",
                        "release_date": f"{movie_year}-01-01" if movie_year else None,
                    }
        except (RecommendationReportNotFound, FileNotFoundError, KeyError, json.JSONDecodeError):
            pass
    return [dict(by_id[tmdb_id], id=tmdb_id) for tmdb_id in ordered_ids if tmdb_id in by_id]


def _tmdb_search_ids(query: str, year: int | None, limit: int) -> list[int]:
    """Search TMDB, falling back to the bundled catalog after network retry failures."""
    local_ids = _local_movie_search_ids(query, year, limit)
    if not settings.tmdb_api_key:
        return local_ids
    client = TmdbClient(settings.tmdb_api_key)
    try:
        movie_results = client.search_movie(query, year, include_adult=True)
        tv_results = [
            normalize_tv_search_result(item)
            for item in client.search_tv(query, year, include_adult=True)
            if item.get("id") is not None
        ]
        results = rank_title_search_results(query, [*movie_results, *tv_results], limit)
        ordered_ids = [int(item["id"]) for item in results if item.get("id") is not None]
        available, _ = load_or_fetch_details(
            client,
            set(ordered_ids),
            settings.processed_data_dir / "tmdb-rich-details.json",
        )
        live_ids = [tmdb_id for tmdb_id in ordered_ids if tmdb_id in available]
        return list(dict.fromkeys([*live_ids, *local_ids]))[:limit]
    except RetryError as error:
        if local_ids:
            return local_ids
        raise HTTPException(
            status_code=503,
            detail=(
                "TMDB is temporarily unreachable, and this title is not in the bundled "
                "offline catalog. Please retry the lookup when the connection is available."
            ),
        ) from error
    finally:
        client.close()


def _set_session_cookie(response: JSONResponse, account: Account) -> None:
    token = create_session_token(
        settings.web_session_secret,
        account.id,
        account.email,
        int(account.session_version or 1),
    )
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=settings.web_session_days * 86_400,
        httponly=True,
        secure=settings.web_cookie_secure,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        CSRF_COOKIE_NAME,
        create_csrf_token(),
        max_age=settings.web_session_days * 86_400,
        httponly=False,
        secure=settings.web_cookie_secure,
        samesite="lax",
        path="/",
    )


def _require_movie_night_profiles(account_id: int | None, profile_slugs: list[str]) -> None:
    if account_id is None:
        return
    unique_slugs = set(profile_slugs)
    with SessionLocal() as session:
        accessible = set(
            session.scalars(
                select(User.slug).where(
                    User.owner_account_id == account_id,
                    User.slug.in_(unique_slugs),
                )
            )
        )
        accessible.update(
            session.scalars(
                select(User.slug)
                .join(ProfileShare, ProfileShare.profile_id == User.id)
                .where(
                    ProfileShare.account_id == account_id,
                    ProfileShare.permission == "movie_night",
                    User.slug.in_(unique_slugs),
                )
            )
        )
    if accessible != unique_slugs:
        raise HTTPException(status_code=404, detail="One or more profiles were not found")


def _account_summary(account: Account) -> dict:
    return {
        "id": account.id,
        "display_name": account.display_name,
        "email": account.email,
        "email_verified": account.email_verified_at is not None,
    }


def _new_email_code(account: Account) -> str:
    code = f"{secrets.randbelow(1_000_000):06d}"
    account.verification_code_hash = hash_password(code)
    account.verification_code_expires_at = datetime.now(UTC) + timedelta(
        minutes=settings.password_reset_code_minutes
    )
    return code


def _deliver_email_verification(account: Account, code: str) -> None:
    send_email_verification_code(
        api_key=settings.resend_api_key or "",
        sender=settings.email_from or "",
        recipient=account.email,
        display_name=account.display_name,
        code=code,
        minutes=settings.password_reset_code_minutes,
    )


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _enforce_rate_limit(decision, message: str) -> None:
    if decision.allowed:
        return
    raise HTTPException(
        status_code=429,
        detail=message,
        headers={"Retry-After": str(decision.retry_after_seconds)},
    )


@app.middleware("http")
async def protect_cookie_authenticated_mutations(request: Request, call_next):
    if (
        settings.web_auth_required
        and request.method.upper() not in {"GET", "HEAD", "OPTIONS"}
        and request.url.path
        not in {"/auth/login", "/auth/register", "/auth/recover", "/auth/recover/request"}
        and request.cookies.get(COOKIE_NAME)
    ):
        cookie_token = request.cookies.get(CSRF_COOKIE_NAME, "")
        header_token = request.headers.get(CSRF_HEADER_NAME, "")
        if not cookie_token or not secrets.compare_digest(cookie_token, header_token):
            return JSONResponse(
                {"detail": "Security token is missing or invalid. Refresh and try again."},
                status_code=403,
            )
    return await call_next(request)


@app.middleware("http")
async def authenticate_web_request(request: Request, call_next):
    if not settings.web_auth_required:
        request.state.account_id = None
        return await call_next(request)
    path = request.url.path
    public = path in {"/", "/health", "/ready", "/tmdb/status"} or path.startswith(
        ("/static/", "/auth/")
    )
    if public:
        return await call_next(request)
    token = request.cookies.get(COOKIE_NAME, "")
    identity = read_session_token(
        settings.web_session_secret,
        token,
        settings.web_session_days * 86_400,
    )
    if identity is None:
        return JSONResponse({"detail": "Sign in required"}, status_code=401)
    with SessionLocal() as session:
        account = session.get(Account, identity.account_id)
        if (
            account is None
            or account.email != identity.email
            or int(account.session_version or 1) != identity.session_version
        ):
            return JSONResponse({"detail": "Session is no longer valid"}, status_code=401)
        request.state.account_id = account.id

        profile_slug = None
        match = re.match(r"^/(?:profiles|recommendations)/([^/]+)", path)
        if match and match.group(1) != "import":
            profile_slug = match.group(1)
        match = re.match(r"^/movies/search/([^/]+)", path)
        if match:
            profile_slug = match.group(1)
        if profile_slug:
            owned = session.scalar(
                select(User.id).where(
                    User.slug == profile_slug,
                    User.owner_account_id == account.id,
                )
            )
            if owned is None:
                return JSONResponse({"detail": "Profile not found"}, status_code=404)
    return await call_next(request)


@app.middleware("http")
async def disable_local_ui_cache(request: Request, call_next):
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
    return response


@app.middleware("http")
async def add_browser_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "base-uri 'self'; "
        "connect-src 'self'; "
        "font-src 'self'; "
        "form-action 'self'; "
        "frame-ancestors 'none'; "
        "img-src 'self' https://image.tmdb.org data:; "
        "object-src 'none'; "
        "script-src 'self'; "
        "style-src 'self'"
    )
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    if settings.app_env.casefold() == "production":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


@app.post("/auth/register")
def register_account(request: AccountRegistration) -> JSONResponse:
    email = normalize_email(request.email)
    with SessionLocal() as session:
        if session.scalar(select(Account.id).where(Account.email == email)) is not None:
            raise HTTPException(status_code=409, detail="An account already uses that email")
        account = Account(
            email=email,
            display_name=request.display_name.strip(),
            password_hash=hash_password(request.password),
            email_verified_at=(
                None if settings.registration_email_verification else datetime.now(UTC)
            ),
        )
        session.add(account)
        verification_code = (
            _new_email_code(account) if settings.registration_email_verification else None
        )
        session.commit()
        session.refresh(account)
        if verification_code is not None:
            try:
                _deliver_email_verification(account, verification_code)
            except EmailDeliveryError as error:
                session.delete(account)
                session.commit()
                logger.warning("Registration email delivery failed: %s", error)
                raise HTTPException(
                    status_code=503,
                    detail="Verification email could not be sent. Please try again.",
                ) from error
            return JSONResponse(
                {"email": account.email, "verification_required": True},
                status_code=202,
            )
        payload = _account_summary(account)
        response = JSONResponse(payload, status_code=201)
        _set_session_cookie(response, account)
        return response


@app.post("/auth/verify-email")
def verify_account_email(http_request: Request, request: EmailVerificationRequest) -> JSONResponse:
    email = normalize_email(request.email)
    decision = login_rate_limiter.consume(
        f"verify-email:{_client_ip(http_request)}:{email}",
        limit=settings.web_login_attempts,
        window_seconds=settings.web_login_window_seconds,
    )
    _enforce_rate_limit(decision, "Too many verification attempts. Please wait and try again.")
    with SessionLocal() as session:
        account = session.scalar(select(Account).where(Account.email == email))
        expiry = account.verification_code_expires_at if account is not None else None
        if expiry is not None and expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=UTC)
        if (
            account is None
            or not account.verification_code_hash
            or expiry is None
            or expiry <= datetime.now(UTC)
            or not verify_password(request.verification_code, account.verification_code_hash)
        ):
            raise HTTPException(status_code=401, detail="Verification code is incorrect or expired")
        account.email_verified_at = datetime.now(UTC)
        account.verification_code_hash = None
        account.verification_code_expires_at = None
        session.commit()
        response = JSONResponse(_account_summary(account))
        _set_session_cookie(response, account)
        return response


@app.post("/auth/verify-email/request", status_code=202)
def request_account_email_verification(
    http_request: Request, request: EmailVerificationCodeRequest
) -> dict:
    if not (settings.resend_api_key or "").strip() or not (settings.email_from or "").strip():
        raise HTTPException(status_code=503, detail="Verification email is not configured")
    email = normalize_email(request.email)
    decision = login_rate_limiter.consume(
        f"verify-email-request:{_client_ip(http_request)}:{email}",
        limit=settings.web_login_attempts,
        window_seconds=settings.web_login_window_seconds,
    )
    _enforce_rate_limit(decision, "Too many code requests. Please wait and try again.")
    with SessionLocal() as session:
        account = session.scalar(select(Account).where(Account.email == email))
        if account is None or account.email_verified_at is not None:
            return {"accepted": True}
        code = _new_email_code(account)
        session.commit()
        try:
            _deliver_email_verification(account, code)
        except EmailDeliveryError as error:
            account.verification_code_hash = None
            account.verification_code_expires_at = None
            session.commit()
            logger.warning(
                "Verification email redelivery failed for account %s: %s", account.id, error
            )
    return {"accepted": True}


@app.post("/auth/login")
def login_account(http_request: Request, request: AccountCredentials) -> JSONResponse:
    email = normalize_email(request.email)
    rate_key = f"login:{_client_ip(http_request)}:{email}"
    decision = login_rate_limiter.consume(
        rate_key,
        limit=settings.web_login_attempts,
        window_seconds=settings.web_login_window_seconds,
    )
    _enforce_rate_limit(decision, "Too many sign-in attempts. Please wait and try again.")
    with SessionLocal() as session:
        account = session.scalar(select(Account).where(Account.email == email))
        if account is None or not verify_password(request.password, account.password_hash):
            raise HTTPException(status_code=401, detail="Email or password is incorrect")
        if settings.registration_email_verification and account.email_verified_at is None:
            raise HTTPException(status_code=403, detail="Verify your email before signing in")
        payload = _account_summary(account)
        response = JSONResponse(payload)
        _set_session_cookie(response, account)
        return response


@app.post("/auth/logout")
def logout_account() -> JSONResponse:
    response = JSONResponse({"signed_out": True})
    response.delete_cookie(COOKIE_NAME, path="/")
    response.delete_cookie(CSRF_COOKIE_NAME, path="/")
    return response


@app.get("/auth/me")
def current_account(request: Request) -> dict:
    token = request.cookies.get(COOKIE_NAME, "")
    identity = read_session_token(
        settings.web_session_secret,
        token,
        settings.web_session_days * 86_400,
    )
    if identity is None:
        raise HTTPException(status_code=401, detail="Sign in required")
    with SessionLocal() as session:
        account = session.get(Account, identity.account_id)
        if (
            account is None
            or account.email != identity.email
            or int(account.session_version or 1) != identity.session_version
            or (settings.registration_email_verification and account.email_verified_at is None)
        ):
            raise HTTPException(status_code=401, detail="Session is no longer valid")
        return _account_summary(account)


@app.put("/auth/password")
def change_account_password(http_request: Request, request: PasswordChangeRequest) -> JSONResponse:
    """Change a signed-in account password after confirming the current password."""
    identity = read_session_token(
        settings.web_session_secret,
        http_request.cookies.get(COOKIE_NAME, ""),
        settings.web_session_days * 86_400,
    )
    if identity is None:
        raise HTTPException(status_code=401, detail="Sign in required")
    with SessionLocal() as session:
        account = session.get(Account, identity.account_id)
        if (
            account is None
            or account.email != identity.email
            or int(account.session_version or 1) != identity.session_version
        ):
            raise HTTPException(status_code=401, detail="Session is no longer valid")
        if not verify_password(request.current_password, account.password_hash):
            raise HTTPException(status_code=401, detail="Current password is incorrect")
        if verify_password(request.new_password, account.password_hash):
            raise HTTPException(
                status_code=422, detail="New password must be different from the current password"
            )
        account.password_hash = hash_password(request.new_password)
        account.session_version = int(account.session_version or 1) + 1
        session.commit()
        response = JSONResponse({"changed": True})
        _set_session_cookie(response, account)
        return response


@app.post("/auth/recover/request", status_code=202)
def request_account_password_reset(
    http_request: Request, request: PasswordResetCodeRequest
) -> dict:
    """Email a short-lived reset code without revealing whether the account exists."""
    if not (settings.resend_api_key or "").strip() or not (settings.email_from or "").strip():
        raise HTTPException(status_code=503, detail="Password-reset email is not configured")
    email = normalize_email(request.email)
    decision = login_rate_limiter.consume(
        f"recover-request:{_client_ip(http_request)}:{email}",
        limit=settings.web_login_attempts,
        window_seconds=settings.web_login_window_seconds,
    )
    _enforce_rate_limit(decision, "Too many reset requests. Please wait and try again.")
    with SessionLocal() as session:
        account = session.scalar(select(Account).where(Account.email == email))
        if account is None:
            return {"accepted": True}
        recovery_code = f"{secrets.randbelow(1_000_000):06d}"
        account.recovery_code_hash = hash_password(recovery_code)
        account.recovery_code_expires_at = datetime.now(UTC) + timedelta(
            minutes=settings.password_reset_code_minutes
        )
        session.commit()
        try:
            send_password_reset_code(
                api_key=settings.resend_api_key or "",
                sender=settings.email_from or "",
                recipient=account.email,
                display_name=account.display_name,
                code=recovery_code,
                minutes=settings.password_reset_code_minutes,
            )
        except EmailDeliveryError as error:
            account.recovery_code_hash = None
            account.recovery_code_expires_at = None
            session.commit()
            logger.warning(
                "Password-reset email delivery failed for account %s: %s",
                account.id,
                error,
            )
    return {"accepted": True}


@app.post("/auth/recover")
def recover_account_password(
    http_request: Request, request: PasswordRecoveryRequest
) -> JSONResponse:
    """Consume a recovery code, reset the password, and revoke existing sessions."""
    email = normalize_email(request.email)
    decision = login_rate_limiter.consume(
        f"recover:{_client_ip(http_request)}:{email}",
        limit=settings.web_login_attempts,
        window_seconds=settings.web_login_window_seconds,
    )
    _enforce_rate_limit(decision, "Too many recovery attempts. Please wait and try again.")
    with SessionLocal() as session:
        account = session.scalar(select(Account).where(Account.email == email))
        expiry = account.recovery_code_expires_at if account is not None else None
        if expiry is not None and expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=UTC)
        if (
            account is None
            or not account.recovery_code_hash
            or expiry is None
            or expiry <= datetime.now(UTC)
            or not verify_password(request.recovery_code, account.recovery_code_hash)
        ):
            raise HTTPException(status_code=401, detail="Recovery information is incorrect")
        account.password_hash = hash_password(request.new_password)
        account.recovery_code_hash = None
        account.recovery_code_expires_at = None
        account.session_version = int(account.session_version or 1) + 1
        session.commit()
        response = JSONResponse(_account_summary(account))
        _set_session_cookie(response, account)
        return response


@app.delete("/auth/account")
def delete_account(http_request: Request, request: AccountDeleteRequest) -> JSONResponse:
    """Permanently remove an account, its profiles, social links, and personal data."""
    if request.confirmation != "DELETE":
        raise HTTPException(status_code=422, detail="Type DELETE exactly to confirm")
    identity = read_session_token(
        settings.web_session_secret,
        http_request.cookies.get(COOKIE_NAME, ""),
        settings.web_session_days * 86_400,
    )
    if identity is None:
        raise HTTPException(status_code=401, detail="Sign in required")
    with SessionLocal() as session:
        account = session.get(Account, identity.account_id)
        if (
            account is None
            or account.email != identity.email
            or int(account.session_version or 1) != identity.session_version
        ):
            raise HTTPException(status_code=401, detail="Session is no longer valid")
        if not verify_password(request.password, account.password_hash):
            raise HTTPException(status_code=401, detail="Password is incorrect")
        profiles = session.scalars(select(User).where(User.owner_account_id == account.id)).all()
        profile_slugs = [profile.slug for profile in profiles]
        for profile in profiles:
            session.delete(profile)
        session.flush()
        session.delete(account)
        session.commit()
    warnings = [warning for slug in profile_slugs for warning in _delete_profile_files(slug)]
    clear_group_recommendation_cache()
    response = JSONResponse(
        {"deleted": True, "profiles_deleted": len(profile_slugs), "warnings": warnings}
    )
    response.delete_cookie(COOKIE_NAME, path="/")
    response.delete_cookie(CSRF_COOKIE_NAME, path="/")
    return response


@app.get("/friends")
def list_friends(request: Request) -> dict:
    account_id = request.state.account_id
    if account_id is None:
        raise HTTPException(status_code=401, detail="Sign in required")
    with SessionLocal() as session:
        relationships = session.scalars(
            select(Friendship).where(
                (Friendship.requester_id == account_id) | (Friendship.addressee_id == account_id)
            )
        ).all()
        account_ids = {
            relationship.addressee_id
            if relationship.requester_id == account_id
            else relationship.requester_id
            for relationship in relationships
        }
        accounts = (
            {
                account.id: account
                for account in session.scalars(select(Account).where(Account.id.in_(account_ids)))
            }
            if account_ids
            else {}
        )
        result = {"friends": [], "incoming": [], "outgoing": []}
        for relationship in relationships:
            other_id = (
                relationship.addressee_id
                if relationship.requester_id == account_id
                else relationship.requester_id
            )
            other = accounts.get(other_id)
            if other is None:
                continue
            item = {"friendship_id": relationship.id, **_account_summary(other)}
            if relationship.status == "accepted":
                item["shared_profiles"] = [
                    {"id": profile.id, "slug": profile.slug, "display_name": profile.display_name}
                    for profile in session.scalars(
                        select(User)
                        .join(ProfileShare, ProfileShare.profile_id == User.id)
                        .where(
                            User.owner_account_id == account_id,
                            ProfileShare.account_id == other.id,
                            ProfileShare.permission == "movie_night",
                        )
                        .order_by(User.display_name, User.slug)
                    )
                ]
                result["friends"].append(item)
            elif relationship.status == "pending" and relationship.addressee_id == account_id:
                result["incoming"].append(item)
            elif relationship.status == "pending":
                result["outgoing"].append(item)
        for values in result.values():
            values.sort(key=lambda item: (item["display_name"].casefold(), item["email"]))
        return result


@app.post("/friends/request", status_code=201)
def request_friend(request: Request, payload: FriendRequest) -> dict:
    account_id = request.state.account_id
    if account_id is None:
        raise HTTPException(status_code=401, detail="Sign in required")
    email = normalize_email(payload.email)
    with SessionLocal() as session:
        addressee = session.scalar(select(Account).where(Account.email == email))
        if addressee is None:
            raise HTTPException(status_code=404, detail="No Movie Compass account uses that email")
        if addressee.id == account_id:
            raise HTTPException(status_code=422, detail="You cannot add your own account")
        existing = session.scalar(
            select(Friendship).where(
                (
                    (Friendship.requester_id == account_id)
                    & (Friendship.addressee_id == addressee.id)
                )
                | (
                    (Friendship.requester_id == addressee.id)
                    & (Friendship.addressee_id == account_id)
                )
            )
        )
        if existing is not None:
            if existing.status == "accepted":
                raise HTTPException(status_code=409, detail="You are already friends")
            if existing.status == "pending" and existing.addressee_id == account_id:
                existing.status = "accepted"
                session.commit()
                return {
                    "status": "accepted",
                    "friendship_id": existing.id,
                    **_account_summary(addressee),
                }
            raise HTTPException(status_code=409, detail="A friend request is already pending")
        friendship = Friendship(
            requester_id=account_id,
            addressee_id=addressee.id,
            status="pending",
        )
        session.add(friendship)
        session.commit()
        session.refresh(friendship)
        return {"status": "pending", "friendship_id": friendship.id, **_account_summary(addressee)}


@app.post("/friends/{friendship_id}/accept")
def accept_friend(friendship_id: int, request: Request) -> dict:
    account_id = request.state.account_id
    if account_id is None:
        raise HTTPException(status_code=401, detail="Sign in required")
    with SessionLocal() as session:
        friendship = session.get(Friendship, friendship_id)
        if (
            friendship is None
            or friendship.addressee_id != account_id
            or friendship.status != "pending"
        ):
            raise HTTPException(status_code=404, detail="Pending friend request not found")
        friendship.status = "accepted"
        requester = session.get(Account, friendship.requester_id)
        session.commit()
        return {"status": "accepted", "friendship_id": friendship.id, **_account_summary(requester)}


@app.delete("/friends/{friendship_id}")
def remove_friend(friendship_id: int, request: Request) -> dict:
    account_id = request.state.account_id
    if account_id is None:
        raise HTTPException(status_code=401, detail="Sign in required")
    with SessionLocal() as session:
        friendship = session.get(Friendship, friendship_id)
        if friendship is None or account_id not in {
            friendship.requester_id,
            friendship.addressee_id,
        }:
            raise HTTPException(status_code=404, detail="Friendship not found")
        account_pair = {friendship.requester_id, friendship.addressee_id}
        shares = session.scalars(
            select(ProfileShare)
            .join(User, User.id == ProfileShare.profile_id)
            .where(
                User.owner_account_id.in_(account_pair),
                ProfileShare.account_id.in_(account_pair),
            )
        ).all()
        for share in shares:
            session.delete(share)
        session.delete(friendship)
        session.commit()
    return {"removed": friendship_id}


@app.post("/friends/{friendship_id}/shares", status_code=201)
def share_profile(friendship_id: int, request: Request, payload: ProfileShareRequest) -> dict:
    account_id = request.state.account_id
    if account_id is None:
        raise HTTPException(status_code=401, detail="Sign in required")
    with SessionLocal() as session:
        friendship = session.get(Friendship, friendship_id)
        if (
            friendship is None
            or friendship.status != "accepted"
            or account_id not in {friendship.requester_id, friendship.addressee_id}
        ):
            raise HTTPException(status_code=404, detail="Accepted friendship not found")
        recipient_id = (
            friendship.addressee_id
            if friendship.requester_id == account_id
            else friendship.requester_id
        )
        profile = session.scalar(
            select(User).where(
                User.slug == payload.profile_slug,
                User.owner_account_id == account_id,
            )
        )
        if profile is None:
            raise HTTPException(status_code=404, detail="Owned profile not found")
        existing = session.scalar(
            select(ProfileShare).where(
                ProfileShare.profile_id == profile.id,
                ProfileShare.account_id == recipient_id,
            )
        )
        if existing is None:
            existing = ProfileShare(
                profile_id=profile.id,
                account_id=recipient_id,
                permission="movie_night",
            )
            session.add(existing)
        else:
            existing.permission = "movie_night"
        session.commit()
        session.refresh(existing)
        return {
            "share_id": existing.id,
            "profile_id": profile.id,
            "profile_slug": profile.slug,
            "display_name": profile.display_name,
            "permission": existing.permission,
        }


@app.delete("/friends/{friendship_id}/shares/{profile_id}")
def unshare_profile(friendship_id: int, profile_id: int, request: Request) -> dict:
    account_id = request.state.account_id
    if account_id is None:
        raise HTTPException(status_code=401, detail="Sign in required")
    with SessionLocal() as session:
        friendship = session.get(Friendship, friendship_id)
        if friendship is None or account_id not in {
            friendship.requester_id,
            friendship.addressee_id,
        }:
            raise HTTPException(status_code=404, detail="Friendship not found")
        recipient_id = (
            friendship.addressee_id
            if friendship.requester_id == account_id
            else friendship.requester_id
        )
        profile = session.scalar(
            select(User).where(User.id == profile_id, User.owner_account_id == account_id)
        )
        share = session.scalar(
            select(ProfileShare).where(
                ProfileShare.profile_id == profile_id,
                ProfileShare.account_id == recipient_id,
            )
        )
        if profile is None or share is None:
            raise HTTPException(status_code=404, detail="Shared profile not found")
        session.delete(share)
        session.commit()
    return {"unshared": profile_id}


@app.get("/", include_in_schema=False)
def frontend() -> FileResponse:
    return FileResponse(static_dir / "index.html")


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "environment": settings.app_env,
        "tmdb": "configured" if settings.tmdb_api_key else "missing",
    }


@app.get("/ready")
def readiness() -> JSONResponse:
    """Report whether this instance can safely receive user traffic."""
    checks = {
        "database": False,
        "catalog": False,
        "tmdb_configured": bool(settings.tmdb_api_key),
    }
    try:
        with SessionLocal() as session:
            session.execute(text("SELECT 1"))
        checks["database"] = True
    except Exception:
        pass
    try:
        _latest_artifact(settings.ml_artifacts_dir)
        checks["catalog"] = True
    except (RecommendationReportNotFound, OSError):
        pass
    ready = all(checks.values())
    return JSONResponse(
        {
            "status": "ready" if ready else "not_ready",
            "environment": settings.app_env,
            "checks": checks,
        },
        status_code=200 if ready else 503,
    )


@app.get("/tmdb/status")
def tmdb_status() -> dict:
    """Actively verify TMDB while advertising the bundled-catalog fallback."""
    if not settings.tmdb_api_key:
        return {
            "configured": False,
            "live": False,
            "fallback_available": True,
            "message": "TMDB key missing; bundled catalog is available.",
        }
    client = TmdbClient(settings.tmdb_api_key)
    try:
        client.check_connection()
        return {
            "configured": True,
            "live": True,
            "fallback_available": True,
            "message": "TMDB live connection is working.",
        }
    except RetryError:
        return {
            "configured": True,
            "live": False,
            "fallback_available": True,
            "message": "TMDB network is temporarily unavailable; bundled catalog is active.",
        }
    except TmdbError as error:
        return {
            "configured": True,
            "live": False,
            "fallback_available": True,
            "message": str(error),
        }
    finally:
        client.close()


@app.get("/catalog/status")
def catalog_status() -> dict:
    summary = load_catalog_summary(settings.processed_data_dir / "tmdb-catalog-manifest.json")
    return {
        "synced": summary is not None,
        "tmdb_daily_export": summary,
    }


@app.get("/profiles")
def profiles(request: Request) -> dict:
    """List only the profiles owned by the signed-in account."""
    artifact = _latest_artifact(settings.ml_artifacts_dir)
    shared_profiles = []
    with SessionLocal() as session:
        query = select(User).order_by(User.created_at, User.slug)
        if settings.web_auth_required:
            query = query.where(User.owner_account_id == request.state.account_id)
        owners = session.scalars(query).all()
        result = []
        for owner in owners:
            ranking_ready = (
                has_profile_artifact(owner.slug, "recommendation", "all")
                or (artifact / "recommendations" / owner.slug / "all.json").exists()
            )
            total = (
                session.scalar(
                    select(func.count())
                    .select_from(ImportMapping)
                    .where(ImportMapping.user_id == owner.id)
                )
                or 0
            )
            if not total:
                continue
            mapped = (
                session.scalar(
                    select(func.count())
                    .select_from(ImportMapping)
                    .where(
                        ImportMapping.user_id == owner.id,
                        ImportMapping.status.in_(("matched", "matched_local", "matched_manual")),
                    )
                )
                or 0
            )
            rated = (
                session.scalar(
                    select(func.count())
                    .select_from(ImportMapping)
                    .where(
                        ImportMapping.user_id == owner.id,
                        ImportMapping.rating.is_not(None),
                    )
                )
                or 0
            )
            result.append(
                {
                    "id": owner.id,
                    "slug": owner.slug,
                    "display_name": owner.display_name,
                    "films": total,
                    "rated": rated,
                    "mapped": mapped,
                    "pending": total - mapped,
                    "ranking_ready": ranking_ready,
                }
            )
        if settings.web_auth_required:
            shared_profiles = [
                {
                    "slug": profile.slug,
                    "display_name": profile.display_name,
                    "permission": share.permission,
                    "shared_by": account.display_name,
                }
                for profile, share, account in session.execute(
                    select(User, ProfileShare, Account)
                    .join(ProfileShare, ProfileShare.profile_id == User.id)
                    .join(Account, Account.id == User.owner_account_id)
                    .where(
                        ProfileShare.account_id == request.state.account_id,
                        ProfileShare.permission == "movie_night",
                    )
                    .order_by(User.display_name, User.slug)
                )
            ]
    deduplicated: dict[str, dict] = {}
    for profile in result:
        key = profile["slug"].casefold()
        existing = deduplicated.get(key)
        if (
            existing is None
            or profile["mapped"] > existing["mapped"]
            or profile["mapped"] == existing["mapped"]
            and profile["ranking_ready"]
            and not existing["ranking_ready"]
        ):
            deduplicated[key] = profile
    return {
        "profiles": list(deduplicated.values()),
        "shared_profiles": shared_profiles,
    }


@app.patch("/profiles/{user}")
def update_profile(user: str, request: ProfileUpdateRequest) -> dict:
    """Update user-facing profile details without changing its stable internal ID."""
    from app.services.recommendation_reports import VALID_USER

    if not VALID_USER.fullmatch(user):
        raise HTTPException(status_code=422, detail="Invalid profile ID")
    display_name = request.display_name.strip()
    if not display_name:
        raise HTTPException(status_code=422, detail="Display name cannot be empty")
    with SessionLocal() as session:
        owner = session.scalar(select(User).where(User.slug == user))
        if owner is None:
            raise HTTPException(status_code=404, detail="Profile not found")
        owner.display_name = display_name
        session.commit()
    return {"slug": user, "display_name": display_name}


@app.get("/profiles/{user}/stats")
def profile_stats(
    user: str,
    watched_year_min: int | None = Query(default=None, ge=1870, le=2200),
    watched_year_max: int | None = Query(default=None, ge=1870, le=2200),
) -> dict:
    """Return rating-only statistics for one imported profile."""
    from app.services.recommendation_reports import VALID_USER

    if not VALID_USER.fullmatch(user):
        raise HTTPException(status_code=422, detail="Invalid profile ID")
    if (
        watched_year_min is not None
        and watched_year_max is not None
        and watched_year_min > watched_year_max
    ):
        raise HTTPException(status_code=422, detail="From year must not exceed through year")
    with SessionLocal() as session:
        owner = session.scalar(select(User).where(User.slug == user))
        if owner is None:
            raise HTTPException(status_code=404, detail="Profile not found")
        rows = session.execute(
            select(
                Movie.title,
                UserMovieInteraction.rating,
                UserMovieInteraction.review_text,
                UserMovieInteraction.rewatch_count,
                Movie.year,
                UserMovieInteraction.watched_date,
                Movie.tmdb_id,
                Movie.runtime,
            )
            .join(Movie, Movie.id == UserMovieInteraction.movie_id)
            .where(
                UserMovieInteraction.user_id == owner.id,
                UserMovieInteraction.rating.is_not(None),
            )
        ).all()
        last_import = session.scalar(
            select(func.max(ImportRun.completed_at)).where(ImportRun.user_id == owner.id)
        )
    all_rows = rows
    if watched_year_min is not None or watched_year_max is not None:
        rows = [
            row
            for row in all_rows
            if row.watched_date is not None
            and (watched_year_min is None or row.watched_date.year >= watched_year_min)
            and (watched_year_max is None or row.watched_date.year <= watched_year_max)
        ]
    ratings = [float(row.rating) for row in rows]
    distribution = {f"{value / 2:.1f}": 0 for value in range(1, 11)}
    for rating in ratings:
        distribution[f"{rating:.1f}"] = distribution.get(f"{rating:.1f}", 0) + 1
    details_raw = _load_profile_detail_cache()
    details_by_id = {int(key): value for key, value in details_raw.items() if str(key).isdigit()}
    taste_breakdown = build_taste_breakdown(
        [
            {
                "rating": float(row.rating),
                "year": row.year,
                "tmdb_id": row.tmdb_id,
                "runtime": row.runtime,
            }
            for row in rows
        ],
        details_by_id,
    )
    return {
        "slug": owner.slug,
        "display_name": owner.display_name,
        "rated_films": len(ratings),
        "mapped_films": len(rows),
        "pending_films": 0,
        "average_rating": round(sum(ratings) / len(ratings), 2) if ratings else None,
        "median_rating": round(float(median(ratings)), 2) if ratings else None,
        "lowest_rating": min(ratings) if ratings else None,
        "highest_rating": max(ratings) if ratings else None,
        "rated_reviews": sum(bool((row.review_text or "").strip()) for row in rows),
        "rewatches": sum(int(row.rewatch_count or 0) for row in rows),
        "rewatched_titles": [
            {"title": row.title, "count": int(row.rewatch_count or 0)}
            for row in rows
            if int(row.rewatch_count or 0) > 0
        ],
        "rating_distribution": distribution,
        "taste_breakdown": taste_breakdown,
        "watched_year_filter": {
            "minimum": watched_year_min,
            "maximum": watched_year_max,
        },
        "available_watched_years": {
            "minimum": min(
                (row.watched_date.year for row in all_rows if row.watched_date), default=None
            ),
            "maximum": max(
                (row.watched_date.year for row in all_rows if row.watched_date), default=None
            ),
        },
        "available_review_years": sorted(
            {
                row.watched_date.year
                for row in all_rows
                if row.watched_date and str(row.review_text or "").strip()
            },
            reverse=True,
        ),
        "undated_ratings_excluded": (
            sum(row.watched_date is None for row in all_rows)
            if watched_year_min is not None or watched_year_max is not None
            else 0
        ),
        "last_imported_at": last_import.isoformat() if last_import else None,
    }


@app.get("/profiles/{user}/stats/movies")
def profile_stat_movies(
    user: str,
    category: str = Query(max_length=30),
    value: str = Query(min_length=1, max_length=200),
    watched_year: int | None = Query(default=None, ge=1870, le=2200),
) -> dict:
    """List the unique rated films contributing to one taste-stat row."""
    from app.services.recommendation_reports import VALID_USER

    valid_categories = {
        "genres",
        "themes",
        "decades",
        "directors",
        "actors",
        "languages",
        "runtimes",
        "popularity",
        "certifications",
    }
    if not VALID_USER.fullmatch(user):
        raise HTTPException(status_code=422, detail="Invalid profile ID")
    if category not in valid_categories:
        raise HTTPException(status_code=422, detail="Invalid statistics category")
    with SessionLocal() as session:
        owner = session.scalar(select(User).where(User.slug == user))
        if owner is None:
            raise HTTPException(status_code=404, detail="Profile not found")
        rows = session.execute(
            select(Movie, UserMovieInteraction)
            .join(UserMovieInteraction, UserMovieInteraction.movie_id == Movie.id)
            .where(
                UserMovieInteraction.user_id == owner.id,
                UserMovieInteraction.rating.is_not(None),
            )
            .order_by(
                UserMovieInteraction.watched_date.desc(),
                UserMovieInteraction.rating.desc(),
                Movie.title,
            )
        ).all()
    details_raw = _load_profile_detail_cache()
    matches = []
    for movie, interaction in rows:
        if watched_year is not None and (
            interaction.watched_date is None or interaction.watched_date.year != watched_year
        ):
            continue
        details = details_raw.get(str(movie.tmdb_id), {}) if movie.tmdb_id else {}
        labels = movie_category_labels({"year": movie.year, "runtime": movie.runtime}, details).get(
            category, ()
        )
        if value not in labels:
            continue
        poster_path = movie.poster_path or details.get("poster_path")
        matches.append(
            {
                "tmdb_id": movie.tmdb_id,
                "title": movie.title,
                "year": movie.year,
                "rating": float(interaction.rating),
                "watched_date": (
                    interaction.watched_date.isoformat() if interaction.watched_date else None
                ),
                "review_text": interaction.review_text,
                "poster_url": (
                    f"https://image.tmdb.org/t/p/w185{poster_path}" if poster_path else None
                ),
            }
        )
    return {
        "user": user,
        "display_name": owner.display_name,
        "category": category,
        "value": value,
        "watched_year": watched_year,
        "count": len(matches),
        "movies": matches,
    }


@app.get("/profiles/{user}/stats/category")
def profile_stat_category(
    user: str,
    category: str = Query(max_length=30),
    watched_year: int | None = Query(default=None, ge=1870, le=2200),
) -> dict:
    """Return the strongest and weakest values within one taste category."""
    from app.services.recommendation_reports import VALID_USER

    valid_categories = {
        "genres",
        "themes",
        "decades",
        "directors",
        "actors",
        "languages",
        "runtimes",
        "popularity",
        "certifications",
    }
    if not VALID_USER.fullmatch(user):
        raise HTTPException(status_code=422, detail="Invalid profile ID")
    if category not in valid_categories:
        raise HTTPException(status_code=422, detail="Invalid statistics category")
    with SessionLocal() as session:
        owner = session.scalar(select(User).where(User.slug == user))
        if owner is None:
            raise HTTPException(status_code=404, detail="Profile not found")
        display_name = owner.display_name
        rows = session.execute(
            select(Movie, UserMovieInteraction)
            .join(UserMovieInteraction, UserMovieInteraction.movie_id == Movie.id)
            .where(
                UserMovieInteraction.user_id == owner.id,
                UserMovieInteraction.rating.is_not(None),
            )
        ).all()
    if watched_year is not None:
        rows = [
            (movie, interaction)
            for movie, interaction in rows
            if interaction.watched_date is not None
            and interaction.watched_date.year == watched_year
        ]
    details_raw = _load_profile_detail_cache()
    details_by_id = {int(key): value for key, value in details_raw.items() if str(key).isdigit()}
    breakdown = build_taste_breakdown(
        [
            {
                "rating": float(interaction.rating),
                "year": movie.year,
                "tmdb_id": movie.tmdb_id,
                "runtime": movie.runtime,
            }
            for movie, interaction in rows
        ],
        details_by_id,
        limit=None,
        include_singletons=True,
    )
    ranked = breakdown.get(category, [])
    top_count = min(25, (len(ranked) + 1) // 2) if len(ranked) <= 50 else 25
    bottom_count = min(25, len(ranked) - top_count) if len(ranked) <= 50 else 25
    return {
        "user": user,
        "display_name": display_name,
        "category": category,
        "watched_year": watched_year,
        "count": len(ranked),
        "top": ranked[:top_count],
        "bottom": list(reversed(ranked[-bottom_count:])) if bottom_count else [],
    }


@app.get("/profiles/{user}/ratings")
def profile_rating_history(user: str) -> dict:
    """Return a profile's rated movies, newest watches first and high ratings first."""
    from app.services.recommendation_reports import VALID_USER

    if not VALID_USER.fullmatch(user):
        raise HTTPException(status_code=422, detail="Invalid profile ID")
    with SessionLocal() as session:
        owner = session.scalar(select(User).where(User.slug == user))
        if owner is None:
            raise HTTPException(status_code=404, detail="Profile not found")
        rows = session.execute(
            select(Movie, UserMovieInteraction)
            .join(UserMovieInteraction, UserMovieInteraction.movie_id == Movie.id)
            .where(
                UserMovieInteraction.user_id == owner.id,
                UserMovieInteraction.rating.is_not(None),
            )
            .order_by(
                UserMovieInteraction.watched_date.desc(),
                UserMovieInteraction.imported_at.desc(),
                UserMovieInteraction.rating.desc(),
                Movie.title,
            )
        ).all()
    metadata_caches = []
    for cache_name in ("display-metadata.json", "tmdb-rich-details.json"):
        cache_path = settings.processed_data_dir / cache_name
        try:
            metadata_caches.append(
                json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.is_file() else {}
            )
        except (OSError, json.JSONDecodeError):
            metadata_caches.append({})
    ratings = []
    for movie, interaction in rows:
        metadata = next(
            (
                cache[str(movie.tmdb_id)]
                for cache in metadata_caches
                if movie.tmdb_id and str(movie.tmdb_id) in cache
            ),
            {},
        )
        poster_path = movie.poster_path or metadata.get("poster_path")
        ratings.append(
            {
                "movie_id": movie.id,
                "tmdb_id": movie.tmdb_id,
                "title": movie.title,
                "year": movie.year,
                "rating": float(interaction.rating),
                "watched_date": (
                    interaction.watched_date.isoformat() if interaction.watched_date else None
                ),
                "review_text": interaction.review_text,
                "rewatch_count": int(interaction.rewatch_count or 0),
                "poster_url": (
                    f"https://image.tmdb.org/t/p/w185{poster_path}" if poster_path else None
                ),
            }
        )
    return {
        "user": user,
        "display_name": owner.display_name,
        "count": len(ratings),
        "sort": "watched_date_desc_then_rating_desc",
        "ratings": ratings,
    }


@app.get("/profiles/{user}/export")
def export_profile(user: str) -> StreamingResponse:
    """Download a rating-only profile backup accepted by the existing import flow."""
    from app.services.recommendation_reports import VALID_USER

    if not VALID_USER.fullmatch(user):
        raise HTTPException(status_code=422, detail="Invalid profile ID")
    try:
        with SessionLocal() as session:
            content, filename, _ = build_profile_archive(
                session,
                user,
                settings.processed_data_dir / "tmdb-rich-details.json",
            )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return StreamingResponse(
        io.BytesIO(content),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/profiles/{user}/accuracy")
def profile_accuracy(user: str) -> dict:
    """Measure personal prediction accuracy using repeated held-out ratings."""
    from app.services.recommendation_reports import VALID_USER

    if not VALID_USER.fullmatch(user):
        raise HTTPException(status_code=422, detail="Invalid profile ID")
    try:
        result = evaluate_profile_accuracy(_latest_artifact(settings.ml_artifacts_dir), user)
        result["review_signal_policy"] = load_review_policy(
            settings.processed_data_dir / "review-policies" / f"{user}.json"
        )
        return result
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/movies/rating-search")
def rating_movie_search(
    q: str = Query(min_length=2, max_length=120),
    year: int | None = Query(default=None, ge=1870, le=2200),
    user: str | None = Query(default=None, max_length=100),
) -> dict:
    """Find exact TMDB records before adding a manual profile rating."""
    # Results are public metadata. Profile ownership is enforced when the
    # selected rating is written, not while a user is browsing movie titles.
    if not settings.tmdb_api_key:
        results = _local_movie_search_results(q, year, 12)
        if not results:
            raise HTTPException(status_code=503, detail="TMDB_API_KEY is not configured")
        search_warning = "TMDB is unavailable; showing matches from the bundled catalog."
    else:
        client = TmdbClient(settings.tmdb_api_key)
        local_results = _local_movie_search_results(q, year, 12)
        try:
            live_movies = client.search_movie(q, year, include_adult=True)
            live_tv = [
                normalize_tv_search_result(item)
                for item in client.search_tv(q, year, include_adult=True)
                if item.get("id") is not None
            ]
            live_results = [*live_movies, *live_tv]
            results_by_id = {
                int(item["id"]): item for item in [*local_results, *live_results] if item.get("id")
            }
            ranked_results = rank_title_search_results(q, [*live_results, *local_results], 12)
            ordered_ids = [int(item["id"]) for item in ranked_results if item.get("id")]
            results = [results_by_id[item_id] for item_id in dict.fromkeys(ordered_ids)][:12]
            search_warning = None
        except RetryError as error:
            if not local_results:
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "TMDB is temporarily unreachable, and this title is not in the bundled "
                        "offline catalog. Please retry when the connection is available."
                    ),
                ) from error
            results = local_results
            search_warning = "TMDB is temporarily unreachable; showing bundled catalog matches."
        except Exception as error:
            raise HTTPException(
                status_code=503,
                detail=f"Movie search could not reach TMDB: {type(error).__name__}",
            ) from error
        finally:
            client.close()
    result_rows = [
        {
            "tmdb_id": int(item["id"]),
            "title": item.get("title") or item.get("original_title") or "Untitled",
            "year": (
                int(str(item.get("release_date"))[:4])
                if str(item.get("release_date") or "")[:4].isdigit()
                else None
            ),
            "poster_url": (
                f"https://image.tmdb.org/t/p/w185{item['poster_path']}"
                if item.get("poster_path")
                else None
            ),
            "media_type": item.get("media_type") or "movie",
            "adult": bool(item.get("adult", False)),
        }
        for item in results
        if item.get("id") is not None
    ]
    if user:
        from app.services.recommendation_reports import VALID_USER

        if not VALID_USER.fullmatch(user):
            raise HTTPException(status_code=422, detail="Invalid profile ID")
        result_ids = [item["tmdb_id"] for item in result_rows]
        with SessionLocal() as session:
            existing = session.execute(
                select(Movie.tmdb_id, UserMovieInteraction)
                .join(UserMovieInteraction, UserMovieInteraction.movie_id == Movie.id)
                .join(User, User.id == UserMovieInteraction.user_id)
                .where(User.slug == user, Movie.tmdb_id.in_(result_ids))
            ).all()
        by_tmdb = {int(tmdb_id): interaction for tmdb_id, interaction in existing}
        for item in result_rows:
            interaction = by_tmdb.get(item["tmdb_id"])
            item["current_rating"] = (
                float(interaction.rating) if interaction and interaction.rating else None
            )
            item["current_review_text"] = interaction.review_text if interaction else None
    return {"results": result_rows, "warning": search_warning}


@app.delete("/profiles/{user}/ratings/{movie_id}")
def delete_profile_rating(user: str, movie_id: int) -> dict:
    """Remove one mistaken rating and rebuild the profile without that movie."""
    from app.services.recommendation_reports import VALID_USER

    if not VALID_USER.fullmatch(user):
        raise HTTPException(status_code=422, detail="Invalid profile ID")
    with SessionLocal() as session:
        owner = session.scalar(select(User).where(User.slug == user))
        if owner is None:
            raise HTTPException(status_code=404, detail="Profile not found")
        row = session.execute(
            select(Movie, UserMovieInteraction)
            .join(UserMovieInteraction, UserMovieInteraction.movie_id == Movie.id)
            .where(
                UserMovieInteraction.user_id == owner.id,
                UserMovieInteraction.movie_id == movie_id,
                UserMovieInteraction.rating.is_not(None),
            )
        ).one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="Rated movie not found")
        movie, interaction = row
        title = movie.title
        mappings = session.scalars(
            select(ImportMapping).where(
                ImportMapping.user_id == owner.id,
                ImportMapping.movie_id == movie.id,
            )
        ).all()
        for mapping in mappings:
            session.delete(mapping)
        session.delete(interaction)
        session.commit()

    review_policy_warning = None
    try:
        refresh_review_policy(
            user,
            settings.processed_data_dir / "tmdb-rich-details.json",
            settings.processed_data_dir / "review-policies" / f"{user}.json",
        )
    except Exception as error:
        review_policy_warning = f"Review policy refresh failed: {type(error).__name__}"
    clear_group_recommendation_cache()
    ranking_warning = None
    try:
        generate_recommendations(
            _latest_artifact(settings.ml_artifacts_dir),
            user=user,
            limit=20,
            scope="all",
            live_tmdb=False,
            persist=True,
            emit=False,
        )
    except Exception as error:
        ranking_warning = f"Rating removed, but ranking refresh failed: {type(error).__name__}"
    return {
        "user": user,
        "movie_id": movie_id,
        "title": title,
        "deleted": True,
        "ranking_updated": ranking_warning is None,
        "ranking_warning": ranking_warning,
        "review_policy_warning": review_policy_warning,
    }


@app.put("/profiles/{user}/ratings")
def save_manual_rating(user: str, request: ManualRatingRequest) -> dict:
    """Add or update one rated film and immediately refresh the personal model."""
    from app.services.recommendation_reports import VALID_USER

    if not VALID_USER.fullmatch(user):
        raise HTTPException(status_code=422, detail="Invalid profile ID")
    if request.tmdb_id == 0:
        raise HTTPException(status_code=422, detail="Invalid TMDB title ID")
    details_cache_path = settings.processed_data_dir / "tmdb-rich-details.json"
    try:
        cached_details = (
            json.loads(details_cache_path.read_text(encoding="utf-8"))
            if details_cache_path.is_file()
            else {}
        )
    except (OSError, json.JSONDecodeError):
        cached_details = {}
    cached_movie = cached_details.get(str(request.tmdb_id))
    details_warning = None
    details = None
    if settings.tmdb_api_key:
        client = TmdbClient(settings.tmdb_api_key)
        try:
            if is_tv_catalog_id(request.tmdb_id):
                details = normalize_tv_details(
                    client.tv_details(
                        abs(request.tmdb_id),
                        "keywords,credits,content_ratings",
                    )
                )
            else:
                details = client.movie_details(
                    request.tmdb_id,
                    "keywords,credits,release_dates",
                )
        except RetryError as error:
            if cached_movie and cached_movie.get("missing") is not True:
                details = cached_movie
                details_warning = (
                    "TMDB was temporarily unreachable; verified bundled movie details were used."
                )
            else:
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "TMDB is temporarily unreachable and this movie is not yet cached. "
                        "Your rating was not changed; please retry shortly."
                    ),
                ) from error
        except Exception as error:
            raise HTTPException(
                status_code=422, detail="TMDB rejected the selected movie details"
            ) from error
        finally:
            client.close()
    elif cached_movie and cached_movie.get("missing") is not True:
        details = cached_movie
        details_warning = "Verified bundled movie details were used."
    else:
        raise HTTPException(
            status_code=503,
            detail="TMDB is unavailable and this selected movie is not in the bundled cache.",
        )
    assert details is not None
    cached_details[str(request.tmdb_id)] = details
    details_cache_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_cache = details_cache_path.with_suffix(".tmp")
    temporary_cache.write_text(json.dumps(cached_details), encoding="utf-8")
    temporary_cache.replace(details_cache_path)
    release = str(details.get("release_date") or "")
    try:
        release_date = date.fromisoformat(release) if release else None
    except ValueError:
        release_date = None
    title = str(details.get("title") or request.title)
    year = int(release[:4]) if release[:4].isdigit() else request.year
    review = (request.review_text or "").strip() or None
    entered_on = date.today()
    with SessionLocal() as session:
        owner = session.scalar(select(User).where(User.slug == user))
        if owner is None:
            raise HTTPException(status_code=404, detail="Profile not found")
        movie = session.scalar(select(Movie).where(Movie.tmdb_id == request.tmdb_id))
        if movie is None:
            movie = Movie(tmdb_id=request.tmdb_id, title=title, original_title=title, year=year)
            session.add(movie)
            session.flush()
        movie.title = title
        movie.original_title = details.get("original_title") or title
        movie.year = year
        movie.release_date = release_date
        movie.overview = details.get("overview")
        movie.runtime = details.get("runtime")
        movie.poster_path = details.get("poster_path")
        interaction = session.scalar(
            select(UserMovieInteraction).where(
                UserMovieInteraction.user_id == owner.id,
                UserMovieInteraction.movie_id == movie.id,
            )
        )
        if interaction is None:
            interaction = UserMovieInteraction(
                user_id=owner.id,
                movie_id=movie.id,
                source="manual",
            )
            session.add(interaction)
        interaction.rating = Decimal(str(request.rating))
        interaction.review_text = review
        interaction.watched = True
        interaction.watched_date = entered_on
        mapping = session.scalar(
            select(ImportMapping).where(
                ImportMapping.user_id == owner.id,
                ImportMapping.movie_id == movie.id,
            )
        )
        if mapping is None:
            mapping = ImportMapping(
                user_id=owner.id,
                source="manual",
                source_key=(
                    f"tmdb:tv:{abs(request.tmdb_id)}"
                    if is_tv_catalog_id(request.tmdb_id)
                    else f"tmdb:movie:{request.tmdb_id}"
                ),
                movie_id=movie.id,
                title=title,
                year=year,
                status="matched_manual",
                watched=True,
            )
            session.add(mapping)
        mapping.rating = Decimal(str(request.rating))
        mapping.review_text = review
        mapping.watched = True
        mapping.watched_date = entered_on
        mapping.title = title
        mapping.year = year
        mapping.status = "matched_manual" if mapping.source == "manual" else mapping.status
        session.commit()
    review_policy = refresh_review_policy(
        user,
        settings.processed_data_dir / "tmdb-rich-details.json",
        settings.processed_data_dir / "review-policies" / f"{user}.json",
    )
    clear_group_recommendation_cache()
    ranking_warning = None
    try:
        generate_recommendations(
            _latest_artifact(settings.ml_artifacts_dir),
            user=user,
            limit=20,
            scope="all",
            live_tmdb=False,
            persist=True,
            emit=False,
        )
    except Exception as error:
        ranking_warning = f"Rating saved, but ranking refresh failed: {type(error).__name__}"
    return {
        "user": user,
        "tmdb_id": request.tmdb_id,
        "media_type": "tv" if is_tv_catalog_id(request.tmdb_id) else "movie",
        "title": title,
        "rating": request.rating,
        "review_saved": review is not None,
        "ranking_updated": ranking_warning is None,
        "ranking_warning": ranking_warning,
        "details_warning": details_warning,
        "review_signal_policy": review_policy,
    }


def _delete_profile_files(user: str) -> list[str]:
    warnings = []
    artifact_root = settings.ml_artifacts_dir.resolve()
    for candidate in artifact_root.glob(f"*/recommendations/{user}"):
        resolved = candidate.resolve()
        if not resolved.is_relative_to(artifact_root) or not resolved.is_dir():
            continue
        try:
            rmtree(resolved)
        except OSError:
            warnings.append(f"Could not remove generated ranking directory: {resolved.name}")
    policy = (settings.processed_data_dir / "review-policies" / f"{user}.json").resolve()
    processed_root = settings.processed_data_dir.resolve()
    if policy.is_relative_to(processed_root) and policy.is_file():
        try:
            policy.unlink()
        except OSError:
            warnings.append("Could not remove the generated review-policy file")
    return warnings


@app.delete("/profiles/{user}")
def delete_profile(user: str, request: ProfileDeleteRequest) -> dict:
    """Delete one profile and its imported interactions after exact typed confirmation."""
    from app.services.recommendation_reports import VALID_USER

    if not VALID_USER.fullmatch(user):
        raise HTTPException(status_code=422, detail="Invalid profile ID")
    with SessionLocal() as session:
        owner = session.scalar(select(User).where(User.slug == user))
        if owner is None:
            raise HTTPException(status_code=404, detail="Profile not found")
        if request.confirmation not in {owner.slug, owner.display_name}:
            raise HTTPException(
                status_code=422,
                detail=f"Type {owner.display_name} or {owner.slug} exactly to confirm deletion",
            )
        owner_id = owner.id
        session.delete(owner)
        session.commit()
    delete_profile_artifacts(owner_id)
    warnings = _delete_profile_files(user)
    clear_group_recommendation_cache()
    return {"deleted": user, "warnings": warnings}


@app.post("/groups/recommendations")
def group_recommendations(http_request: Request, request: GroupRecommendationRequest) -> dict:
    _require_movie_night_profiles(http_request.state.account_id, request.users)
    try:
        report = generate_group_recommendations(
            _latest_artifact(settings.ml_artifacts_dir),
            request.users,
            limit=request.limit,
            year_min=request.year_min,
            year_max=request.year_max,
            runtime_min=request.runtime_min,
            runtime_max=request.runtime_max,
            popularity=request.popularity,
            genre=request.genre,
            media_type=request.media_type,
            include_watched=request.include_watched,
        )
        return _with_display_metadata(report, request.country)
    except (ValueError, typer.BadParameter) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Group ranking could not be completed: {type(error).__name__}",
        ) from error


@app.post("/groups/search")
def group_movie_search(http_request: Request, request: GroupMovieSearchRequest) -> dict:
    """Search TMDB and score exact matches for every profile in a movie-night group."""
    _require_movie_night_profiles(http_request.state.account_id, request.users)
    try:
        tmdb_ids = _tmdb_search_ids(request.query, request.year, request.limit)
        if not tmdb_ids:
            return {
                "users": request.users,
                "query": request.query,
                "year": request.year,
                "recommendations": [],
            }
        report = generate_group_recommendations(
            _latest_artifact(settings.ml_artifacts_dir),
            request.users,
            limit=request.limit,
            candidate_tmdb_ids=",".join(str(value) for value in tmdb_ids),
            include_watched=True,
            bottom_limit=0,
        )
        relevance = {tmdb_id: position for position, tmdb_id in enumerate(tmdb_ids)}
        report["recommendations"].sort(
            key=lambda item: relevance.get(int(item["tmdb_id"]), len(relevance))
        )
        for rank, item in enumerate(report["recommendations"], start=1):
            item["rank"] = rank
        report["query"] = request.query
        report["search_year"] = request.year
        report["tmdb_matches"] = len(tmdb_ids)
        return _with_display_metadata(report, request.country)
    except HTTPException:
        raise
    except (ValueError, typer.BadParameter) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Group movie lookup could not be completed: {type(error).__name__}",
        ) from error


@app.get("/recommendations/{user}/scopes")
def recommendation_scopes(user: str) -> dict:
    try:
        return available_recommendation_scopes(settings.ml_artifacts_dir, user)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except RecommendationReportNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.get("/recommendations/{user}")
def recommendations(
    user: str,
    scope: str = Query(default="all"),
    year_min: int | None = Query(default=None, ge=1870, le=2200),
    year_max: int | None = Query(default=None, ge=1870, le=2200),
    runtime_min: int | None = Query(default=None, ge=1, le=600),
    runtime_max: int | None = Query(default=None, ge=1, le=600),
    genre: str | None = Query(default=None, max_length=60),
    media_type: str = Query(default="all", pattern=r"^(all|movie|tv)$"),
    country: str = Query(default="US", pattern=r"^[A-Z]{2}$"),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict:
    try:
        if media_type != "all":
            report = generate_recommendations(
                _latest_artifact(settings.ml_artifacts_dir),
                user=user,
                limit=limit,
                scope=scope,
                year_min=year_min,
                year_max=year_max,
                runtime_min=runtime_min,
                runtime_max=runtime_max,
                genre=genre,
                media_type=media_type,
                live_tmdb=media_type == "tv",
                persist=False,
                emit=False,
            )
            return _with_display_metadata(report, country)
        report = load_recommendation_report(
            settings.ml_artifacts_dir,
            user,
            scope=scope,
            year_min=year_min,
            year_max=year_max,
            runtime_min=runtime_min,
            runtime_max=runtime_max,
            genre=genre,
            media_type=media_type,
            limit=limit,
        )
        return _with_display_metadata(report, country)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except RecommendationReportNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.post("/profiles/import")
async def import_profile(
    request: Request,
    user: Annotated[str, Form()],
    archive: Annotated[UploadFile, File()],
) -> dict:
    """Import a Letterboxd ZIP locally, then map its titles to TMDB."""
    from app.services.recommendation_reports import VALID_USER

    if not getattr(request.state, "rate_limit_checked", False):
        account_key = request.state.account_id or _client_ip(request)
        decision = profile_import_rate_limiter.consume(
            f"profile-import:{account_key}",
            limit=settings.web_profile_imports_per_hour,
            window_seconds=3600,
        )
        _enforce_rate_limit(
            decision,
            "Too many profile imports. Please wait before uploading another export.",
        )

    if not VALID_USER.fullmatch(user):
        raise HTTPException(status_code=422, detail="Use only letters, numbers, - or _ for profile")
    if not archive.filename or not archive.filename.casefold().endswith(".zip"):
        raise HTTPException(status_code=422, detail="Select a Letterboxd .zip export")
    max_bytes = 100 * 1024 * 1024
    size = 0
    try:
        with TemporaryDirectory(prefix="letterboxd-import-") as directory:
            target = Path(directory) / "letterboxd.zip"
            with target.open("wb") as handle:
                while chunk := await archive.read(1024 * 1024):
                    size += len(chunk)
                    if size > max_bytes:
                        raise HTTPException(status_code=413, detail="Export exceeds 100 MB")
                    handle.write(chunk)
            with SessionLocal() as session:
                existing = session.scalar(select(User).where(User.slug == user))
                if existing is not None and existing.owner_account_id not in {
                    None,
                    request.state.account_id,
                }:
                    raise HTTPException(
                        status_code=409,
                        detail="That profile ID is already used by another account",
                    )
                imported = import_letterboxd_archive(session, target, user)
                imported_profile = session.get(User, imported.user_id)
                if imported_profile is None:
                    raise HTTPException(status_code=500, detail="Imported profile was not saved")
                if settings.web_auth_required:
                    imported_profile.owner_account_id = request.state.account_id
                session.commit()
                totals = {"processed": 0, "matched": 0, "ambiguous": 0, "unresolved": 0}
                mapping_warning = None
                portable_mapping_restored = restore_profile_archive(
                    session,
                    target,
                    user,
                    settings.processed_data_dir / "tmdb-rich-details.json",
                )
                totals["matched"] += portable_mapping_restored
                local = map_pending_from_artifact(
                    session,
                    imported.user_id,
                    _latest_artifact(settings.ml_artifacts_dir),
                    settings.processed_data_dir / "tmdb-rich-details.json",
                )
                totals["matched"] += local.matched
                client = TmdbClient(settings.tmdb_api_key) if settings.tmdb_api_key else None
                if client is None:
                    mapping_warning = (
                        "Live TMDB mapping is unavailable; exact backup mappings and the "
                        "bundled catalog were used. Unmapped films remain safely pending."
                    )
                else:
                    try:
                        while True:
                            batch = map_pending_letterboxd(
                                session,
                                client,
                                imported.user_id,
                                limit=100,
                                ttl_seconds=settings.tmdb_cache_ttl_seconds,
                            )
                            for key in totals:
                                totals[key] += getattr(batch, key)
                            if batch.processed < 100:
                                break
                        direct = resolve_letterboxd_links(session, client, imported.user_id)
                        totals["matched"] += direct.matched
                        mapped_tmdb_ids = {
                            int(value)
                            for value in session.scalars(
                                select(Movie.tmdb_id)
                                .join(ImportMapping, ImportMapping.movie_id == Movie.id)
                                .where(
                                    ImportMapping.user_id == imported.user_id,
                                    ImportMapping.rating.is_not(None),
                                    Movie.tmdb_id.is_not(None),
                                )
                            )
                        }
                        load_or_fetch_details(
                            client,
                            mapped_tmdb_ids,
                            settings.processed_data_dir / "tmdb-rich-details.json",
                        )
                    except RetryError:
                        mapping_warning = (
                            "The export was imported, but TMDB mapping could not reach "
                            "the network. "
                            "Unmapped films remain safely pending."
                        )
                    finally:
                        client.close()
            review_policy = refresh_review_policy(
                user,
                settings.processed_data_dir / "tmdb-rich-details.json",
                settings.processed_data_dir / "review-policies" / f"{user}.json",
            )
            clear_group_recommendation_cache()
    except HTTPException:
        raise
    except (ValueError, OSError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    finally:
        await archive.close()
    return {
        "user": user,
        "import": asdict(imported),
        "mapping": totals,
        "local_mapping": asdict(local),
        "portable_mapping_restored": portable_mapping_restored,
        "latest_review_only": True,
        "rewatch_count_retained": True,
        "archive_retained": False,
        "mapping_complete": mapping_warning is None,
        "mapping_warning": mapping_warning,
        "review_signal_policy": review_policy,
    }


def _job_payload(job: WebJob) -> dict:
    result = None
    if job.result_json:
        try:
            result = json.loads(job.result_json)
        except json.JSONDecodeError:
            result = None
    return {
        "id": job.id,
        "job_type": job.job_type,
        "profile_slug": job.profile_slug,
        "status": job.status,
        "progress_message": job.progress_message,
        "result": result,
        "error": job.error_message,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
    }


def _update_import_job(job_id: str, **values) -> None:
    with SessionLocal() as session:
        job = session.get(WebJob, job_id)
        if job is None:
            return
        for key, value in values.items():
            setattr(job, key, value)
        job.updated_at = datetime.now(UTC)
        session.commit()


def _run_import_job(job_id: str, archive_path: str, user: str, account_id: int) -> None:
    path = Path(archive_path)
    _update_import_job(
        job_id,
        status="running",
        progress_message="Reading ratings, matching titles, and building the taste profile…",
    )
    try:
        with path.open("rb") as handle:
            upload = UploadFile(file=handle, filename=path.name)
            job_request = SimpleNamespace(
                state=SimpleNamespace(account_id=account_id, rate_limit_checked=True)
            )
            result = asyncio.run(import_profile(job_request, user, upload))
        _update_import_job(
            job_id,
            status="succeeded",
            progress_message="Profile import completed.",
            result_json=json.dumps(result, default=str),
            completed_at=datetime.now(UTC),
        )
    except HTTPException as error:
        _update_import_job(
            job_id,
            status="failed",
            progress_message="Profile import failed.",
            error_message=str(error.detail),
            completed_at=datetime.now(UTC),
        )
    except Exception as error:
        _update_import_job(
            job_id,
            status="failed",
            progress_message="Profile import failed.",
            error_message=f"{type(error).__name__}: {error}",
            completed_at=datetime.now(UTC),
        )
    finally:
        path.unlink(missing_ok=True)


@app.post("/profiles/import/start", status_code=202)
async def start_profile_import(
    background_tasks: BackgroundTasks,
    request: Request,
    user: Annotated[str, Form()],
    archive: Annotated[UploadFile, File()],
) -> dict:
    """Save an upload briefly and process it after returning a durable job ID."""
    from app.services.recommendation_reports import VALID_USER

    account_id = request.state.account_id
    if account_id is None:
        raise HTTPException(status_code=401, detail="Sign in required")
    decision = profile_import_rate_limiter.consume(
        f"profile-import:{account_id}",
        limit=settings.web_profile_imports_per_hour,
        window_seconds=3600,
    )
    _enforce_rate_limit(
        decision,
        "Too many profile imports. Please wait before uploading another export.",
    )
    if not VALID_USER.fullmatch(user):
        raise HTTPException(status_code=422, detail="Use only letters, numbers, - or _ for profile")
    if not archive.filename or not archive.filename.casefold().endswith(".zip"):
        raise HTTPException(status_code=422, detail="Select a Letterboxd .zip export")
    with SessionLocal() as session:
        existing = session.scalar(select(User).where(User.slug == user))
        if existing is not None and existing.owner_account_id not in {None, account_id}:
            raise HTTPException(
                status_code=409,
                detail="That profile ID is already used by another account",
            )
    job_id = str(uuid4())
    jobs_dir = settings.data_dir / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    target = jobs_dir / f"{job_id}.zip"
    size = 0
    try:
        with target.open("wb") as handle:
            while chunk := await archive.read(1024 * 1024):
                size += len(chunk)
                if size > 100 * 1024 * 1024:
                    raise HTTPException(status_code=413, detail="Export exceeds 100 MB")
                handle.write(chunk)
        with SessionLocal() as session:
            session.add(
                WebJob(
                    id=job_id,
                    account_id=account_id,
                    job_type="profile_import",
                    profile_slug=user,
                    status="queued",
                    progress_message="Upload received; import is queued.",
                )
            )
            session.commit()
    except Exception:
        target.unlink(missing_ok=True)
        raise
    finally:
        await archive.close()
    background_tasks.add_task(_run_import_job, job_id, str(target), user, account_id)
    return {
        "job_id": job_id,
        "status": "queued",
        "progress_message": "Upload received; import is queued.",
    }


@app.get("/jobs/{job_id}")
def web_job(job_id: str, request: Request) -> dict:
    with SessionLocal() as session:
        job = session.scalar(
            select(WebJob).where(
                WebJob.id == job_id,
                WebJob.account_id == request.state.account_id,
            )
        )
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return _job_payload(job)


@app.post("/recommendations/{user}/refresh")
def refresh_recommendations(
    user: str,
    year_min: int | None = Query(default=None, ge=1870, le=2200),
    year_max: int | None = Query(default=None, ge=1870, le=2200),
    runtime_min: int | None = Query(default=None, ge=1, le=600),
    runtime_max: int | None = Query(default=None, ge=1, le=600),
    limit: int = Query(default=20, ge=1, le=100),
    popularity: str = Query(default="all"),
    genre: str | None = Query(default=None, max_length=60),
    media_type: str = Query(default="all", pattern=r"^(all|movie|tv)$"),
    country: str = Query(default="US", pattern=r"^[A-Z]{2}$"),
) -> dict:
    """Rebuild the combined historical/current ranking for one imported profile."""
    try:
        refresh_review_policy(
            user,
            settings.processed_data_dir / "tmdb-rich-details.json",
            settings.processed_data_dir / "review-policies" / f"{user}.json",
        )
        clear_group_recommendation_cache()
        report = generate_recommendations(
            _latest_artifact(settings.ml_artifacts_dir),
            user=user,
            limit=limit,
            scope="all",
            year_min=year_min,
            year_max=year_max,
            runtime_min=runtime_min,
            runtime_max=runtime_max,
            popularity_tier=popularity,
            genre=genre,
            media_type=media_type,
            live_tmdb=False,
            persist=True,
            emit=False,
        )
        return _with_display_metadata(report, country)
    except (ValueError, typer.BadParameter) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except Exception as error:
        # The UI must always receive JSON, including for unexpected local/runtime failures.
        raise HTTPException(
            status_code=500,
            detail=f"Ranking could not be rebuilt: {type(error).__name__}",
        ) from error


@app.get("/movies/search/{user}")
def search_movie_scores(
    user: str,
    q: str = Query(min_length=2, max_length=120),
    year: int | None = Query(default=None, ge=1870, le=2200),
    limit: int = Query(default=10, ge=1, le=25),
    country: str = Query(default="US", pattern=r"^[A-Z]{2}$"),
) -> dict:
    """Search TMDB and score exact title matches for one profile."""
    try:
        tmdb_ids = _tmdb_search_ids(q, year, limit)
        if not tmdb_ids:
            return {
                "user": user,
                "query": q,
                "year": year,
                "matches_scored": 0,
                "results": [],
            }
        report = generate_recommendations(
            _latest_artifact(settings.ml_artifacts_dir),
            user=user,
            limit=limit,
            scope="all",
            candidate_tmdb_ids=",".join(str(value) for value in tmdb_ids),
            include_watched=True,
            live_tmdb=False,
            persist=False,
            emit=False,
        )
        relevance = {tmdb_id: position for position, tmdb_id in enumerate(tmdb_ids)}
        scored = sorted(
            report["recommendations"],
            key=lambda item: relevance.get(int(item["tmdb_id"]), len(relevance)),
        )
        for rank, item in enumerate(scored, start=1):
            item["rank"] = rank
        result = {
            "user": user,
            "query": q,
            "year": year,
            "matches_scored": len(scored),
            "results": scored,
        }
        return _with_display_metadata(result, country)
    except HTTPException:
        raise
    except (ValueError, typer.BadParameter) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Movie lookup could not be completed: {type(error).__name__}",
        ) from error
