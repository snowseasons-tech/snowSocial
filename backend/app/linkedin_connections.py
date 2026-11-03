from __future__ import annotations

import hashlib
import os
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .connections import (
    OAUTH_STATE_TTL,
    ChannelConnection,
    OAuthState,
    _fernet,
    _required_setting,
)
from .database import get_session

router = APIRouter(prefix="/api/connections/linkedin", tags=["connections"])


@router.get("/authorize", include_in_schema=False)
def begin_linkedin_oauth(session: Session = Depends(get_session),  # noqa: B008
) -> RedirectResponse:
    client_id = _required_setting("LINKEDIN_CLIENT_ID")
    redirect_uri = _required_setting("LINKEDIN_REDIRECT_URI")
    _required_setting("LINKEDIN_CLIENT_SECRET")
    _required_setting("LINKEDIN_ORGANIZATION_ID")
    _fernet()
    state = secrets.token_urlsafe(32)
    session.add(OAuthState(
        state_hash=hashlib.sha256(state.encode("utf-8")).hexdigest(),
        provider="linkedin",
        expires_at=datetime.now(UTC) + OAUTH_STATE_TTL,
    ))
    try:
        session.commit()
    except SQLAlchemyError as error:
        session.rollback()
        raise HTTPException(status_code=503, detail="Could not start LinkedIn OAuth") from error
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "scope": "w_organization_social rw_organization_admin",
    }
    return RedirectResponse(
        f"https://www.linkedin.com/oauth/v2/authorization?{urlencode(params)}",
        status_code=302,
    )


@router.get("/callback", include_in_schema=False)
def complete_linkedin_oauth(
    state: str = Query(min_length=20, max_length=256),
    code: str | None = Query(default=None, max_length=4096),
    error: str | None = Query(default=None, max_length=128),
    session: Session = Depends(get_session),  # noqa: B008
) -> RedirectResponse:
    digest = hashlib.sha256(state.encode("utf-8")).hexdigest()
    oauth_state = session.scalar(select(OAuthState).where(OAuthState.state_hash == digest).with_for_update())
    expiry = oauth_state.expires_at if oauth_state else None
    if expiry is not None and expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=UTC)
    if oauth_state is None or oauth_state.provider != "linkedin" or expiry is None or expiry <= datetime.now(UTC):
        raise HTTPException(status_code=400, detail="OAuth state is invalid or expired")
    session.delete(oauth_state)
    session.commit()
    frontend = os.getenv("LINKEDIN_FRONTEND_URL", "http://localhost:8080").rstrip("/")
    if error:
        return RedirectResponse(f"{frontend}/?connection=linkedin_error", status_code=303)
    if not code:
        raise HTTPException(status_code=400, detail="LinkedIn did not return an authorization code")

    client_id = _required_setting("LINKEDIN_CLIENT_ID")
    client_secret = _required_setting("LINKEDIN_CLIENT_SECRET")
    redirect_uri = _required_setting("LINKEDIN_REDIRECT_URI")
    try:
        response = httpx.post(
            "https://www.linkedin.com/oauth/v2/accessToken",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
            },
            timeout=httpx.Timeout(15.0, connect=5.0),
        )
        response.raise_for_status()
        token_data = response.json()
        access_token = token_data["access_token"]
        expires_in = int(token_data["expires_in"])
        if not isinstance(access_token, str) or not access_token:
            raise ValueError("LinkedIn did not return an access token")
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
        raise HTTPException(status_code=502, detail="LinkedIn OAuth token exchange failed") from error

    organization_id = _required_setting("LINKEDIN_ORGANIZATION_ID")
    if not organization_id.isdigit():
        raise HTTPException(status_code=503, detail="LINKEDIN_ORGANIZATION_ID must contain the numeric company page ID")
    organization_urn = f"urn:li:organization:{organization_id}"
    account_name = os.getenv("LINKEDIN_ORGANIZATION_NAME", "").strip() or f"LinkedIn organization {organization_id}"
    connection = session.scalar(select(ChannelConnection).where(
        ChannelConnection.provider == "linkedin",
        ChannelConnection.channel == "linkedin",
        ChannelConnection.provider_account_id == organization_urn,
    ))
    if connection is None:
        connection = ChannelConnection(
            provider="linkedin",
            channel="linkedin",
            provider_account_id=organization_urn,
            account_name=account_name,
            encrypted_access_token="",
        )
        session.add(connection)
    connection.account_name = account_name
    connection.encrypted_access_token = _fernet().encrypt(access_token.encode("utf-8")).decode("ascii")
    connection.token_expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)
    try:
        session.commit()
    except SQLAlchemyError as error:
        session.rollback()
        raise HTTPException(status_code=503, detail="Could not save LinkedIn connection") from error
    return RedirectResponse(f"{frontend}/?connection=linkedin", status_code=303)
