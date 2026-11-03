from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

import httpx
from sqlalchemy import select

from .connections import ChannelConnection, decrypt_access_token
from .database import SessionLocal
from .models import Channel, ChannelDelivery, DeliveryStatus, MonthlyPostMetric

logger = logging.getLogger("snowsocial.analytics")
GRAPH_VERSION = os.getenv("META_GRAPH_API_VERSION", "v26.0").strip()
METRIC_LOOKBACK_DAYS = 45
METRIC_FIELDS = ("reach", "likes", "comments", "shares", "saves")


def _instagram_connection() -> tuple[ChannelConnection, str] | None:
    selected_account_id = os.getenv("META_INSTAGRAM_ACCOUNT_ID", "").strip()
    statement = select(ChannelConnection).where(
        ChannelConnection.provider == "meta",
        ChannelConnection.channel == Channel.INSTAGRAM.value,
    )
    if selected_account_id:
        statement = statement.where(ChannelConnection.provider_account_id == selected_account_id)

    with SessionLocal() as session:
        connections = list(session.scalars(statement).all())
        if len(connections) != 1:
            if len(connections) > 1:
                logger.warning("Instagram analytics needs META_INSTAGRAM_ACCOUNT_ID when multiple accounts are connected")
            return None

        connection = connections[0]
        expires_at = connection.token_expires_at
        if expires_at is not None:
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            if expires_at <= datetime.now(UTC):
                logger.warning("Instagram analytics token has expired; reconnect the Meta account")
                return None
        try:
            access_token = decrypt_access_token(connection)
        except ValueError:
            logger.exception("Could not decrypt the Instagram token for analytics")
            return None
        return connection, access_token


def _request_json(url: str, *, access_token: str, params: dict[str, str]) -> dict:
    response = httpx.get(
        url,
        headers={"Authorization": f"Bearer {access_token}"},
        params=params,
        timeout=httpx.Timeout(20.0, connect=5.0),
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Meta returned an unexpected analytics response")
    return payload


def _count(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    return int(value)


def _insight_counts(payload: dict) -> dict[str, int]:
    mapped_names = {
        "reach": "reach",
        "saved": "saves",
        "shares": "shares",
    }
    counts: dict[str, int] = {}
    data = payload.get("data")
    if not isinstance(data, list):
        return counts
    for insight in data:
        if not isinstance(insight, dict):
            continue
        field = mapped_names.get(insight.get("name"))
        if field is None:
            continue
        values = insight.get("values")
        raw_value = values[0].get("value") if isinstance(values, list) and values and isinstance(values[0], dict) else None
        if raw_value is None:
            total = insight.get("total_value")
            raw_value = total.get("value") if isinstance(total, dict) else None
        metric = _count(raw_value)
        if metric is not None:
            counts[field] = metric
    return counts


def sync_recent_instagram_metrics(*, batch_size: int) -> tuple[int, int]:
    """Refresh lifetime metrics for recently published Instagram media.

    Each result is grouped under the month the post was published, so the monthly
    report describes that month's published content cohort rather than daily activity.
    """
    account = _instagram_connection()
    if account is None:
        return 0, 0
    connection, access_token = account
    now = datetime.now(UTC)
    cutoff = now - timedelta(days=METRIC_LOOKBACK_DAYS)
    with SessionLocal() as session:
        deliveries = list(
            session.scalars(
                select(ChannelDelivery)
                .where(
                    ChannelDelivery.channel == Channel.INSTAGRAM,
                    ChannelDelivery.status == DeliveryStatus.PUBLISHED,
                    ChannelDelivery.platform_post_id.is_not(None),
                    ChannelDelivery.published_at.is_not(None),
                    ChannelDelivery.published_at >= cutoff,
                )
                .order_by(ChannelDelivery.published_at.desc())
                .limit(batch_size)
            ).all()
        )

    if not deliveries:
        return 0, 0

    base_url = f"https://graph.facebook.com/{GRAPH_VERSION}"
    succeeded = 0
    failed = 0
    for delivery in deliveries:
        media_id = quote(delivery.platform_post_id or "", safe="")
        media_url = f"{base_url}/{media_id}"
        try:
            media = _request_json(
                media_url,
                access_token=access_token,
                params={"fields": "like_count,comments_count"},
            )
            insights = _request_json(
                f"{media_url}/insights",
                access_token=access_token,
                params={"metric": "reach,saved,shares"},
            )
        except (httpx.HTTPError, ValueError, TypeError) as error:
            logger.warning(
                "Could not fetch Instagram insights for delivery %s (%s)",
                delivery.id,
                type(error).__name__,
            )
            failed += 1
            continue

        counts = _insight_counts(insights)
        for source, target in (("like_count", "likes"), ("comments_count", "comments")):
            value = _count(media.get(source))
            if value is not None:
                counts[target] = value
        published_at = delivery.published_at
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=UTC)
        month = published_at.date().replace(day=1)

        try:
            with SessionLocal() as session:
                metric = session.scalar(
                    select(MonthlyPostMetric).where(
                        MonthlyPostMetric.delivery_id == delivery.id,
                        MonthlyPostMetric.month == month,
                    )
                )
                if metric is None:
                    metric = MonthlyPostMetric(delivery_id=delivery.id, month=month)
                    session.add(metric)
                for field in METRIC_FIELDS:
                    if field in counts:
                        setattr(metric, field, counts[field])
                metric.imported_at = now
                session.commit()
            succeeded += 1
        except Exception as error:  # noqa: BLE001 - keep one bad row from stopping the periodic sync
            logger.warning(
                "Could not save Instagram insights for delivery %s (%s)",
                delivery.id,
                type(error).__name__,
            )
            failed += 1

    logger.info(
        "Instagram analytics sync finished for account %s: %s updated, %s failed",
        connection.provider_account_id,
        succeeded,
        failed,
    )
    return succeeded, failed
