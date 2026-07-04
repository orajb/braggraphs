"""GitHub connector — code traction: stars, forks, open issues, weekly commits.

Auth: personal access token (GITHUB_PAT). Requests are throttled to 1/sec to
be polite; a daily fetch of dozens of repos is far under the 5,000 req/hour
PAT limit either way.

Star backfill uses the stargazers API with the `star+json` media type, which
returns a `starred_at` timestamp per stargazer — true full history (unlike the
~90-day events window). It's paginated at 100/page and capped at 40,000 stars;
repos over the cap skip backfill and build history from today (best-effort per
spec).
"""
from __future__ import annotations

import logging
import math
import time
from collections import Counter
from datetime import datetime, timedelta, timezone

import requests

from core.connector_base import Connector, Point, utc_today

log = logging.getLogger(__name__)

API_BASE = "https://api.github.com"

HINTS = {
    401: " — is GITHUB_PAT set to a valid token?",
    403: " — rate-limited or the PAT lacks scope (public_repo is enough for public repos)",
    404: " — repo not found (private repo needs a PAT with repo scope; check owner/repo spelling)",
}


class GitHubError(Exception):
    pass


class GitHubConnector(Connector):
    name = "github"

    SIMPLE_METRICS = {
        "stars": "stargazers_count",
        "forks": "forks_count",
        "open_issues": "open_issues_count",
    }

    def __init__(
        self,
        token: str,
        session: requests.Session | None = None,
        sleep=time.sleep,
        throttle_seconds: float = 1.0,
        page_size: int = 100,
        max_backfill_stars: int = 40_000,
    ):
        self._token = token
        self._session = session or requests.Session()
        self._sleep = sleep
        self._throttle = throttle_seconds
        self._page_size = page_size
        self._max_backfill_stars = max_backfill_stars

    def _get(self, path: str, params=None, accept="application/vnd.github+json"):
        self._sleep(self._throttle)
        r = self._session.get(
            API_BASE + path,
            params=params,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": accept,
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=30,
        )
        if r.status_code >= 400:
            raise GitHubError(
                f"GitHub API returned {r.status_code} for {path}"
                f"{HINTS.get(r.status_code, '')}"
            )
        return r

    def fetch(self, item) -> list[Point]:
        today = utc_today()
        data = self._get(f"/repos/{item.owner}/{item.repo}").json()
        points = [
            Point(item.project, metric, "cumulative", float(data[field]), today)
            for metric, field in self.SIMPLE_METRICS.items()
            if metric in item.metrics
        ]
        if "commits_weekly" in item.metrics:
            points += self._weekly_commits(item)
        return points

    def _weekly_commits(self, item) -> list[Point]:
        r = self._get(f"/repos/{item.owner}/{item.repo}/stats/participation")
        if r.status_code == 202:
            # GitHub is still computing stats; tomorrow's fetch will get them.
            log.info("participation stats for %s not ready yet", item.project)
            return []
        weeks = r.json().get("all") or []
        if len(weeks) < 2:
            return []
        # Weeks run Sunday–Saturday, oldest first, last entry = current partial
        # week (dropped — plotting a partial week would always dip the graph).
        today = datetime.now(timezone.utc).date()
        current_week_start = today - timedelta(days=(today.weekday() + 1) % 7)
        n = len(weeks)
        return [
            Point(
                item.project,
                "commits_weekly",
                "flow",
                float(count),
                (current_week_start - timedelta(weeks=n - 1 - i)).isoformat(),
            )
            for i, count in enumerate(weeks[:-1])
        ]

    def backfill(self, item) -> list[Point]:
        if "stars" not in item.metrics:
            return []
        data = self._get(f"/repos/{item.owner}/{item.repo}").json()
        total = data.get("stargazers_count") or 0
        if total == 0:
            return []
        if total > self._max_backfill_stars:
            log.warning(
                "%s has %d stars — over the %d backfill cap, history will build from today",
                item.project, total, self._max_backfill_stars,
            )
            return []

        dates: list[str] = []
        max_pages = math.ceil(self._max_backfill_stars / self._page_size)
        for page in range(1, max_pages + 1):
            batch = self._get(
                f"/repos/{item.owner}/{item.repo}/stargazers",
                params={"per_page": self._page_size, "page": page},
                accept="application/vnd.github.star+json",
            ).json()
            if not isinstance(batch, list) or not batch:
                break
            dates += [e["starred_at"][:10] for e in batch if e.get("starred_at")]
            if len(batch) < self._page_size:
                break

        buckets = Counter(dates)
        points, running = [], 0
        for d in sorted(buckets):
            running += buckets[d]
            points.append(Point(item.project, "stars", "cumulative", float(running), d))
        return points
