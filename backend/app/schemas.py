from datetime import datetime
from ipaddress import ip_address
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import Channel, DeliveryStatus, PostStatus


class PostCreate(BaseModel):
    caption: str = Field(min_length=1, max_length=5000)
    media_url: str | None = Field(default=None, max_length=2048)
    channels: list[Channel] = Field(min_length=1, max_length=4)

    @field_validator("caption")
    @classmethod
    def caption_must_not_be_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Caption must contain non-whitespace text")
        return cleaned

    @field_validator("media_url")
    @classmethod
    def media_url_must_be_public_https(cls, value: str | None) -> str | None:
        return validate_public_media_url(value)

    @field_validator("channels")
    @classmethod
    def channels_must_be_unique(cls, value: list[Channel]) -> list[Channel]:
        if len(set(value)) != len(value):
            raise ValueError("Each channel can only be selected once")
        return value

    @model_validator(mode="after")
    def caption_must_fit_selected_channels(self) -> "PostCreate":
        validate_caption_for_channels(self.caption, self.channels)
        return self


class PostUpdate(BaseModel):
    caption: str | None = Field(default=None, min_length=1, max_length=5000)
    media_url: str | None = Field(default=None, max_length=2048)
    channels: list[Channel] | None = Field(default=None, min_length=1, max_length=4)

    @field_validator("media_url")
    @classmethod
    def media_url_must_be_public_https(cls, value: str | None) -> str | None:
        return validate_public_media_url(value)


def validate_public_media_url(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    parsed = urlsplit(cleaned)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https" or not hostname or not parsed.path or parsed.username or parsed.password:
        raise ValueError("Instagram image URL must be an HTTPS URL to a public image")
    if hostname == "localhost" or hostname.endswith((".localhost", ".local", ".internal")):
        raise ValueError("Instagram image URL must point to a public host")
    try:
        ip_address(hostname)
    except ValueError:
        pass
    else:
        raise ValueError("Instagram image URL must use a public domain name, not an IP address")
    return cleaned


def validate_caption_for_channels(caption: str, channels: list[Channel]) -> None:
    if Channel.INSTAGRAM in channels and len(caption) > 2200:
        raise ValueError("Instagram captions cannot exceed 2,200 characters")


class ScheduleRequest(BaseModel):
    scheduled_at: datetime


class DeliveryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    channel: Channel
    status: DeliveryStatus
    attempt_count: int
    platform_post_id: str | None
    error_message: str | None
    published_at: datetime | None
    last_attempt_at: datetime | None
    next_attempt_at: datetime | None


class PostRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    caption: str
    media_url: str | None
    channels: list[str]
    status: PostStatus
    scheduled_at: datetime | None
    created_at: datetime
    updated_at: datetime
    approved_at: datetime | None
    deliveries: list[DeliveryRead]
