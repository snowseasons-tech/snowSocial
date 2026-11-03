from __future__ import annotations

import os
import time
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

    def publish(self, *, caption: str, idempotency_key: str, media_url: str | None = None) -> str:
        del media_url
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



class InstagramPhotoPublisher:
    """Publish a single public image to a linked Instagram professional account."""

    def is_available(self) -> bool:
        try:
            self._connection()
        except PermanentPublishError:
            return False
        return True

    def publish(self, *, caption: str, idempotency_key: str, media_url: str | None = None) -> str:
        del idempotency_key  # Instagram media publishing does not accept an idempotency key.
        if not media_url:
            raise PermanentPublishError("Instagram photo posts require a public image URL")
        if not caption.strip():
            raise PermanentPublishError("Instagram photo posts require a non-empty caption")
        account, access_token = self._connection()
        api_version = os.getenv("META_GRAPH_API_VERSION", "v26.0").strip()
        account_id = quote(account.provider_account_id, safe="")
        base_url = f"https://graph.facebook.com/{api_version}/{account_id}"
        headers = {"Authorization": f"Bearer {access_token}"}
        timeout = httpx.Timeout(30.0, connect=5.0)

        try:
            response = httpx.post(
                f"{base_url}/media",
                headers=headers,
                data={"image_url": media_url, "caption": caption},
                timeout=timeout,
            )
        except (httpx.TimeoutException, httpx.TransportError) as error:
            raise RetryablePublishError(
                "Instagram did not confirm media-container creation; no post was published, so it is safe to retry."
            ) from error
        self._raise_for_response(response, publish_request=False)
        try:
            container_id = response.json().get("id")
        except (ValueError, AttributeError) as error:
            raise RetryablePublishError("Instagram returned an unreadable media-container response") from error
        if not isinstance(container_id, str) or not container_id:
            raise RetryablePublishError("Instagram did not return a media-container ID")

        container_url = f"https://graph.facebook.com/{api_version}/{quote(container_id, safe='')}"
        for attempt in range(15):
            try:
                status_response = httpx.get(
                    container_url,
                    headers=headers,
                    params={"fields": "status_code"},
                    timeout=timeout,
                )
            except (httpx.TimeoutException, httpx.TransportError) as error:
                raise RetryablePublishError(
                    "Instagram media processing could not be confirmed; no post was published, so it is safe to retry."
                ) from error
            self._raise_for_response(status_response, publish_request=False)
            try:
                container_status = status_response.json().get("status_code")
            except (ValueError, AttributeError) as error:
                raise RetryablePublishError("Instagram returned an unreadable media-processing status") from error
            if container_status == "FINISHED":
                break
            if container_status == "ERROR":
                raise PermanentPublishError("Instagram could not process the supplied image URL")
            if container_status == "EXPIRED":
                raise RetryablePublishError("Instagram media container expired before publishing; it can be recreated")
            if attempt < 14:
                time.sleep(2)
        else:
            raise RetryablePublishError("Instagram media is still processing; no post was published, so it is safe to retry")

        try:
            publish_response = httpx.post(
                f"{base_url}/media_publish",
                headers=headers,
                data={"creation_id": container_id},
                timeout=timeout,
            )
        except (httpx.TimeoutException, httpx.TransportError) as error:
            raise AmbiguousPublishError(
                "Instagram did not confirm whether the photo was published. Check the account before retrying."
            ) from error
        self._raise_for_response(publish_response, publish_request=True)
        try:
            post_id = publish_response.json().get("id")
        except (ValueError, AttributeError) as error:
            raise AmbiguousPublishError(
                "Instagram returned an unreadable publish response. Check the account before retrying."
            ) from error
        if not isinstance(post_id, str) or not post_id:
            raise AmbiguousPublishError(
                "Instagram did not return a media ID. Check the account before retrying."
            )
        return post_id

    @staticmethod
    def _connection() -> tuple[ChannelConnection, str]:
        selected_account_id = os.getenv("META_INSTAGRAM_ACCOUNT_ID", "").strip()
        statement = select(ChannelConnection).where(
            ChannelConnection.provider == "meta",
            ChannelConnection.channel == "instagram",
        )
        if selected_account_id:
            statement = statement.where(ChannelConnection.provider_account_id == selected_account_id)
        with SessionLocal() as session:
            connections = list(session.scalars(statement).all())
            if not connections:
                raise PermanentPublishError(
                    "No Instagram professional account is connected. Connect it and check META_INSTAGRAM_ACCOUNT_ID."
                )
            if len(connections) != 1:
                raise PermanentPublishError(
                    "More than one Instagram account is connected. Set META_INSTAGRAM_ACCOUNT_ID to select the business account."
                )
            connection = connections[0]
            expires_at = connection.token_expires_at
            if expires_at is not None:
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=UTC)
                if expires_at <= datetime.now(UTC):
                    raise PermanentPublishError("The Instagram access token has expired. Reconnect the Meta account.")
            try:
                token = decrypt_access_token(connection)
            except ValueError as error:
                raise PermanentPublishError("The Instagram access token is unreadable. Reconnect the Meta account.") from error
            return connection, token

    @staticmethod
    def _raise_for_response(response: httpx.Response, *, publish_request: bool) -> None:
        if response.status_code == 429:
            raise RetryablePublishError("Meta rate-limited Instagram publishing; it will be retried.")
        if response.status_code in (408,) or response.status_code >= 500:
            message = (
                "Meta may have accepted the Instagram publish request. Check the account before retrying."
                if publish_request
                else "Meta could not complete Instagram media processing; no post was published, so it is safe to retry."
            )
            if publish_request:
                raise AmbiguousPublishError(message)
            raise RetryablePublishError(message)
        if response.status_code >= 400:
            detail = FacebookPagePublisher._provider_error(response)
            raise PermanentPublishError(f"Meta rejected the Instagram photo: {detail}")
