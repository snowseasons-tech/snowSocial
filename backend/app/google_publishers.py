from __future__ import annotations

import os

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .connections import ChannelConnection
from .database import SessionLocal
from .google_auth import get_google_access_token
from .publishers import (
    AmbiguousPublishError,
    PermanentPublishError,
    RetryablePublishError,
)


class GoogleBusinessPublisher:
    def is_available(self) -> bool:
        try:
            with SessionLocal() as session:
                self._select_connection(session)
            return True
        except PermanentPublishError:
            return False

    def publish(self, *, caption: str, idempotency_key: str, media_url: str | None = None) -> str:
        with SessionLocal() as session:
            connection = self._select_connection(session)
            try:
                token = get_google_access_token(connection, session)
            except ValueError as error:
                raise PermanentPublishError(
                    "Google access token refresh failed. Reconnect Google Business Profile."
                ) from error
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
            raise AmbiguousPublishError(
                "Google returned an unreadable publish response. Check the Business Profile."
            ) from error
        if not isinstance(post_name, str) or not post_name:
            raise AmbiguousPublishError(
                "Google did not return a Local Post ID. Check the Business Profile before retrying."
            )
        return post_name

    @staticmethod
    def _select_connection(session: Session) -> ChannelConnection:
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
