from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import httpx
from cryptography.fernet import InvalidToken
from sqlalchemy.orm import Session

from .connections import ChannelConnection, _fernet, decrypt_access_token


def get_google_access_token(connection: ChannelConnection, session: Session) -> str:
    """Return a usable Google token, refreshing and persisting it when needed."""
    expiry = connection.token_expires_at
    if expiry is not None and expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=UTC)
    if expiry is None or expiry > datetime.now(UTC) + timedelta(seconds=60):
        return decrypt_access_token(connection)

    if not connection.encrypted_refresh_token:
        raise ValueError("Google refresh token is missing")
    try:
        refresh_token = _fernet().decrypt(connection.encrypted_refresh_token.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeEncodeError, ValueError) as error:
        raise ValueError("Google refresh token could not be decrypted") from error
    client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise ValueError("Google OAuth client credentials are not configured")
    try:
        response = httpx.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=httpx.Timeout(15.0, connect=5.0),
        )
        response.raise_for_status()
        token_data = response.json()
        access_token = token_data["access_token"]
        expires_in = int(token_data.get("expires_in", 3600))
        if not isinstance(access_token, str) or not access_token:
            raise ValueError("Google did not return an access token")
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
        raise ValueError("Google access token refresh failed") from error

    connection.encrypted_access_token = _fernet().encrypt(access_token.encode("utf-8")).decode("ascii")
    connection.token_expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)
    session.commit()
    return access_token
