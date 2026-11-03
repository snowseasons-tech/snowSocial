from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .database import get_session
from .models import AuthSession

router = APIRouter(prefix="/api/auth", tags=["authentication"])
SESSION_COOKIE_NAME = "snowsocial_session"
SESSION_TTL = timedelta(hours=12)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=1024)


def _session_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _admin_credentials() -> tuple[str, str]:
    username = os.getenv("SNOWSOCIAL_ADMIN_USERNAME", "")
    password = os.getenv("SNOWSOCIAL_ADMIN_PASSWORD", "")
    if not username or not password:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Single-admin login is not configured",
        )
    return username, password


def _unauthorized() -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in to SnowSocial")


def require_authenticated_user(
    request: Request,
    session: Session = Depends(get_session),  # noqa: B008
) -> str:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        raise _unauthorized()
    auth_session = session.get(AuthSession, _session_hash(token))
    if auth_session is None:
        raise _unauthorized()
    expires_at = auth_session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at <= datetime.now(UTC):
        session.delete(auth_session)
        session.commit()
        raise _unauthorized()
    username, _ = _admin_credentials()
    return username


@router.post("/login")
def login(
    payload: LoginRequest,
    response: Response,
    session: Session = Depends(get_session),  # noqa: B008
) -> dict[str, str | bool]:
    expected_username, expected_password = _admin_credentials()
    username_matches = hmac.compare_digest(payload.username.encode("utf-8"), expected_username.encode("utf-8"))
    password_matches = hmac.compare_digest(payload.password.encode("utf-8"), expected_password.encode("utf-8"))
    if not username_matches or not password_matches:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Username or password is incorrect")

    token = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    session.add(AuthSession(
        token_hash=_session_hash(token),
        created_at=now,
        expires_at=now + SESSION_TTL,
    ))
    try:
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        raise HTTPException(status_code=503, detail="Could not create sign-in session") from None

    secure_cookie = os.getenv("SNOWSOCIAL_COOKIE_SECURE", "false").strip().lower() in {"1", "true", "yes"}
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=int(SESSION_TTL.total_seconds()),
        httponly=True,
        secure=secure_cookie,
        samesite="lax",
        path="/",
    )
    return {"authenticated": True, "username": expected_username}


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    response: Response,
    session: Session = Depends(get_session),  # noqa: B008
) -> None:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        auth_session = session.get(AuthSession, _session_hash(token))
        if auth_session is not None:
            session.delete(auth_session)
            session.commit()
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        httponly=True,
        secure=os.getenv("SNOWSOCIAL_COOKIE_SECURE", "false").strip().lower() in {"1", "true", "yes"},
        samesite="lax",
        path="/",
    )


@router.get("/me")
def current_user(username: str = Depends(require_authenticated_user)) -> dict[str, str | bool]:
    return {"authenticated": True, "username": username}
