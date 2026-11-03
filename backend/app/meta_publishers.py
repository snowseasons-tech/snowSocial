from __future__ import annotations

import os
from datetime import UTC, datetime
from urllib.parse import quote

import httpx
from sqlalchemy import select

from .connections import ChannelConnection, decrypt_access_token
from .database import SessionLocal
from .publishers import (
    AmbiguousPublishError,
    PermanentPublishError,
    RetryablePublishError,
)


class FacebookPagePublisher:
    """Publish text posts to one explicitly configured or uniquely connected Facebook Page."""

    @staticmethod
    def is_available() -> bool:
        try:
            FacebookPagePublisher._connection()
        except PermanentPublishError:
            return False
        return True

    def publish(self, *, caption: str, idempotency_key: str) -> str:
        del idempotency_key  # Meta's Page feed endpoint does not accept an idempotency key.
        account, access_token = self._connection()
        if not caption.strip():
            raise PermanentPublishError("Facebook Page posts require non-empty text")

        api_version = os.getenv("META_GRAPH_API_VERSION", "v26.0").strip()
        page_id = quote(account.provider_account_id, safe="")
        url = f"https://graph.facebook.com/{api_version}/{page_id}/feed"
        try:
            response = httpx.post(
                url,
                headers={"Authorization": f"Bearer {access_token}"},
                data={"message": caption},
                timeout=httpx.Timeout(30.0, connect=5.0),
            )
        except httpx.TimeoutException as error:
            raise AmbiguousPublishError(
                "Meta did not confirm whether Facebook accepted the post. Check the Page before retrying."
            ) from error
        except httpx.TransportError as error:
            raise AmbiguousPublishError(
                "The Meta connection ended before the post result was confirmed. Check the Page before retrying."
            ) from error

        if response.status_code == 429:
            raise RetryablePublishError("Meta rate-limited the Facebook Page post; it will be retried.")
        if response.status_code == 408:
            raise AmbiguousPublishError(
                "Meta timed out the Facebook Page request. Check the Page before retrying."
            )
        if response.status_code >= 500:
            raise AmbiguousPublishError(
                "Meta returned a server error after the publish request. Check the Page before retrying."
            )
        if response.status_code >= 400:
            detail = self._provider_error(response)
            raise PermanentPublishError(f"Meta rejected the Facebook Page post: {detail}")

        try:
            post_id = response.json().get("id")
        except (ValueError, AttributeError) as error:
            raise AmbiguousPublishError(
                "Meta returned an unreadable publish response. Check the Page before retrying."
            ) from error
        if not isinstance(post_id, str) or not post_id:
            raise AmbiguousPublishError(
                "Meta did not return a Facebook post ID. Check the Page before retrying."
            )
        return post_id

    @staticmethod
    def _connection() -> tuple[ChannelConnection, str]:
        selected_page_id = os.getenv("META_FACEBOOK_PAGE_ID", "").strip()
        statement = select(ChannelConnection).where(
            ChannelConnection.provider == "meta",
            ChannelConnection.channel == "facebook",
        )
        if selected_page_id:
            statement = statement.where(ChannelConnection.provider_account_id == selected_page_id)
        with SessionLocal() as session:
            connections = list(session.scalars(statement).all())
            if not connections:
                raise PermanentPublishError(
                    "No Facebook Page is connected. Connect the Page and check META_FACEBOOK_PAGE_ID."
                )
            if len(connections) != 1:
                raise PermanentPublishError(
                    "More than one Facebook Page is connected. Set META_FACEBOOK_PAGE_ID to select the business Page."
                )
            connection = connections[0]
            expires_at = connection.token_expires_at
            if expires_at is not None:
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=UTC)
                if expires_at <= datetime.now(UTC):
                    raise PermanentPublishError("The Facebook Page token has expired. Reconnect the Meta account.")
            try:
                token = decrypt_access_token(connection)
            except ValueError as error:
                raise PermanentPublishError("The Facebook Page token is unreadable. Reconnect the Meta account.") from error
            return connection, token

    @staticmethod
    def _provider_error(response: httpx.Response) -> str:
        try:
            error = response.json().get("error", {})
        except (ValueError, AttributeError):
            return f"HTTP {response.status_code}"
        message = error.get("message") if isinstance(error, dict) else None
        return str(message)[:300] if message else f"HTTP {response.status_code}"
