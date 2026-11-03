from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select

from .connections import ChannelConnection, _fernet, decrypt_access_token
from .database import SessionLocal
from .models import Channel
from .publishers import AmbiguousPublishError, PermanentPublishError, RetryablePublishError


class GoogleBusinessPublisher:
    def is_available(self) -> bool:
        try:
            self._connection_id()
            return True
        except PermanentPublishError:
            return False

    def publish(self, *, caption: str, idempotency_key: str, media_url: str | None = None) -> str:
        with SessionLocal() as session:
            connection = self._select_connection(session)
            token = self._access_token(connection, session)
            parent = connection.provider_account_id
        body: dict[str, object] = {
            "languageCode": os.getenv("GOOGLE_BUSINESS_LANGUAGE_CODE", "en-US"),
            "summary": caption,
            "topicType": "STANDARD",
        }
        if media_url:
            body["media"] = [{"mediaFormat": "PHOTO", "sourceUrl": media_url}]
        try:
            response = httpx.post(
                f"https://mybusiness.googleapis.com/v4/{parent}/localPosts",
                headers={"Authorization": f"Bearer {token}"},
                json=body,
                timeout=httpx.Timeout(30.0, connect=5.0),
            )
        except (httpx.TimeoutException, httpx.TransportError) as error:
            raise AmbiguousPublishError(
                "Google may have accepted the Local Post. Check the Business Profile before retrying."
            ) from error
        if response.status_code == 429:
            raise RetryablePublishError("Google rate-limited Local Post publishing; it will be retried.")
        if response.status_code == 408 or response.status_code >= 500:
            raise AmbiguousPublishError(
                "Google may have accepted the Local Post. Check the Business Profile before retrying."
            )
        if response.status_code >= 400:
            raise PermanentPublishError(f"Google rejected the Local Post (HTTP {response.status_code}).")
        try:
            post_name = response.json().get("name")
        except (ValueError, AttributeError) as error:
            raise AmbiguousPublishError("Google returned an unreadable publish response. Check the Business Profile.") from error
        if not isinstance(post_name, str) or not post_name:
            raise AmbiguousPublishError("Google did not return a Local Post ID. Check the Business Profile before retrying.")
        return post_name

    @staticmethod
    def _connection_id() -> str:
        with SessionLocal() as session:
            GoogleBusinessPublisher._select_connection(session)
        return "connected"

    @staticmethod
    def _select_connection(session):
        location_id = os.getenv("GOOGLE_BUSINESS_LOCATION_ID", "").strip()
        statement = select(ChannelConnection).where(
            ChannelConnection.provider == "google",
            ChannelConnection.channel == "google_business",
        )
        if location_id:
            statement = statement.where(ChannelConnection.provider_account_id == location_id)
        connections = list(session.scalars(statement).all())
        if not connections:
            raise PermanentPublishError(
                "No Google Business Profile location is connected. Connect a location and check GOOGLE_BUSINESS_LOCATION_ID."
            )
        if len(connections) != 1:
            raise PermanentPublishError(
                "More than one Google Business Profile location is connected. Set GOOGLE_BUSINESS_LOCATION_ID to select one."
            )
        return connections[0]

    @staticmethod
    def _access_token(connection: ChannelConnection, session) -> str:
        expiry = connection.token_expires_at
        if expiry is not None and expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=UTC)
        if expiry is None or expiry > datetime.now(UTC) + timedelta(seconds=60):
            try:
                return decrypt_access_token(connection)
            except ValueError as error:
                raise PermanentPublishError("The Google access token is unreadable. Reconnect Google Business Profile.") from error
        encrypted_refresh = connection.encrypted_refresh_token
        if not encrypted_refresh:
            raise PermanentPublishError("The Google refresh token is missing. Reconnect Google Business Profile.")
        try:
            refresh_token = _fernet().decrypt(encrypted_refresh.encode("ascii")).decode("utf-8")
        except Exception as error:
            raise PermanentPublishError("The Google refresh token is unreadable. Reconnect Google Business Profile.") from error
        client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
        client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
        if not client_id or not client_secret:
            raise PermanentPublishError("Google OAuth client credentials are not configured.")
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
                raise ValueError("missing access token")
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
            raise PermanentPublishError("Google access token refresh failed. Reconnect Google Business Profile.") from error
        connection.encrypted_access_token = _fernet().encrypt(access_token.encode()).decode("ascii")
        connection.token_expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)
        session.commit()
        return access_token
