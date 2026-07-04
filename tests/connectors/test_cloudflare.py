import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from connectors.cloudflare import CloudflareConnector, CloudflareError, list_sites
from core.config import CloudflareSite

FIXTURES = Path(__file__).parent / "fixtures"

ACCOUNT = "abcdef0123456789abcdef0123456789"
SITE_TAG = "0123456789abcdef0123456789abcdef"
ITEM = CloudflareSite(
    label="zubrafex.com", site_tag=SITE_TAG, metrics=["visits", "pageviews"]
)


class StubPost:
    def __init__(self, fixture_name):
        self.body = json.loads((FIXTURES / fixture_name).read_text())
        self.calls = []

    def __call__(self, token, payload):
        self.calls.append((token, payload))
        return self.body


def yesterday():
    return datetime.now(timezone.utc).date() - timedelta(days=1)


def test_fetch_queries_yesterday_and_maps_points():
    post = StubPost("cloudflare_rum.json")
    conn = CloudflareConnector("cf-tok", ACCOUNT, http_post=post)
    points = conn.fetch(ITEM)

    (token, payload), = post.calls
    assert token == "cf-tok"
    query = payload["query"]
    assert f'date_geq: "{yesterday().isoformat()}"' in query
    assert f'date_leq: "{yesterday().isoformat()}"' in query
    assert SITE_TAG in query and ACCOUNT in query

    visits = [(p.date, p.value) for p in points if p.metric == "visits"]
    assert visits == [  # sorted by date despite out-of-order fixture rows
        ("2026-06-28", 610.0),
        ("2026-06-29", 700.0),
        ("2026-06-30", 830.0),
    ]
    pageviews = [(p.date, p.value) for p in points if p.metric == "pageviews"]
    assert pageviews[-1] == ("2026-06-30", 1240.0)
    assert all(p.kind == "flow" and p.project == "zubrafex.com" for p in points)


def test_visits_only_when_configured():
    post = StubPost("cloudflare_rum.json")
    item = CloudflareSite(label="zubrafex.com", site_tag=SITE_TAG, metrics=["visits"])
    points = CloudflareConnector("t", ACCOUNT, http_post=post).fetch(item)
    assert {p.metric for p in points} == {"visits"}


def test_backfill_covers_90_days():
    post = StubPost("cloudflare_rum.json")
    CloudflareConnector("t", ACCOUNT, http_post=post).backfill(ITEM)
    query = post.calls[0][1]["query"]
    assert f'date_geq: "{(yesterday() - timedelta(days=89)).isoformat()}"' in query
    assert f'date_leq: "{yesterday().isoformat()}"' in query


def test_graphql_error_wrapped_with_permission_hint():
    post = StubPost("cloudflare_rum.json")
    post.body = {"errors": [{"message": "authentication error"}]}
    with pytest.raises(CloudflareError, match="Account Analytics"):
        CloudflareConnector("t", ACCOUNT, http_post=post).fetch(ITEM)


def test_missing_account_flagged():
    post = StubPost("cloudflare_rum.json")
    post.body = {"data": {"viewer": {"accounts": []}}}
    with pytest.raises(CloudflareError, match="account_id"):
        CloudflareConnector("t", ACCOUNT, http_post=post).fetch(ITEM)


def test_bad_account_id_rejected():
    with pytest.raises(CloudflareError, match="32-char hex"):
        CloudflareConnector("t", "not-a-hex-id")


def test_list_sites_aggregates_and_sorts():
    post = StubPost("cloudflare_sites.json")
    sites = list_sites("t", ACCOUNT, http_post=post)
    assert [s["site_tag"] for s in sites] == [
        "fedcba9876543210fedcba9876543210",  # 88 visits
        "0123456789abcdef0123456789abcdef",  # 30 + 9 aggregated
    ]
    assert sites[1]["visits"] == 39
