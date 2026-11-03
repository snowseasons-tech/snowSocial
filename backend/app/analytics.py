from __future__ import annotations

import hmac
import os
from datetime import UTC, date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .database import get_session
from .models import Channel, ChannelDelivery, DeliveryStatus, MonthlyChannelMetric, MonthlyPostMetric

router = APIRouter(prefix="/api/analytics", tags=["analytics"])
METRIC_FIELDS = ("impressions", "reach", "likes", "comments", "shares", "saves", "link_clicks", "calls", "direction_requests")


class MetricImportItem(BaseModel):
    delivery_id: UUID
    month: date
    impressions: int | None = Field(default=None, ge=0)
    reach: int | None = Field(default=None, ge=0)
    likes: int | None = Field(default=None, ge=0)
    comments: int | None = Field(default=None, ge=0)
    shares: int | None = Field(default=None, ge=0)
    saves: int | None = Field(default=None, ge=0)
    link_clicks: int | None = Field(default=None, ge=0)
    calls: int | None = Field(default=None, ge=0)
    direction_requests: int | None = Field(default=None, ge=0)

    @field_validator("month")
    @classmethod
    def month_must_be_first_day(cls, value: date) -> date:
        if value.day != 1:
            raise ValueError("month must be the first day of the reporting month")
        return value


class MetricImportRequest(BaseModel):
    items: list[MetricImportItem] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def unique_delivery_month_pairs(self) -> MetricImportRequest:
        keys = [(item.delivery_id, item.month) for item in self.items]
        if len(set(keys)) != len(keys):
            raise ValueError("Each delivery and month pair can appear only once per import")
        return self


class MetricTotals(BaseModel):
    published_posts: int
    impressions: int | None
    reach: int | None
    likes: int | None
    comments: int | None
    shares: int | None
    saves: int | None
    link_clicks: int | None
    calls: int | None
    direction_requests: int | None


class ChannelMetricSummary(BaseModel):
    channel: str
    metrics: MetricTotals


class MonthlySummary(BaseModel):
    month: date
    totals: MetricTotals
    by_channel: list[ChannelMetricSummary]
    previous_month: date
    previous_totals: MetricTotals
    change_percent: dict[str, float | None]


def require_ingest_token(
    supplied_token: Annotated[str | None, Header(alias="X-Analytics-Ingest-Token")] = None,
) -> None:
    expected = os.getenv("ANALYTICS_INGEST_TOKEN", "").strip()
    if len(expected) < 32:
        raise HTTPException(status_code=503, detail="Analytics ingestion is not configured")
    if supplied_token is None or not hmac.compare_digest(supplied_token, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid ingestion token")


def previous_month(month: date) -> date:
    if month.month == 1:
        return date(month.year - 1, 12, 1)
    return date(month.year, month.month - 1, 1)


def _aggregate_month(session: Session, month: date) -> tuple[MetricTotals, list[ChannelMetricSummary]]:
    statement = (
        select(
            ChannelDelivery.channel,
            func.count(func.distinct(MonthlyPostMetric.delivery_id)),
            *(func.sum(getattr(MonthlyPostMetric, field)) for field in METRIC_FIELDS),
        )
        .join(ChannelDelivery, ChannelDelivery.id == MonthlyPostMetric.delivery_id)
        .where(MonthlyPostMetric.month == month, ChannelDelivery.status == DeliveryStatus.PUBLISHED)
        .group_by(ChannelDelivery.channel)
    )
    channel_values: dict[str, dict[str, int | None]] = {}
    for row in session.execute(statement).all():
        channel, post_count, *values = row
        channel_values[channel.value] = {
            "published_posts": post_count,
            **dict(zip(METRIC_FIELDS, values, strict=True)),
        }

    profile_statement = (
        select(
            MonthlyChannelMetric.channel,
            *(func.sum(getattr(MonthlyChannelMetric, field)) for field in METRIC_FIELDS),
        )
        .where(MonthlyChannelMetric.month == month)
        .group_by(MonthlyChannelMetric.channel)
    )
    next_month = date(month.year + 1, 1, 1) if month.month == 12 else date(month.year, month.month + 1, 1)
    for row in session.execute(profile_statement).all():
        channel, *values = row
        summary = channel_values.setdefault(
            channel,
            {"published_posts": 0, **{field: None for field in METRIC_FIELDS}},
        )
        if channel == Channel.GOOGLE_BUSINESS.value:
            published_posts = session.scalar(
                select(func.count(ChannelDelivery.id)).where(
                    ChannelDelivery.channel == Channel.GOOGLE_BUSINESS,
                    ChannelDelivery.status == DeliveryStatus.PUBLISHED,
                    ChannelDelivery.published_at >= datetime.combine(month, datetime.min.time(), tzinfo=UTC),
                    ChannelDelivery.published_at < datetime.combine(next_month, datetime.min.time(), tzinfo=UTC),
                )
            )
            summary["published_posts"] = published_posts or 0
        for field, value in zip(METRIC_FIELDS, values, strict=True):
            if value is not None:
                summary[field] = (summary[field] or 0) + value

    channel_totals = [
        ChannelMetricSummary(channel=channel, metrics=MetricTotals(**values))
        for channel, values in sorted(channel_values.items())
    ]
    totals = MetricTotals(
        published_posts=sum(item.metrics.published_posts for item in channel_totals),
        **{
            field: (
                sum(getattr(item.metrics, field) or 0 for item in channel_totals)
                if any(getattr(item.metrics, field) is not None for item in channel_totals)
                else None
            )
            for field in METRIC_FIELDS
        },
    )
    return totals, channel_totals


@router.post("/metrics", status_code=status.HTTP_202_ACCEPTED)
def import_metrics(
    payload: MetricImportRequest,
    _: None = Depends(require_ingest_token),
    session: Session = Depends(get_session),
) -> dict[str, int | str]:
    try:
        for item in payload.items:
            delivery = session.get(ChannelDelivery, item.delivery_id)
            if delivery is None or delivery.status is not DeliveryStatus.PUBLISHED:
                raise HTTPException(status_code=409, detail=f"Delivery {item.delivery_id} is not published")
            metric = session.scalar(
                select(MonthlyPostMetric).where(
                    MonthlyPostMetric.delivery_id == item.delivery_id,
                    MonthlyPostMetric.month == item.month,
                )
            )
            if metric is None:
                metric = MonthlyPostMetric(delivery_id=item.delivery_id, month=item.month)
                session.add(metric)
            for field in METRIC_FIELDS:
                setattr(metric, field, getattr(item, field))
            metric.imported_at = datetime.now(UTC)
        session.commit()
    except HTTPException:
        session.rollback()
        raise
    except SQLAlchemyError as error:
        session.rollback()
        raise HTTPException(status_code=503, detail="Could not import metrics") from error
    return {"imported": len(payload.items), "status": "accepted"}


@router.get("/monthly", response_model=MonthlySummary)
def monthly_summary(
    month: date = Query(description="First day of the reporting month, for example 2026-11-01"),
    session: Session = Depends(get_session),
) -> MonthlySummary:
    if month.day != 1:
        raise HTTPException(status_code=422, detail="month must be the first day of the reporting month")
    prior_month = previous_month(month)
    try:
        totals, channels = _aggregate_month(session, month)
        prior_totals, _ = _aggregate_month(session, prior_month)
    except SQLAlchemyError as error:
        raise HTTPException(status_code=503, detail="Could not load monthly analytics") from error
    changes = {
        field: (
            round((current - prior) * 100 / prior, 2)
            if current is not None and prior not in (None, 0)
            else None
        )
        for field in METRIC_FIELDS
        for current, prior in [(getattr(totals, field), getattr(prior_totals, field))]
    }
    return MonthlySummary(
        month=month,
        totals=totals,
        by_channel=channels,
        previous_month=prior_month,
        previous_totals=prior_totals,
        change_percent=changes,
    )
