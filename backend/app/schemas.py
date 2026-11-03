from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

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


class PostUpdate(BaseModel):
    caption: str | None = Field(default=None, min_length=1, max_length=5000)
    channels: list[Channel] | None = Field(default=None, min_length=1, max_length=4)


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


class PostRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    caption: str
    channels: list[str]
    status: PostStatus
    scheduled_at: datetime | None
    created_at: datetime
    updated_at: datetime
    approved_at: datetime | None
    deliveries: list[DeliveryRead]
