"""Connector interface — sources are single-file additions under connectors/.

Connectors return dated Points rather than bare current values: backfill and
flow metrics (per-day pageviews, per-week commits) need a date attached, and
the storage layer's date-keyed upsert makes re-fetching any range idempotent.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class Point:
    project: str  # 'owner/repo' or a GA4 property label
    metric: str
    kind: str  # 'cumulative' | 'flow'
    value: float
    date: str  # UTC 'YYYY-MM-DD' the value belongs to


def utc_today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


class Connector(ABC):
    name: str
    default_cadence: str = "daily"

    @abstractmethod
    def fetch(self, item) -> list[Point]:
        """Fetch current values for one configured item (repo / property)."""

    def backfill(self, item) -> list[Point]:
        """Best-effort historic seed, run once when an item has no data yet."""
        return []
