from typing import Protocol

from .models import Channel


class PublisherError(Exception):
    """A publishing error safe to store on a delivery record."""

    def __init__(self, message: str):
        super().__init__(message[:1000])


class RetryablePublishError(PublisherError):
    """A temporary provider failure that may succeed on a later attempt."""


class PermanentPublishError(PublisherError):
    """A content, permission, or configuration failure requiring intervention."""


class ChannelPublisher(Protocol):
    def publish(self, *, caption: str, idempotency_key: str) -> str:
        """Publish content and return the provider's post ID."""


# Channel adapters will register here in the OAuth integration stage.
PUBLISHERS: dict[Channel, ChannelPublisher] = {}
