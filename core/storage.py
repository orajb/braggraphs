"""SQLite storage: metric points + per-item fetch status.

`fetched_at` is stored as a UTC 'YYYY-MM-DD' string — daily granularity is the
design point. Writes upsert on (source, project, metric, fetched_at) so
backfill and refetch are idempotent: re-running any range rewrites the same
rows instead of duplicating them.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS metrics (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    project TEXT NOT NULL,
    metric TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'cumulative',
    value REAL NOT NULL,
    fetched_at TIMESTAMP NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_metric_lookup
    ON metrics(source, project, metric, fetched_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_metric_unique
    ON metrics(source, project, metric, fetched_at);

CREATE TABLE IF NOT EXISTS fetch_status (
    source TEXT NOT NULL,
    project TEXT NOT NULL,
    last_attempt_at TEXT,
    last_success_at TEXT,
    last_error TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    next_run_at TEXT,
    PRIMARY KEY (source, project)
);
"""


class Storage:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    # --- metric points ---

    def upsert_point(
        self, source: str, project: str, metric: str, kind: str, value: float, date: str
    ) -> None:
        self.upsert_points([(source, project, metric, kind, value, date)])

    def upsert_points(self, rows) -> None:
        """rows: iterable of (source, project, metric, kind, value, date)."""
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO metrics (source, project, metric, kind, value, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (source, project, metric, fetched_at)
                DO UPDATE SET value = excluded.value, kind = excluded.kind
                """,
                list(rows),
            )

    def get_series(
        self, source: str, project: str, metric: str, since: str | None = None
    ) -> list[tuple[str, float]]:
        """Ordered (date, value) points, optionally from `since` (inclusive)."""
        q = (
            "SELECT fetched_at, value FROM metrics"
            " WHERE source = ? AND project = ? AND metric = ?"
        )
        params: list = [source, project, metric]
        if since:
            q += " AND fetched_at >= ?"
            params.append(since)
        q += " ORDER BY fetched_at"
        with self._connect() as conn:
            return [(r["fetched_at"], r["value"]) for r in conn.execute(q, params)]

    def latest(self, source: str, project: str, metric: str) -> tuple[str, float] | None:
        with self._connect() as conn:
            r = conn.execute(
                "SELECT fetched_at, value FROM metrics"
                " WHERE source = ? AND project = ? AND metric = ?"
                " ORDER BY fetched_at DESC LIMIT 1",
                (source, project, metric),
            ).fetchone()
        return (r["fetched_at"], r["value"]) if r else None

    def value_at_or_before(
        self, source: str, project: str, metric: str, date: str
    ) -> float | None:
        with self._connect() as conn:
            r = conn.execute(
                "SELECT value FROM metrics"
                " WHERE source = ? AND project = ? AND metric = ? AND fetched_at <= ?"
                " ORDER BY fetched_at DESC LIMIT 1",
                (source, project, metric, date),
            ).fetchone()
        return r["value"] if r else None

    def has_data(self, source: str, project: str, metric: str) -> bool:
        with self._connect() as conn:
            r = conn.execute(
                "SELECT 1 FROM metrics"
                " WHERE source = ? AND project = ? AND metric = ? LIMIT 1",
                (source, project, metric),
            ).fetchone()
        return r is not None

    # --- fetch status (drives scheduler backoff, admin settings, healthz) ---

    def get_status(self, source: str, project: str) -> dict | None:
        with self._connect() as conn:
            r = conn.execute(
                "SELECT * FROM fetch_status WHERE source = ? AND project = ?",
                (source, project),
            ).fetchone()
        return dict(r) if r else None

    def all_statuses(self) -> list[dict]:
        with self._connect() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM fetch_status ORDER BY source, project"
                )
            ]

    def update_status(self, source: str, project: str, **fields) -> None:
        allowed = {
            "last_attempt_at",
            "last_success_at",
            "last_error",
            "consecutive_failures",
            "next_run_at",
        }
        bad = set(fields) - allowed
        if bad:
            raise ValueError(f"unknown fetch_status fields: {bad}")
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO fetch_status (source, project) VALUES (?, ?)",
                (source, project),
            )
            if fields:
                sets = ", ".join(f"{k} = ?" for k in fields)
                conn.execute(
                    f"UPDATE fetch_status SET {sets} WHERE source = ? AND project = ?",
                    [*fields.values(), source, project],
                )

    def last_fetch_at(self) -> str | None:
        """Most recent successful fetch across all items (for /healthz)."""
        with self._connect() as conn:
            r = conn.execute(
                "SELECT MAX(last_success_at) AS m FROM fetch_status"
            ).fetchone()
        return r["m"]
