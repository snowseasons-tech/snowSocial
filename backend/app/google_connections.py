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
from .auth import require_authenticated_user
from .database import get_session

router = APIRouter(prefix="/api/connections/google", tags=["connections"], dependencies=[Depends(require_authenticated_user)])


def _request_google_locations(access_token: str) -> list[dict[str, str]]:
    headers = {"Authorization": f"Bearer {access_token}"}
    timeout = httpx.Timeout(20.0, connect=5.0)
    locations: list[dict[str, str]] = []
    try:
        with httpx.Client(timeout=timeout) as client:
            accounts_response = client.get(
                "https://mybusinessaccountmanagement.googleapis.com/v1/accounts",
                headers=headers,
            )
            accounts_response.raise_for_status()
            accounts = accounts_response.json().get("accounts", [])
            for account in accounts:
                account_name = account.get("name")
                if not isinstance(account_name, str) or not account_name.startswith("accounts/"):
                    continue
                page_token = None
                while True:
                    params = {"readMask": "name,title,storeCode", "pageSize": 100}
                    if page_token:
                        params["pageToken"] = page_token
                    response = client.get(
                        f"https://mybusinessbusinessinformation.googleapis.com/v1/{account_name}/locations",
                        params=params,
                        headers=headers,
                    )
                    response.raise_for_status()
                    payload = response.json()
                    for location in payload.get("locations", []):
                        resource = location.get("name")
                        title = location.get("title")
                        if isinstance(resource, str) and resource.startswith("locations/") and isinstance(title, str):
                            locations.append({
                                "resource_name": f"{account_name}/{resource}",
                                "account_name": title,
                            })
                    page_token = payload.get("nextPageToken")
                    if not page_token:
                        break
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
        raise HTTPException(status_code=502, detail="Google Business Profile location discovery failed") from error
    return locations


@router.get("/authorize", include_in_schema=False)
def begin_google_oauth(session: Session = Depends(get_session),  # noqa: B008
) -> RedirectResponse:
    client_id = _required_setting("GOOGLE_CLIENT_ID")
    redirect_uri = _required_setting("GOOGLE_REDIRECT_URI")
    _required_setting("GOOGLE_CLIENT_SECRET")
    _fernet()
    state = secrets.token_urlsafe(32)
    session.add(OAuthState(
        state_hash=hashlib.sha256(state.encode("utf-8")).hexdigest(),
        provider="google",
        expires_at=datetime.now(UTC) + OAUTH_STATE_TTL,
    ))
    try:
        session.commit()
    except SQLAlchemyError as error:
        session.rollback()
        raise HTTPException(status_code=503, detail="Could not start Google OAuth") from error
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "https://www.googleapis.com/auth/business.manage",
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return RedirectResponse(f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}", status_code=302)


@router.get("/callback", include_in_schema=False)
def complete_google_oauth(
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
    if oauth_state is None or oauth_state.provider != "google" or expiry is None or expiry <= datetime.now(UTC):
        raise HTTPException(status_code=400, detail="OAuth state is invalid or expired")
    session.delete(oauth_state)
    session.commit()
    frontend = os.getenv("GOOGLE_FRONTEND_URL", "http://localhost:8080").rstrip("/")
    if error:
        return RedirectResponse(f"{frontend}/?connection=google_error", status_code=303)
    if not code:
        raise HTTPException(status_code=400, detail="Google did not return an authorization code")

    client_id = _required_setting("GOOGLE_CLIENT_ID")
    client_secret = _required_setting("GOOGLE_CLIENT_SECRET")
    redirect_uri = _required_setting("GOOGLE_REDIRECT_URI")
    try:
        with httpx.Client(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
            response = client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "code": code,
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
            )
            response.raise_for_status()
            tokens = response.json()
    except (httpx.HTTPError, ValueError) as error:
        raise HTTPException(status_code=502, detail="Google OAuth token exchange failed") from error
    access_token = tokens.get("access_token")
    refresh_token = tokens.get("refresh_token")
    if not isinstance(access_token, str) or not access_token:
        raise HTTPException(status_code=502, detail="Google did not return an access token")
    locations = _request_google_locations(access_token)
    if not locations:
        raise HTTPException(status_code=422, detail="No Google Business Profile locations were found")
    cipher = _fernet()
    token_expiry = datetime.now(UTC) + timedelta(seconds=int(tokens.get("expires_in", 3600)))
    for location in locations:
        connection = session.scalar(select(ChannelConnection).where(
            ChannelConnection.provider == "google",
            ChannelConnection.channel == "google_business",
            ChannelConnection.provider_account_id == location["resource_name"],
        ))
        if connection is None:
            connection = ChannelConnection(
                provider="google",
                channel="google_business",
                provider_account_id=location["resource_name"],
                account_name=location["account_name"],
                encrypted_access_token=cipher.encrypt(access_token.encode()).decode("ascii"),
            )
            session.add(connection)
        connection.account_name = location["account_name"]
        connection.encrypted_access_token = cipher.encrypt(access_token.encode()).decode("ascii")
        connection.token_expires_at = token_expiry
        if refresh_token:
            connection.encrypted_refresh_token = cipher.encrypt(refresh_token.encode()).decode("ascii")
        elif connection.encrypted_refresh_token is None:
            raise HTTPException(status_code=422, detail="Google did not issue a refresh token; reconnect with consent enabled")
    try:
        session.commit()
    except SQLAlchemyError as error:
        session.rollback()
        raise HTTPException(status_code=503, detail="Could not save Google Business Profile connections") from error
    return RedirectResponse(f"{frontend}/?connection=google", status_code=303)
