from __future__ import annotations

import os
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .connections import ChannelConnection, decrypt_access_token
from .database import SessionLocal
from .models import Channel
from .publishers import (
    AmbiguousPublishError,
    PermanentPublishError,
    RetryablePublishError,
)


class LinkedInOrganizationPublisher:
    def is_available(self) -> bool:
        try:
            with SessionLocal() as session:
                connection = self._connection(session)
                decrypt_access_token(connection)
            return True
        except (PermanentPublishError, ValueError):
            return False

    def publish(self, *, caption: str, idempotency_key: str, media_url: str | None = None) -> str:
        if media_url:
            raise PermanentPublishError(
                "LinkedIn currently supports text-only publishing. Remove the image URL or publish to LinkedIn separately."
            )
        with SessionLocal() as session:
            connection = self._connection(session)
            expires_at = connection.token_expires_at
            if expires_at is not None:
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=UTC)
                if expires_at <= datetime.now(UTC):
                    raise PermanentPublishError("The LinkedIn access token expired. Reconnect the organization account.")
            try:
                access_token = decrypt_access_token(connection)
            except ValueError as error:
                raise PermanentPublishError("The LinkedIn access token is unreadable. Reconnect the organization account.") from error
            author_urn = connection.provider_account_id

        version = os.getenv("LINKEDIN_API_VERSION", "202611").strip()
        if len(version) != 6 or not version.isdigit():
            raise PermanentPublishError("LINKEDIN_API_VERSION must use the YYYYMM format.")
        headers = {
            "Authorization": f"Bearer {access_token}",
            "LinkedIn-Version": version,
            "X-Restli-Protocol-Version": "2.0.0",
            "Content-Type": "application/json",
        }
        body = {
            "author": author_urn,
            "commentary": caption,
            "visibility": "PUBLIC",
            "distribution": {
                "feedDistribution": "MAIN_FEED",
                "targetEntities": [],
                "thirdPartyDistributionChannels": [],
            },
            "lifecycleState": "PUBLISHED",
            "isReshareDisabledByAuthor": False,
        }
        try:
            response = httpx.post(
                "https://api.linkedin.com/rest/posts",
                headers=headers,
                json=body,
                timeout=httpx.Timeout(30.0, connect=5.0),
            )
        except (httpx.TimeoutException, httpx.TransportError) as error:
            raise AmbiguousPublishError(
                "LinkedIn may have accepted the post. Check the organization page before retrying."
            ) from error
        if response.status_code == 429:
            raise RetryablePublishError("LinkedIn rate-limited publishing; the post will be retried.")
        if response.status_code == 408 or response.status_code >= 500:
            raise AmbiguousPublishError(
                "LinkedIn may have accepted the post. Check the organization page before retrying."
            )
        if response.status_code >= 400:
            raise PermanentPublishError(f"LinkedIn rejected the organization post (HTTP {response.status_code}).")
        post_id = response.headers.get("x-restli-id")
        if not post_id:
            raise AmbiguousPublishError(
                "LinkedIn did not return a post ID. Check the organization page before retrying."
            )
        return post_id

    @staticmethod
    def _connection(session: Session) -> ChannelConnection:
        organization_id = os.getenv("LINKEDIN_ORGANIZATION_ID", "").strip()
        if not organization_id.isdigit():
            raise PermanentPublishError("Set LINKEDIN_ORGANIZATION_ID to the numeric company page ID.")
        organization_urn = f"urn:li:organization:{organization_id}"
        connections = list(session.scalars(
            select(ChannelConnection).where(
                ChannelConnection.provider == "linkedin",
                ChannelConnection.channel == Channel.LINKEDIN.value,
                ChannelConnection.provider_account_id == organization_urn,
            )
        ).all())
        if len(connections) != 1:
            raise PermanentPublishError(
                "No unique LinkedIn organization connection is available. Connect the configured company page."
            )
        return connections[0]
