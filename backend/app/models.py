from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class PostStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    SCHEDULED = "scheduled"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Channel(StrEnum):
    FACEBOOK = "facebook"
    INSTAGRAM = "instagram"
    GOOGLE_BUSINESS = "google_business"
    LINKEDIN = "linkedin"


class DeliveryStatus(StrEnum):
    QUEUED = "queued"
    PUBLISHING = "publishing"
    RETRY_WAIT = "retry_wait"
    PUBLISHED = "published"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"


def utc_now() -> datetime:
    return datetime.now(UTC)


class Post(Base):
    __tablename__ = "posts"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    caption: Mapped[str] = mapped_column(Text, nullable=False)
    media_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
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
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    post: Mapped[Post] = relationship(back_populates="deliveries")


class MonthlyPostMetric(Base):
    __tablename__ = "monthly_post_metrics"
    __table_args__ = (UniqueConstraint("delivery_id", "month", name="uq_post_metric_delivery_month"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    delivery_id: Mapped[UUID] = mapped_column(
        ForeignKey("channel_deliveries.id", ondelete="CASCADE"), nullable=False, index=True
    )
    month: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    impressions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reach: Mapped[int | None] = mapped_column(Integer, nullable=True)
    likes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comments: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shares: Mapped[int | None] = mapped_column(Integer, nullable=True)
    saves: Mapped[int | None] = mapped_column(Integer, nullable=True)
    link_clicks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    calls: Mapped[int | None] = mapped_column(Integer, nullable=True)
    direction_requests: Mapped[int | None] = mapped_column(Integer, nullable=True)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)



class MonthlyChannelMetric(Base):
    """Profile-level monthly metrics not attributable to an individual post."""

    __tablename__ = "monthly_channel_metrics"
    __table_args__ = (
        UniqueConstraint("channel", "provider_account_id", "month", name="uq_channel_metric_account_month"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    channel: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    provider_account_id: Mapped[str] = mapped_column(String(255), nullable=False)
    month: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    impressions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reach: Mapped[int | None] = mapped_column(Integer, nullable=True)
    likes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comments: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shares: Mapped[int | None] = mapped_column(Integer, nullable=True)
    saves: Mapped[int | None] = mapped_column(Integer, nullable=True)
    link_clicks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    calls: Mapped[int | None] = mapped_column(Integer, nullable=True)
    direction_requests: Mapped[int | None] = mapped_column(Integer, nullable=True)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
