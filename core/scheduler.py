"""Fetch orchestration: daily cadence, first-add backfill, backoff, isolation.

A single APScheduler job ticks hourly (plus once immediately at boot — graphs
shouldn't be empty on day one). The tick logic lives in `run_tick(now)` so
tests drive it directly with synthetic clocks and stub connectors.

Per-item failure isolation: one repo/property failing never blocks the rest.
Failed items retry on a 1h → 4h → 24h backoff, tracked in the fetch_status
table (which also feeds the admin settings page and /healthz).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from core.config import AppConfig
from core.connector_base import Connector
from core.storage import Storage

log = logging.getLogger(__name__)

BACKOFF_HOURS = [1, 4, 24]
DAILY = timedelta(hours=24)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


class SchedulerService:
    def __init__(self, config: AppConfig, storage: Storage, connectors: dict[str, Connector]):
        self.config = config
        self.storage = storage
        self.connectors = connectors
        self._scheduler = None

    def run_tick(self, now: datetime | None = None) -> None:
        now = now or _now()
        for source, item in self.config.items():
            connector = self.connectors.get(source)
            if connector is None:
                continue
            project = item.project
            status = self.storage.get_status(source, project) or {}
            next_run = status.get("next_run_at")
            if next_run and _parse(next_run) > now:
                continue
            self._run_item(connector, source, item, now)

    def _run_item(self, connector: Connector, source: str, item, now: datetime) -> None:
        project = item.project
        try:
            points = []
            if not any(
                self.storage.has_data(source, project, m) for m in item.metrics
            ):
                log.info("first fetch of %s/%s — backfilling", source, project)
                points += connector.backfill(item)
            points += connector.fetch(item)
            wanted = set(item.metrics)
            self.storage.upsert_points(
                (source, p.project, p.metric, p.kind, p.value, p.date)
                for p in points
                if p.metric in wanted
            )
            self.storage.update_status(
                source,
                project,
                last_attempt_at=now.isoformat(),
                last_success_at=now.isoformat(),
                last_error=None,
                consecutive_failures=0,
                next_run_at=(now + DAILY).isoformat(),
            )
            log.info("fetched %s/%s (%d points)", source, project, len(points))
        except Exception as e:  # noqa: BLE001 — isolation is the point
            status = self.storage.get_status(source, project) or {}
            failures = (status.get("consecutive_failures") or 0) + 1
            delay = timedelta(hours=BACKOFF_HOURS[min(failures - 1, len(BACKOFF_HOURS) - 1)])
            self.storage.update_status(
                source,
                project,
                last_attempt_at=now.isoformat(),
                last_error=str(e)[:500],
                consecutive_failures=failures,
                next_run_at=(now + delay).isoformat(),
            )
            log.error(
                "fetch failed for %s/%s (attempt %d, retrying in %s): %s",
                source, project, failures, delay, e,
            )

    def fire_now(self) -> None:
        """Admin 'fetch now': make every item due, then tick."""
        for source, item in self.config.items():
            self.storage.update_status(source, item.project, next_run_at=None)
        self.run_tick()

    def start(self) -> None:
        from apscheduler.schedulers.background import BackgroundScheduler

        self._scheduler = BackgroundScheduler(timezone="UTC")
        self._scheduler.add_job(
            self.run_tick,
            "interval",
            hours=1,
            next_run_time=datetime.now(timezone.utc),  # first-boot UX: run now
        )
        self._scheduler.start()


def build_scheduler(config: AppConfig, storage: Storage) -> SchedulerService:
    connectors: dict[str, Connector] = {}
    if config.github:
        from connectors.github import GitHubConnector

        connectors["github"] = GitHubConnector(os.environ["GITHUB_PAT"])
    if config.ga4:
        from connectors.ga4 import GA4Connector

        connectors["ga4"] = GA4Connector()
    if config.cloudflare:
        from connectors.cloudflare import CloudflareConnector

        connectors["cloudflare"] = CloudflareConnector(
            os.environ["CLOUDFLARE_API_TOKEN"], config.cloudflare.account_id
        )
    return SchedulerService(config, storage, connectors)
