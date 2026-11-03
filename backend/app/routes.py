from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from .database import get_session
from .models import Channel, ChannelDelivery, DeliveryStatus, Post, PostStatus, utc_now
from .schemas import PostCreate, PostRead, PostUpdate, ScheduleRequest, validate_caption_for_channels

router = APIRouter(prefix="/api/posts", tags=["posts"])


def get_post(session: Session, post_id: UUID) -> Post:
    statement = select(Post).options(selectinload(Post.deliveries)).where(Post.id == post_id)
    post = session.scalar(statement)
    if post is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found")
    return post


@router.get("", response_model=list[PostRead])
def list_posts(
    post_status: PostStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
) -> list[Post]:
    statement = select(Post).options(selectinload(Post.deliveries)).order_by(Post.created_at.desc())
    if post_status is not None:
        statement = statement.where(Post.status == post_status)
    try:
        return list(session.scalars(statement.limit(limit).offset(offset)).unique().all())
    except SQLAlchemyError as error:
        raise HTTPException(status_code=503, detail="Could not load posts") from error


@router.post("", response_model=PostRead, status_code=status.HTTP_201_CREATED)
def create_post(payload: PostCreate, session: Session = Depends(get_session)) -> Post:
    post = Post(caption=payload.caption, channels=[channel.value for channel in payload.channels])
    try:
        session.add(post)
        session.commit()
        return get_post(session, post.id)
    except SQLAlchemyError as error:
        session.rollback()
        raise HTTPException(status_code=503, detail="Could not save post") from error


@router.patch("/{post_id}", response_model=PostRead)
def update_post(post_id: UUID, payload: PostUpdate, session: Session = Depends(get_session)) -> Post:
    post = get_post(session, post_id)
    if post.status is not PostStatus.DRAFT:
        raise HTTPException(status_code=409, detail="Only draft posts can be edited")
    changes = payload.model_dump(exclude_unset=True)
    if "caption" in changes and changes["caption"] is not None:
        post.caption = changes["caption"].strip()
        if not post.caption:
            raise HTTPException(status_code=422, detail="Caption must contain non-whitespace text")
    if "channels" in changes and changes["channels"] is not None:
        channels = changes["channels"]
        if not channels:
            raise HTTPException(status_code=422, detail="Select at least one channel")
        if len(set(channels)) != len(channels):
            raise HTTPException(status_code=422, detail="Each channel can only be selected once")
        post.channels = [channel.value for channel in channels]
    try:
        validate_caption_for_channels(post.caption, [Channel(channel) for channel in post.channels])
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    try:
        session.commit()
        return get_post(session, post.id)
    except SQLAlchemyError as error:
        session.rollback()
        raise HTTPException(status_code=503, detail="Could not update post") from error


@router.post("/{post_id}/approve", response_model=PostRead)
def approve_post(post_id: UUID, session: Session = Depends(get_session)) -> Post:
    post = get_post(session, post_id)
    if post.status is not PostStatus.DRAFT:
        raise HTTPException(status_code=409, detail="Only draft posts can be approved")
    post.status = PostStatus.APPROVED
    post.approved_at = utc_now()
    try:
        session.commit()
        return get_post(session, post.id)
    except SQLAlchemyError as error:
        session.rollback()
        raise HTTPException(status_code=503, detail="Could not approve post") from error


@router.post("/{post_id}/schedule", response_model=PostRead)
def schedule_post(post_id: UUID, payload: ScheduleRequest, session: Session = Depends(get_session)) -> Post:
    post = get_post(session, post_id)
    if post.status is not PostStatus.APPROVED:
        raise HTTPException(status_code=409, detail="Approve the post before scheduling")
    scheduled_at = payload.scheduled_at
    if scheduled_at.tzinfo is None:
        raise HTTPException(status_code=422, detail="scheduled_at must include a timezone offset")
    if scheduled_at <= datetime.now(UTC):
        raise HTTPException(status_code=422, detail="scheduled_at must be in the future")
    try:
        post.scheduled_at = scheduled_at.astimezone(UTC)
        post.status = PostStatus.SCHEDULED
        post.deliveries = [
            ChannelDelivery(channel=Channel(channel_name), status=DeliveryStatus.QUEUED)
            for channel_name in post.channels
        ]
        session.commit()
        return get_post(session, post.id)
    except (IntegrityError, ValueError) as error:
        session.rollback()
        raise HTTPException(status_code=409, detail="Could not create channel delivery records") from error
    except SQLAlchemyError as error:
        session.rollback()
        raise HTTPException(status_code=503, detail="Could not schedule post") from error


@router.post("/{post_id}/cancel", response_model=PostRead)
def cancel_post(post_id: UUID, session: Session = Depends(get_session)) -> Post:
    post = get_post(session, post_id)
    if post.status not in {PostStatus.DRAFT, PostStatus.APPROVED, PostStatus.SCHEDULED}:
        raise HTTPException(status_code=409, detail="This post can no longer be cancelled")
    post.status = PostStatus.CANCELLED
    for delivery in post.deliveries:
        if delivery.status is DeliveryStatus.QUEUED:
            session.delete(delivery)
    try:
        session.commit()
        return get_post(session, post.id)
    except SQLAlchemyError as error:
        session.rollback()
        raise HTTPException(status_code=503, detail="Could not cancel post") from error
