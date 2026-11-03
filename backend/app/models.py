from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import JSON, DateTime, Enum as SqlEnum, ForeignKey, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class PostStatus(str, Enum):
    DRAFT = "draft"
    APPROVED = "approved"
    SCHEDULED = "scheduled"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Channel(str, Enum):
    FACEBOOK = "facebook"
    INSTAGRAM = "instagram"
    GOOGLE_BUSINESS = "google_business"
    LINKEDIN = "linkedin"


class DeliveryStatus(str, Enum):
    QUEUED = "queued"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Post(Base):
    __tablename__ = "posts"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    caption: Mapped[str] = mapped_column(Text, nullable=False)
    channels: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    status: Mapped[PostStatus] = mapped_column(
        SqlEnum(PostStatus, values_callable=lambda values: [item.value for item in values]),
        nullable=False,
        default=PostStatus.DRAFT,
        index=True,
    )
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deliveries: Mapped[list[ChannelDelivery]] = relationship(
        back_populates="post", cascade="all, delete-orphan", order_by="ChannelDelivery.channel"
    )


class ChannelDelivery(Base):
    __tablename__ = "channel_deliveries"
    __table_args__ = (UniqueConstraint("post_id", "channel", name="uq_delivery_post_channel"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    post_id: Mapped[UUID] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"), nullable=False, index=True)
    channel: Mapped[Channel] = mapped_column(
        SqlEnum(Channel, values_callable=lambda values: [item.value for item in values]), nullable=False
    )
    status: Mapped[DeliveryStatus] = mapped_column(
        SqlEnum(DeliveryStatus, values_callable=lambda values: [item.value for item in values]),
        nullable=False,
        default=DeliveryStatus.QUEUED,
        index=True,
    )
    attempt_count: Mapped[int] = mapped_column(nullable=False, default=0)
    platform_post_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    post: Mapped[Post] = relationship(back_populates="deliveries")
