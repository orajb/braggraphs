from datetime import datetime, timedelta, timezone

from core.config import AppConfig
from core.connector_base import Connector, Point
from core.scheduler import SchedulerService
from core.storage import Storage

T0 = datetime(2026, 7, 1, 6, 0, tzinfo=timezone.utc)

CONFIG = AppConfig.model_validate(
    {
        "github": {
            "repos": [
                {"owner": "a", "repo": "one", "metrics": ["stars"]},
                {"owner": "a", "repo": "two", "metrics": ["stars"]},
            ]
        }
    }
)


class StubConnector(Connector):
    name = "github"

    def __init__(self, fail_projects=()):
        self.fail_projects = set(fail_projects)
        self.fetch_calls = []
        self.backfill_calls = []
        self.value = 10

    def fetch(self, item):
        self.fetch_calls.append(item.project)
        if item.project in self.fail_projects:
            raise RuntimeError("api down")
        return [Point(item.project, "stars", "cumulative", self.value, "2026-07-01")]

    def backfill(self, item):
        self.backfill_calls.append(item.project)
        return [Point(item.project, "stars", "cumulative", 5, "2026-06-01")]


def make(tmp_path, connector):
    storage = Storage(tmp_path / "t.db")
    return SchedulerService(CONFIG, storage, {"github": connector}), storage


def test_first_run_backfills_then_fetches(tmp_path):
    conn = StubConnector()
    svc, storage = make(tmp_path, conn)
    svc.run_tick(now=T0)
    assert conn.backfill_calls == ["a/one", "a/two"]
    assert storage.get_series("github", "a/one", "stars") == [
        ("2026-06-01", 5.0),
        ("2026-07-01", 10.0),
    ]
    st = storage.get_status("github", "a/one")
    assert st["consecutive_failures"] == 0
    assert st["last_success_at"] == T0.isoformat()


def test_not_due_items_skipped_and_no_duplicate_rows(tmp_path):
    conn = StubConnector()
    svc, storage = make(tmp_path, conn)
    svc.run_tick(now=T0)
    svc.run_tick(now=T0 + timedelta(hours=1))  # nothing due yet
    assert conn.fetch_calls == ["a/one", "a/two"]
    svc.run_tick(now=T0 + timedelta(hours=25))  # daily slot passed
    assert conn.fetch_calls == ["a/one", "a/two", "a/one", "a/two"]
    assert conn.backfill_calls == ["a/one", "a/two"]  # backfill ran once only
    assert len(storage.get_series("github", "a/one", "stars")) == 2  # upserted


def test_backoff_progression_and_reset(tmp_path):
    conn = StubConnector(fail_projects={"a/one"})
    svc, storage = make(tmp_path, conn)

    svc.run_tick(now=T0)
    st = storage.get_status("github", "a/one")
    assert st["consecutive_failures"] == 1
    assert st["next_run_at"] == (T0 + timedelta(hours=1)).isoformat()
    assert "api down" in st["last_error"]

    t1 = T0 + timedelta(hours=1)
    svc.run_tick(now=t1)
    st = storage.get_status("github", "a/one")
    assert st["consecutive_failures"] == 2
    assert st["next_run_at"] == (t1 + timedelta(hours=4)).isoformat()

    t2 = t1 + timedelta(hours=4)
    svc.run_tick(now=t2)
    st = storage.get_status("github", "a/one")
    assert st["consecutive_failures"] == 3
    assert st["next_run_at"] == (t2 + timedelta(hours=24)).isoformat()

    # stays capped at 24h
    t3 = t2 + timedelta(hours=24)
    svc.run_tick(now=t3)
    assert storage.get_status("github", "a/one")["next_run_at"] == (
        t3 + timedelta(hours=24)
    ).isoformat()

    # recovery resets the backoff
    conn.fail_projects.clear()
    t4 = t3 + timedelta(hours=24)
    svc.run_tick(now=t4)
    st = storage.get_status("github", "a/one")
    assert st["consecutive_failures"] == 0
    assert st["last_error"] is None


def test_failure_isolation(tmp_path):
    conn = StubConnector(fail_projects={"a/one"})
    svc, storage = make(tmp_path, conn)
    svc.run_tick(now=T0)
    # a/two succeeded despite a/one blowing up
    assert storage.latest("github", "a/two", "stars") == ("2026-07-01", 10.0)
    assert storage.get_status("github", "a/two")["consecutive_failures"] == 0


def test_untracked_metrics_filtered(tmp_path):
    class NoisyConnector(StubConnector):
        def fetch(self, item):
            super().fetch(item)
            return [
                Point(item.project, "stars", "cumulative", 10, "2026-07-01"),
                Point(item.project, "forks", "cumulative", 2, "2026-07-01"),
            ]

    conn = NoisyConnector()
    svc, storage = make(tmp_path, conn)
    svc.run_tick(now=T0)
    assert storage.has_data("github", "a/one", "stars")
    assert not storage.has_data("github", "a/one", "forks")  # not configured


def test_fire_now_resets_schedule(tmp_path):
    conn = StubConnector()
    svc, storage = make(tmp_path, conn)
    svc.run_tick(now=T0)
    assert len(conn.fetch_calls) == 2
    svc.fire_now()  # everything becomes due immediately
    assert len(conn.fetch_calls) == 4
