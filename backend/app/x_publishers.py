from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import httpx
from cryptography.fernet import InvalidToken
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .connections import ChannelConnection, _fernet, decrypt_access_token
from .database import SessionLocal
from .models import Channel
from .publishers import (
    AmbiguousPublishError,
    PermanentPublishError,
    RetryablePublishError,
)


class XUserPublisher:
    def is_available(self) -> bool:
        try:
            with SessionLocal() as session:
                connection = self._connection(session)
                _fernet().decrypt(connection.encrypted_refresh_token.encode("ascii"))
            return True
        except (PermanentPublishError, AttributeError, InvalidToken, ValueError):
            return False

    def publish(self, *, caption: str, idempotency_key: str, media_url: str | None = None) -> str:
        if media_url:
            raise PermanentPublishError("X text publishing cannot include an image URL in this release.")
        with SessionLocal() as session:
            connection = self._connection(session)
            access_token = self._get_access_token(connection, session)
        try:
            response = httpx.post(
                "https://api.x.com/2/tweets",
                headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
                json={"text": caption},
                timeout=httpx.Timeout(30.0, connect=5.0),
            )
        except (httpx.TimeoutException, httpx.TransportError) as error:
            raise AmbiguousPublishError("X may have accepted the post. Check the account before retrying.") from error
        if response.status_code == 429:
            raise RetryablePublishError("X rate-limited publishing; the post will be retried.")
        if response.status_code == 408 or response.status_code >= 500:
            raise AmbiguousPublishError("X may have accepted the post. Check the account before retrying.")
        if response.status_code >= 400:
            raise PermanentPublishError(f"X rejected the post (HTTP {response.status_code}).")
        try:
            return str(response.json()["data"]["id"])
        except (ValueError, KeyError, TypeError) as error:
            raise AmbiguousPublishError("X did not return a post ID. Check the account before retrying.") from error

    @staticmethod
    def _connection(session: Session) -> ChannelConnection:
        connections = list(session.scalars(select(ChannelConnection).where(
            ChannelConnection.provider == "x",
            ChannelConnection.channel == Channel.X.value,
        )).all())
        if len(connections) != 1:
            raise PermanentPublishError("Connect exactly one X account before scheduling X posts.")
        return connections[0]

    @staticmethod
    def _get_access_token(connection: ChannelConnection, session: Session) -> str:
        try:
            access_token = decrypt_access_token(connection)
        except ValueError as error:
            raise PermanentPublishError("The X access token is unreadable. Reconnect the account.") from error
        expires_at = connection.token_expires_at
        if expires_at is not None:
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            if expires_at > datetime.now(UTC) + timedelta(minutes=1):
                return access_token
        if not connection.encrypted_refresh_token:
            raise PermanentPublishError("The X token expired. Reconnect the account.")
        try:
            refresh_token = _fernet().decrypt(connection.encrypted_refresh_token.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError, ValueError) as error:
            raise PermanentPublishError("The X refresh token is unreadable. Reconnect the account.") from error
        client_id = os.getenv("X_CLIENT_ID", "").strip()
        client_secret = os.getenv("X_CLIENT_SECRET", "").strip()
        if not client_id or not client_secret:
            raise PermanentPublishError("X OAuth client credentials are not configured.")
        try:
            response = httpx.post(
                "https://api.x.com/2/oauth2/token",
                data={"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": client_id},
                auth=(client_id, client_secret),
                timeout=httpx.Timeout(20.0, connect=5.0),
            )
        except httpx.HTTPError as error:
            raise RetryablePublishError("Could not refresh the X token; the post will be retried.") from error
        if response.status_code == 429 or response.status_code >= 500:
            raise RetryablePublishError("X token refresh is temporarily unavailable.")
        if response.status_code >= 400:
            raise PermanentPublishError("X token refresh failed. Reconnect the account.")
        try:
            token_data = response.json()
            access_token = token_data["access_token"]
            new_refresh_token = token_data.get("refresh_token", refresh_token)
            expires_seconds = int(token_data.get("expires_in", 7200))
        except (ValueError, KeyError, TypeError) as error:
            raise PermanentPublishError("X returned an invalid refresh-token response.") from error
        cipher = _fernet()
        connection.encrypted_access_token = cipher.encrypt(access_token.encode("utf-8")).decode("ascii")
        connection.encrypted_refresh_token = cipher.encrypt(new_refresh_token.encode("utf-8")).decode("ascii")
        connection.token_expires_at = datetime.now(UTC) + timedelta(seconds=expires_seconds)
        try:
            session.commit()
        except SQLAlchemyError as error:
            session.rollback()
            raise PermanentPublishError("Could not save refreshed X tokens. Reconnect the account.") from error
        return access_token
