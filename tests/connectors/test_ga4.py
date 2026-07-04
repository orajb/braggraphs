import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from connectors.ga4 import GA4Connector, GA4Error, METRIC_MAP
from core.config import GA4Property

FIXTURES = Path(__file__).parent / "fixtures"

ITEM = GA4Property(
    label="zubrafex.com", property_id="123456789", metrics=["pageviews", "sessions"]
)


class StubClient:
    """Mimics BetaAnalyticsDataClient.run_report against a recorded fixture."""

    def __init__(self, fixture_name="ga4_run_report.json", error=None):
        self.fixture = json.loads((FIXTURES / fixture_name).read_text())
        self.error = error
        self.requests = []

    def run_report(self, request):
        self.requests.append(request)
        if self.error:
            raise self.error
        rows = [
            SimpleNamespace(
                dimension_values=[SimpleNamespace(value=r["date"])],
                metric_values=[SimpleNamespace(value=str(v)) for v in r["values"]],
            )
            for r in self.fixture["rows"]
        ]
        return SimpleNamespace(rows=rows)


def make_connector(**kw):
    client = StubClient(**kw)
    return GA4Connector(client_factory=lambda: client), client


def yesterday():
    return datetime.now(timezone.utc).date() - timedelta(days=1)


def test_fetch_requests_previous_complete_day():
    conn, client = make_connector()
    conn.fetch(ITEM)
    (req,) = client.requests
    assert req.property == "properties/123456789"
    dr = req.date_ranges[0]
    assert dr.start_date == dr.end_date == yesterday().isoformat()
    assert [m.name for m in req.metrics] == ["screenPageViews", "sessions"]
    assert req.dimensions[0].name == "date"


def test_points_mapped_with_dates_and_kind():
    conn, _ = make_connector()
    points = conn.fetch(ITEM)
    assert len(points) == 6  # 3 rows × 2 metrics
    pv = [p for p in points if p.metric == "pageviews"]
    assert [(p.date, p.value) for p in pv] == [
        ("2026-06-28", 812.0),
        ("2026-06-29", 1240.0),
        ("2026-06-30", 903.0),
    ]
    assert all(p.kind == "flow" and p.project == "zubrafex.com" for p in points)


def test_backfill_covers_365_days():
    conn, client = make_connector()
    conn.backfill(ITEM)
    dr = client.requests[0].date_ranges[0]
    assert dr.end_date == yesterday().isoformat()
    assert dr.start_date == (yesterday() - timedelta(days=364)).isoformat()


def test_api_error_wrapped_with_viewer_hint():
    conn, _ = make_connector(error=PermissionError("caller lacks permission"))
    with pytest.raises(GA4Error, match="Viewer role"):
        conn.fetch(ITEM)


def test_metric_map_covers_allowlist():
    from core.config import GA4_METRICS

    assert set(METRIC_MAP) == GA4_METRICS
