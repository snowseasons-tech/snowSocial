from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import Channel, DeliveryStatus, PostStatus


class PostCreate(BaseModel):
    caption: str = Field(min_length=1, max_length=5000)
    channels: list[Channel] = Field(min_length=1, max_length=4)

    @field_validator("caption")
    @classmethod
    def caption_must_not_be_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Caption must contain non-whitespace text")
        return cleaned

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
    channels: list[Channel] | None = Field(default=None, min_length=1, max_length=4)


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
