"""GA4 connector — site traffic: pageviews, sessions, active users.

Auth: a Google Cloud service-account JSON key (GOOGLE_APPLICATION_CREDENTIALS)
granted the Viewer role on each GA4 property. Two APIs are used:

- Data API v1 (google-analytics-data): the metric values.
- Admin API v1 (google-analytics-admin): `list_properties()` powers the
  property picker (`flask ga4-properties`) so nobody hand-hunts numeric IDs.

Traffic is a per-day flow, so the daily fetch records the *previous complete
UTC day* — never today's partial, always-low count. Backfill is one Data API
call over the last 365 days: GA4 retains history, so unlike GitHub this is
true backfill. Both paths key rows by GA4's own date dimension, making them
naturally idempotent.

Google client imports are deferred into the methods: GitHub-only installs
never touch (or need) the Google libraries at runtime.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from core.connector_base import Connector, Point

log = logging.getLogger(__name__)

# v1 allowlist (config validation enforces it) → GA4 Data API metric names
METRIC_MAP = {
    "pageviews": "screenPageViews",
    "sessions": "sessions",
    "active_users": "activeUsers",
}


class GA4Error(Exception):
    pass


def _default_client_factory():
    from google.analytics.data_v1beta import BetaAnalyticsDataClient

    return BetaAnalyticsDataClient()


class GA4Connector(Connector):
    name = "ga4"

    def __init__(self, client_factory=_default_client_factory, backfill_days: int = 365):
        self._client_factory = client_factory
        self._client = None
        self._backfill_days = backfill_days

    def _get_client(self):
        if self._client is None:
            self._client = self._client_factory()
        return self._client

    @staticmethod
    def _yesterday() -> date:
        return datetime.now(timezone.utc).date() - timedelta(days=1)

    def fetch(self, item) -> list[Point]:
        yday = self._yesterday()
        return self._report(item, yday, yday)

    def backfill(self, item) -> list[Point]:
        yday = self._yesterday()
        return self._report(item, yday - timedelta(days=self._backfill_days - 1), yday)

    def _report(self, item, start: date, end: date) -> list[Point]:
        from google.analytics.data_v1beta.types import (
            DateRange,
            Dimension,
            Metric,
            RunReportRequest,
        )

        request = RunReportRequest(
            property=f"properties/{item.property_id}",
            dimensions=[Dimension(name="date")],
            metrics=[Metric(name=METRIC_MAP[m]) for m in item.metrics],
            date_ranges=[
                DateRange(start_date=start.isoformat(), end_date=end.isoformat())
            ],
            limit=100_000,
        )
        try:
            response = self._get_client().run_report(request)
        except Exception as e:
            raise GA4Error(
                f"GA4 Data API error for property {item.property_id}: {e} — is the "
                "service account granted the Viewer role on this property?"
            ) from e

        points = []
        for row in response.rows:
            raw = row.dimension_values[0].value  # '20260630'
            day = f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
            for i, metric in enumerate(item.metrics):
                points.append(
                    Point(item.label, metric, "flow", float(row.metric_values[i].value), day)
                )
        return points


def list_properties() -> list[dict]:
    """Every GA4 property the service account can see — the property picker."""
    from google.analytics.admin_v1beta import AnalyticsAdminServiceClient

    client = AnalyticsAdminServiceClient()
    out = []
    for account in client.list_account_summaries():
        for ps in account.property_summaries:
            out.append(
                {
                    "property_id": ps.property.split("/")[-1],
                    "display_name": ps.display_name,
                    "account": account.display_name,
                }
            )
    return out
