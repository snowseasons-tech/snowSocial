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


class AmbiguousPublishError(PublisherError):
    """The provider may have accepted a request, so retrying could duplicate a post."""


class ChannelPublisher(Protocol):
    def is_available(self) -> bool:
        """Return whether this adapter has an unambiguous connected destination."""

    def publish(self, *, caption: str, idempotency_key: str) -> str:
        """Publish content and return the provider's post ID."""


# Registered adapters are loaded by the publishing worker.
PUBLISHERS: dict[Channel, ChannelPublisher] = {}
