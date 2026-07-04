"""Cloudflare Web Analytics connector — site traffic: visits, pageviews.

Data source: the Web Analytics (RUM) GraphQL dataset
`rumPageloadEventsAdaptiveGroups`. Its numbers come from a JS beacon that
only fires in real browsers, so bot traffic is excluded by construction —
no filtering, scoring, or paid bot-management needed.

Auth: a Cloudflare API token with the account-level **Account Analytics:
Read** permission (`CLOUDFLARE_API_TOKEN`). The account id is not a
secret and lives in config.yml.

Traffic is a per-day flow, so the daily fetch records the *previous complete
UTC day* — never today's partial, always-low count. Web Analytics queries are
capped at a ~13-week window (measured: the API rejects ranges over "13w2d"),
so backfill seeds up to 90 days and graphs grow from there. Both paths key
rows by the dataset's own date dimension, making them naturally idempotent.

Queries interpolate their arguments instead of using GraphQL variables: the
filter scalars (Date) reject String-typed variables, and every interpolated
value is shape-checked first (32-hex tags, ISO dates), so this stays safe.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone

from core.connector_base import Connector, Point

log = logging.getLogger(__name__)

GRAPHQL_URL = "https://api.cloudflare.com/client/v4/graphql"

# v1 allowlist (config validation enforces it) → how each metric is read
# off a rumPageloadEventsAdaptiveGroups row.
METRIC_READERS = {
    "visits": lambda row: row["sum"]["visits"],
    "pageviews": lambda row: row["count"],
}

HEX32 = re.compile(r"^[0-9a-f]{32}$")


class CloudflareError(Exception):
    pass


def _hex32(value: str, what: str) -> str:
    if not HEX32.match(value or ""):
        raise CloudflareError(
            f"{what} {value!r} is not a 32-char hex id — copy it from the "
            "Cloudflare dashboard or `flask cf-sites`"
        )
    return value


def _default_http_post(token: str, payload: dict) -> dict:
    import requests

    r = requests.post(
        GRAPHQL_URL,
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    if r.status_code != 200:
        raise CloudflareError(
            f"Cloudflare GraphQL replied {r.status_code} — is the token valid and "
            "granted the account-level 'Account Analytics: Read' permission?"
        )
    return r.json()


def _run_query(token: str, query: str, http_post) -> list[dict]:
    body = http_post(token, {"query": query})
    errors = body.get("errors") or []
    if errors:
        raise CloudflareError(
            f"Cloudflare GraphQL error: {errors[0].get('message', errors[0])} — is the "
            "token granted the account-level 'Account Analytics: Read' permission?"
        )
    accounts = (body.get("data") or {}).get("viewer", {}).get("accounts", [])
    if not accounts:
        raise CloudflareError(
            "Cloudflare GraphQL returned no account — check cloudflare.account_id "
            "in config.yml and the token's account scope"
        )
    return next(iter(accounts[0].values()))


def _series_query(account_id: str, site_tag: str, start: date, end: date) -> str:
    return f"""{{ viewer {{ accounts(filter: {{accountTag: "{account_id}"}}) {{
      series: rumPageloadEventsAdaptiveGroups(
        limit: 400
        filter: {{siteTag: "{site_tag}", date_geq: "{start.isoformat()}", date_leq: "{end.isoformat()}"}}
      ) {{ count sum {{ visits }} dimensions {{ date }} }}
    }} }} }}"""


def _sites_query(account_id: str, start: date, end: date) -> str:
    return f"""{{ viewer {{ accounts(filter: {{accountTag: "{account_id}"}}) {{
      sites: rumPageloadEventsAdaptiveGroups(
        limit: 1000
        filter: {{date_geq: "{start.isoformat()}", date_leq: "{end.isoformat()}"}}
      ) {{ count sum {{ visits }} dimensions {{ siteTag, requestHost }} }}
    }} }} }}"""


class CloudflareConnector(Connector):
    name = "cloudflare"

    def __init__(
        self,
        token: str,
        account_id: str,
        http_post=_default_http_post,
        backfill_days: int = 90,
    ):
        self._token = token
        self._account_id = _hex32(account_id, "cloudflare.account_id")
        self._http_post = http_post
        self._backfill_days = backfill_days

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
        query = _series_query(
            self._account_id, _hex32(item.site_tag, f"site_tag for {item.label}"), start, end
        )
        rows = _run_query(self._token, query, self._http_post)
        points = []
        for row in sorted(rows, key=lambda r: r["dimensions"]["date"]):
            day = row["dimensions"]["date"]
            for metric in item.metrics:
                points.append(
                    Point(item.label, metric, "flow", float(METRIC_READERS[metric](row)), day)
                )
        return points


def list_sites(token: str, account_id: str, http_post=_default_http_post) -> list[dict]:
    """Site tags with recent Web Analytics data — the site picker (flask cf-sites).

    Only sites whose beacon reported at least one pageload in the last 30 days
    appear; a silent site either has Web Analytics disabled or truly no visits.
    """
    yday = datetime.now(timezone.utc).date() - timedelta(days=1)
    query = _sites_query(
        _hex32(account_id, "cloudflare.account_id"), yday - timedelta(days=29), yday
    )
    rows = _run_query(token, query, http_post)
    by_tag: dict[str, dict] = {}
    for row in rows:
        tag = row["dimensions"]["siteTag"]
        entry = by_tag.setdefault(
            tag, {"site_tag": tag, "host": row["dimensions"]["requestHost"], "visits": 0}
        )
        entry["visits"] += row["sum"]["visits"]
    return sorted(by_tag.values(), key=lambda e: -e["visits"])
