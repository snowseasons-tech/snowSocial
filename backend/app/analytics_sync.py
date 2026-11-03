from __future__ import annotations

import logging
import os
from datetime import UTC, date, datetime, timedelta
from urllib.parse import quote

import httpx
from sqlalchemy import select

from .connections import ChannelConnection, decrypt_access_token
from .database import SessionLocal
from .google_auth import get_google_access_token
from .models import Channel, ChannelDelivery, DeliveryStatus, MonthlyChannelMetric, MonthlyPostMetric

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


def _facebook_connection() -> tuple[ChannelConnection, str] | None:
    selected_page_id = os.getenv("META_FACEBOOK_PAGE_ID", "").strip()
    statement = select(ChannelConnection).where(
        ChannelConnection.provider == "meta",
        ChannelConnection.channel == Channel.FACEBOOK.value,
    )
    if selected_page_id:
        statement = statement.where(ChannelConnection.provider_account_id == selected_page_id)

    with SessionLocal() as session:
        connections = list(session.scalars(statement).all())
        if len(connections) != 1:
            if len(connections) > 1:
                logger.warning("Facebook analytics needs META_FACEBOOK_PAGE_ID when multiple Pages are connected")
            return None

        connection = connections[0]
        expires_at = connection.token_expires_at
        if expires_at is not None:
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            if expires_at <= datetime.now(UTC):
                logger.warning("Facebook Page token has expired; reconnect the Meta account")
                return None
        try:
            access_token = decrypt_access_token(connection)
        except ValueError:
            logger.exception("Could not decrypt the Facebook Page token for analytics")
            return None
        return connection, access_token


def _facebook_post_counts(payload: dict) -> dict[str, int]:
    counts: dict[str, int] = {}
    reactions = payload.get("reactions")
    if isinstance(reactions, dict):
        summary = reactions.get("summary")
        if isinstance(summary, dict):
            value = _count(summary.get("total_count"))
            if value is not None:
                counts["likes"] = value

    comments = payload.get("comments")
    if isinstance(comments, dict):
        summary = comments.get("summary")
        if isinstance(summary, dict):
            value = _count(summary.get("total_count"))
            if value is not None:
                counts["comments"] = value

    shares = payload.get("shares")
    if isinstance(shares, dict):
        value = _count(shares.get("count"))
        if value is not None:
            counts["shares"] = value
    return counts


def sync_recent_facebook_metrics(*, batch_size: int) -> tuple[int, int]:
    """Refresh Facebook Page post reaction, comment, and share counts."""
    account = _facebook_connection()
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
                    ChannelDelivery.channel == Channel.FACEBOOK,
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
    fields = "reactions.type(LIKE).limit(0).summary(total_count),comments.limit(0).summary(true),shares"
    succeeded = 0
    failed = 0
    for delivery in deliveries:
        post_id = quote(delivery.platform_post_id or "", safe="")
        try:
            post = _request_json(
                f"{base_url}/{post_id}",
                access_token=access_token,
                params={"fields": fields},
            )
        except (httpx.HTTPError, ValueError, TypeError) as error:
            logger.warning(
                "Could not fetch Facebook post metrics for delivery %s (%s)",
                delivery.id,
                type(error).__name__,
            )
            failed += 1
            continue

        counts = _facebook_post_counts(post)
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
                "Could not save Facebook post metrics for delivery %s (%s)",
                delivery.id,
                type(error).__name__,
            )
            failed += 1

    logger.info(
        "Facebook analytics sync finished for Page %s: %s updated, %s failed",
        connection.provider_account_id,
        succeeded,
        failed,
    )
    return succeeded, failed



GOOGLE_DAILY_METRICS = (
    "BUSINESS_IMPRESSIONS_DESKTOP_MAPS",
    "BUSINESS_IMPRESSIONS_DESKTOP_SEARCH",
    "BUSINESS_IMPRESSIONS_MOBILE_MAPS",
    "BUSINESS_IMPRESSIONS_MOBILE_SEARCH",
    "WEBSITE_CLICKS",
    "CALL_CLICKS",
    "BUSINESS_DIRECTION_REQUESTS",
)
GOOGLE_METRIC_FIELDS = ("impressions", "link_clicks", "calls", "direction_requests")


def _google_count(value: object) -> int | None:
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    return _count(value)


def _google_connection() -> ChannelConnection | None:
    location_id = os.getenv("GOOGLE_BUSINESS_LOCATION_ID", "").strip()
    statement = select(ChannelConnection).where(
        ChannelConnection.provider == "google",
        ChannelConnection.channel == Channel.GOOGLE_BUSINESS.value,
    )
    if location_id:
        statement = statement.where(ChannelConnection.provider_account_id == location_id)
    with SessionLocal() as session:
        connections = list(session.scalars(statement).all())
        if len(connections) != 1:
            if len(connections) > 1:
                logger.warning("Google Business analytics needs GOOGLE_BUSINESS_LOCATION_ID when multiple locations are connected")
            return None
        return connections[0]


def sync_google_business_metrics() -> tuple[int, int]:
    """Refresh profile-level Google metrics for the previous and current calendar month."""
    connection = _google_connection()
    if connection is None:
        return 0, 0

    today = datetime.now(UTC).date()
    current_month = today.replace(day=1)
    previous_month = (current_month - timedelta(days=1)).replace(day=1)
    location_id = connection.provider_account_id.rsplit("/", 1)[-1]
    resource_name = f"locations/{location_id}"
    params: list[tuple[str, str]] = [("dailyMetrics", metric) for metric in GOOGLE_DAILY_METRICS]
    for prefix, value in (
        ("dailyRange.start_date", previous_month),
        ("dailyRange.end_date", today),
    ):
        params.extend(
            (f"{prefix}.{part}", str(number))
            for part, number in (("year", value.year), ("month", value.month), ("day", value.day))
        )

    try:
        with SessionLocal() as session:
            managed_connection = session.get(ChannelConnection, connection.id)
            if managed_connection is None:
                return 0, 0
            token = get_google_access_token(managed_connection, session)
        response = httpx.get(
            f"https://businessprofileperformance.googleapis.com/v1/{resource_name}:fetchMultiDailyMetricsTimeSeries",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            timeout=httpx.Timeout(30.0, connect=5.0),
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Google returned an unexpected performance response")
    except (httpx.HTTPError, ValueError, TypeError) as error:
        logger.warning("Could not fetch Google Business metrics for location %s (%s)", connection.provider_account_id, type(error).__name__)
        return 0, 1

    totals_by_month: dict[date, dict[str, int]] = {
        previous_month: {field: 0 for field in GOOGLE_METRIC_FIELDS},
        current_month: {field: 0 for field in GOOGLE_METRIC_FIELDS},
    }
    field_by_metric = {
        "BUSINESS_IMPRESSIONS_DESKTOP_MAPS": "impressions",
        "BUSINESS_IMPRESSIONS_DESKTOP_SEARCH": "impressions",
        "BUSINESS_IMPRESSIONS_MOBILE_MAPS": "impressions",
        "BUSINESS_IMPRESSIONS_MOBILE_SEARCH": "impressions",
        "WEBSITE_CLICKS": "link_clicks",
        "CALL_CLICKS": "calls",
        "BUSINESS_DIRECTION_REQUESTS": "direction_requests",
    }
    for metric_group in payload.get("multiDailyMetricTimeSeries", []):
        if not isinstance(metric_group, dict):
            continue
        for series in metric_group.get("dailyMetricTimeSeries", []):
            if not isinstance(series, dict):
                continue
            target_field = field_by_metric.get(series.get("dailyMetric"))
            time_series = series.get("timeSeries")
            dated_values = time_series.get("datedValues", []) if isinstance(time_series, dict) else []
            if target_field is None or not isinstance(dated_values, list):
                continue
            for dated_value in dated_values:
                if not isinstance(dated_value, dict) or not isinstance(dated_value.get("date"), dict):
                    continue
                value_date = dated_value["date"]
                try:
                    point_date = date(
                        int(value_date["year"]),
                        int(value_date["month"]),
                        int(value_date.get("day", 1)),
                    )
                except (KeyError, TypeError, ValueError):
                    continue
                month = point_date.replace(day=1)
                if month not in totals_by_month:
                    continue
                count = _google_count(dated_value.get("value"))
                if count is not None:
                    totals_by_month[month][target_field] += count

    succeeded = 0
    failed = 0
    for month, counts in totals_by_month.items():
        try:
            with SessionLocal() as session:
                metric = session.scalar(
                    select(MonthlyChannelMetric).where(
                        MonthlyChannelMetric.channel == Channel.GOOGLE_BUSINESS.value,
                        MonthlyChannelMetric.provider_account_id == connection.provider_account_id,
                        MonthlyChannelMetric.month == month,
                    )
                )
                if metric is None:
                    metric = MonthlyChannelMetric(
                        channel=Channel.GOOGLE_BUSINESS.value,
                        provider_account_id=connection.provider_account_id,
                        month=month,
                    )
                    session.add(metric)
                for field in GOOGLE_METRIC_FIELDS:
                    setattr(metric, field, counts[field])
                metric.imported_at = datetime.now(UTC)
                session.commit()
            succeeded += 1
        except Exception as error:  # noqa: BLE001 - isolate a failed month from the remaining sync
            logger.warning("Could not save Google Business metrics for %s (%s)", month, type(error).__name__)
            failed += 1

    logger.info(
        "Google Business metrics sync finished for location %s: %s months updated, %s failed",
        connection.provider_account_id,
        succeeded,
        failed,
    )
    return succeeded, failed
