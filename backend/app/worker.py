from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .database import SessionLocal, settings
from .meta_publishers import FacebookPagePublisher
from .models import Channel, ChannelDelivery, DeliveryStatus, Post, PostStatus
from .publishers import (
    PUBLISHERS,
    AmbiguousPublishError,
    PermanentPublishError,
    RetryablePublishError,
)

logger = logging.getLogger("snowsocial.worker")
TERMINAL_DELIVERY_STATUSES = {DeliveryStatus.PUBLISHED, DeliveryStatus.FAILED, DeliveryStatus.NEEDS_REVIEW}
PUBLISHERS[Channel.FACEBOOK] = FacebookPagePublisher()
PUBLISHERS[Channel.INSTAGRAM] = InstagramPhotoPublisher()


@dataclass(frozen=True)
class DeliveryJob:
    delivery_id: UUID
    post_id: UUID
    channel: Channel
    caption: str
    attempt_count: int


def utc_now() -> datetime:
    return datetime.now(UTC)


def reconcile_post_status(session: Session, post_id: UUID) -> None:
    post = session.scalar(select(Post).where(Post.id == post_id).with_for_update())
    if post is None:
        return
    deliveries = list(session.scalars(select(ChannelDelivery).where(ChannelDelivery.post_id == post_id)).all())
    if not deliveries:
        return
    statuses = {delivery.status for delivery in deliveries}
    if statuses <= TERMINAL_DELIVERY_STATUSES:
        post.status = PostStatus.PUBLISHED if statuses == {DeliveryStatus.PUBLISHED} else PostStatus.FAILED
    elif DeliveryStatus.PUBLISHING in statuses:
        post.status = PostStatus.PUBLISHING
    else:
        post.status = PostStatus.SCHEDULED


def move_expired_claims_to_review(session: Session, now: datetime) -> int:
    expired_before = now - timedelta(seconds=settings.claim_timeout_seconds)
    statement = (
        select(ChannelDelivery)
        .where(
            ChannelDelivery.status == DeliveryStatus.PUBLISHING,
            ChannelDelivery.claimed_at.is_not(None),
            ChannelDelivery.claimed_at <= expired_before,
        )
        .order_by(ChannelDelivery.claimed_at)
        .limit(settings.worker_batch_size)
        .with_for_update(skip_locked=True, of=ChannelDelivery)
    )
    expired = list(session.scalars(statement).all())
    affected_posts: set[UUID] = set()
    for delivery in expired:
        delivery.status = DeliveryStatus.NEEDS_REVIEW
        delivery.error_message = (
            "Worker stopped during a publish attempt. Confirm the post's channel status before retrying."
        )
        delivery.claimed_at = None
        delivery.next_attempt_at = None
        affected_posts.add(delivery.post_id)
    for post_id in affected_posts:
        reconcile_post_status(session, post_id)
    if expired:
        session.commit()
    return len(expired)


def claim_due_deliveries(session: Session, now: datetime) -> list[DeliveryJob]:
    configured_channels = [channel for channel, publisher in PUBLISHERS.items() if publisher.is_available()]
    if not configured_channels:
        return []
    statement = (
        select(ChannelDelivery)
        .join(Post, Post.id == ChannelDelivery.post_id)
        .where(
            ChannelDelivery.status.in_([DeliveryStatus.QUEUED, DeliveryStatus.RETRY_WAIT]),
            ChannelDelivery.channel.in_(configured_channels),
            Post.status.in_([PostStatus.SCHEDULED, PostStatus.PUBLISHING]),
            Post.scheduled_at.is_not(None),
            Post.scheduled_at <= now,
            or_(ChannelDelivery.next_attempt_at.is_(None), ChannelDelivery.next_attempt_at <= now),
        )
        .order_by(Post.scheduled_at, ChannelDelivery.created_at)
        .limit(settings.worker_batch_size)
        .with_for_update(skip_locked=True, of=ChannelDelivery)
    )
    deliveries = list(session.scalars(statement).all())
    jobs: list[DeliveryJob] = []
    for delivery in deliveries:
        delivery.status = DeliveryStatus.PUBLISHING
        delivery.attempt_count += 1
        delivery.last_attempt_at = now
        delivery.claimed_at = now
        delivery.next_attempt_at = None
        delivery.error_message = None
        delivery.post.status = PostStatus.PUBLISHING
        jobs.append(
            DeliveryJob(
                delivery_id=delivery.id,
                post_id=delivery.post_id,
                channel=delivery.channel,
                caption=delivery.post.caption,
                attempt_count=delivery.attempt_count,
            )
        )
    if jobs:
        session.commit()
    return jobs


def complete_delivery(job: DeliveryJob, platform_post_id: str) -> None:
    with SessionLocal() as session:
        delivery = session.scalar(
            select(ChannelDelivery).where(ChannelDelivery.id == job.delivery_id).with_for_update()
        )
        if delivery is None or delivery.status is not DeliveryStatus.PUBLISHING:
            logger.warning("Delivery %s changed state before completion", job.delivery_id)
            return
        delivery.status = DeliveryStatus.PUBLISHED
        delivery.platform_post_id = platform_post_id[:255]
        delivery.published_at = utc_now()
        delivery.claimed_at = None
        delivery.next_attempt_at = None
        delivery.error_message = None
        reconcile_post_status(session, job.post_id)
        session.commit()


def fail_delivery(job: DeliveryJob, message: str, retryable: bool) -> None:
    with SessionLocal() as session:
        delivery = session.scalar(
            select(ChannelDelivery).where(ChannelDelivery.id == job.delivery_id).with_for_update()
        )
        if delivery is None or delivery.status is not DeliveryStatus.PUBLISHING:
            logger.warning("Delivery %s changed state before failure was recorded", job.delivery_id)
            return
        now = utc_now()
        delivery.claimed_at = None
        delivery.error_message = message[:1000]
        if retryable and delivery.attempt_count < settings.max_delivery_attempts:
            delay_seconds = min(60 * (2 ** (delivery.attempt_count - 1)), 21600)
            delivery.status = DeliveryStatus.RETRY_WAIT
            delivery.next_attempt_at = now + timedelta(seconds=delay_seconds)
        else:
            delivery.status = DeliveryStatus.FAILED
            delivery.next_attempt_at = None
        reconcile_post_status(session, job.post_id)
        session.commit()


def mark_delivery_needs_review(job: DeliveryJob, message: str) -> None:
    with SessionLocal() as session:
        delivery = session.scalar(
            select(ChannelDelivery).where(ChannelDelivery.id == job.delivery_id).with_for_update()
        )
        if delivery is None or delivery.status is not DeliveryStatus.PUBLISHING:
            logger.warning("Delivery %s changed state before review status was recorded", job.delivery_id)
            return
        delivery.status = DeliveryStatus.NEEDS_REVIEW
        delivery.error_message = message[:1000]
        delivery.claimed_at = None
        delivery.next_attempt_at = None
        reconcile_post_status(session, job.post_id)
        session.commit()


def process_job(job: DeliveryJob) -> None:
    publisher = PUBLISHERS.get(job.channel)
    if publisher is None:
        return
    try:
        platform_post_id = publisher.publish(caption=job.caption, idempotency_key=str(job.delivery_id))
        if not platform_post_id:
            raise PermanentPublishError("The channel publisher returned no post ID")
    except PermanentPublishError as error:
        fail_delivery(job, str(error), retryable=False)
    except AmbiguousPublishError as error:
        mark_delivery_needs_review(job, str(error))
    except RetryablePublishError as error:
        fail_delivery(job, str(error), retryable=True)
    except Exception as error:  # noqa: BLE001 - provider SDKs raise varied transient exceptions
        logger.error("Publisher error for delivery %s (%s)", job.delivery_id, type(error).__name__)
        fail_delivery(job, "Unexpected provider error; retrying with backoff.", retryable=True)
    else:
        complete_delivery(job, platform_post_id)


def run_forever() -> None:
    while True:
        try:
            with SessionLocal() as session:
                move_expired_claims_to_review(session, utc_now())
                jobs = claim_due_deliveries(session, utc_now())
            for job in jobs:
                process_job(job)
        except SQLAlchemyError:
            logger.exception("Database error in publishing worker")
        except Exception:
            logger.exception("Unexpected publishing worker error")
        time.sleep(settings.worker_poll_seconds)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    run_forever()
