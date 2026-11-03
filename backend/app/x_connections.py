from __future__ import annotations

import base64
import hashlib
import os
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx
from cryptography.fernet import InvalidToken
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .auth import require_authenticated_user
from .connections import ChannelConnection, OAuthState, _fernet
from .database import get_session
from .models import Channel

router = APIRouter(prefix="/api/connections/x", tags=["connections"], dependencies=[Depends(require_authenticated_user)])
OAUTH_STATE_TTL = timedelta(minutes=10)
SCOPES = "tweet.read tweet.write users.read offline.access"


def _setting(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise HTTPException(status_code=503, detail=f"{name} is not configured")
    return value


def _frontend_redirect(result: str) -> RedirectResponse:
    base = os.getenv("X_FRONTEND_URL", "http://localhost:8080").rstrip("/")
    return RedirectResponse(f"{base}/?connection={result}", status_code=303)


@router.get("/authorize", include_in_schema=False)
def begin_x_oauth(session: Session = Depends(get_session)) -> RedirectResponse:
    client_id = _setting("X_CLIENT_ID")
    client_secret = _setting("X_CLIENT_SECRET")
    redirect_uri = _setting("X_REDIRECT_URI")
    cipher = _fernet()
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(48)
    encrypted_verifier = cipher.encrypt(verifier.encode("ascii")).decode("ascii")
    session.add(OAuthState(
        state_hash=hashlib.sha256(state.encode("utf-8")).hexdigest(),
        provider="x",
        expires_at=datetime.now(UTC) + OAUTH_STATE_TTL,
        encrypted_code_verifier=encrypted_verifier,
    ))
    try:
        session.commit()
    except SQLAlchemyError as error:
        session.rollback()
        raise HTTPException(status_code=503, detail="Could not start X authorization") from error
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": SCOPES,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return RedirectResponse(f"https://x.com/i/oauth2/authorize?{urlencode(params)}", status_code=303)


@router.get("/callback", include_in_schema=False)
def complete_x_oauth(
    code: str | None = Query(default=None, max_length=4096),
    state: str | None = Query(default=None, max_length=256),
    error: str | None = Query(default=None, max_length=128),
    session: Session = Depends(get_session),
) -> RedirectResponse:
    if error or not code or not state:
        return _frontend_redirect("x_error")
    auth_state = session.scalar(select(OAuthState).where(
        OAuthState.state_hash == hashlib.sha256(state.encode("utf-8")).hexdigest(),
        OAuthState.provider == "x",
    ))
    if auth_state is None:
        return _frontend_redirect("x_error")
    expires_at = auth_state.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at <= datetime.now(UTC) or not auth_state.encrypted_code_verifier:
        session.delete(auth_state)
        session.commit()
        return _frontend_redirect("x_error")
    encrypted_verifier = auth_state.encrypted_code_verifier
    session.delete(auth_state)
    try:
        session.commit()
        verifier = _fernet().decrypt(encrypted_verifier.encode("ascii")).decode("ascii")
    except (SQLAlchemyError, InvalidToken, UnicodeDecodeError, ValueError):
        session.rollback()
        return _frontend_redirect("x_error")

    client_id = os.getenv("X_CLIENT_ID", "").strip()
    client_secret = os.getenv("X_CLIENT_SECRET", "").strip()
    redirect_uri = os.getenv("X_REDIRECT_URI", "").strip()
    try:
        token_response = httpx.post(
            "https://api.x.com/2/oauth2/token",
            data={
                "code": code,
                "grant_type": "authorization_code",
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "code_verifier": verifier,
            },
            auth=(client_id, client_secret),
            timeout=httpx.Timeout(20.0, connect=5.0),
        )
        token_response.raise_for_status()
        token_data = token_response.json()
        access_token = token_data["access_token"]
        refresh_token = token_data["refresh_token"]
        user_response = httpx.get(
            "https://api.x.com/2/users/me",
            params={"user.fields": "id,name,username"},
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=httpx.Timeout(20.0, connect=5.0),
        )
        user_response.raise_for_status()
        user_data = user_response.json()["data"]
        account_id = str(user_data["id"])
        account_name = f"@{user_data['username']}"
        expires_seconds = int(token_data.get("expires_in", 7200))
    except (httpx.HTTPError, KeyError, ValueError, TypeError):
        return _frontend_redirect("x_error")

    connection = session.scalar(select(ChannelConnection).where(
        ChannelConnection.provider == "x",
        ChannelConnection.channel == Channel.X.value,
        ChannelConnection.provider_account_id == account_id,
    ))
    if connection is None:
        connection = ChannelConnection(
            provider="x",
            channel=Channel.X.value,
            provider_account_id=account_id,
            account_name=account_name,
            encrypted_access_token="",
        )
        session.add(connection)
    cipher = _fernet()
    connection.account_name = account_name
    connection.encrypted_access_token = cipher.encrypt(access_token.encode("utf-8")).decode("ascii")
    connection.encrypted_refresh_token = cipher.encrypt(refresh_token.encode("utf-8")).decode("ascii")
    connection.token_expires_at = datetime.now(UTC) + timedelta(seconds=expires_seconds)
    try:
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        return _frontend_redirect("x_error")
    return _frontend_redirect("x")
