from core.storage import Storage


def make_storage(tmp_path):
    return Storage(tmp_path / "test.db")


def test_upsert_idempotent(tmp_path):
    s = make_storage(tmp_path)
    s.upsert_point("github", "a/b", "stars", "cumulative", 10, "2026-07-01")
    s.upsert_point("github", "a/b", "stars", "cumulative", 12, "2026-07-01")
    series = s.get_series("github", "a/b", "stars")
    assert series == [("2026-07-01", 12.0)]


def test_series_ordering_and_since(tmp_path):
    s = make_storage(tmp_path)
    s.upsert_points(
        [
            ("github", "a/b", "stars", "cumulative", 3, "2026-06-30"),
            ("github", "a/b", "stars", "cumulative", 1, "2026-06-28"),
            ("github", "a/b", "stars", "cumulative", 2, "2026-06-29"),
        ]
    )
    assert [d for d, _ in s.get_series("github", "a/b", "stars")] == [
        "2026-06-28",
        "2026-06-29",
        "2026-06-30",
    ]
    assert s.get_series("github", "a/b", "stars", since="2026-06-29") == [
        ("2026-06-29", 2.0),
        ("2026-06-30", 3.0),
    ]


def test_series_isolated_by_key(tmp_path):
    s = make_storage(tmp_path)
    s.upsert_point("github", "a/b", "stars", "cumulative", 5, "2026-07-01")
    s.upsert_point("github", "a/b", "forks", "cumulative", 2, "2026-07-01")
    s.upsert_point("ga4", "site.com", "pageviews", "flow", 900, "2026-07-01")
    assert s.get_series("github", "a/b", "stars") == [("2026-07-01", 5.0)]
    assert s.latest("ga4", "site.com", "pageviews") == ("2026-07-01", 900.0)
    assert not s.has_data("github", "a/b", "open_issues")
    assert s.has_data("github", "a/b", "forks")


def test_value_at_or_before(tmp_path):
    s = make_storage(tmp_path)
    s.upsert_points(
        [
            ("github", "a/b", "stars", "cumulative", 1, "2026-06-01"),
            ("github", "a/b", "stars", "cumulative", 5, "2026-06-20"),
        ]
    )
    assert s.value_at_or_before("github", "a/b", "stars", "2026-06-25") == 5.0
    assert s.value_at_or_before("github", "a/b", "stars", "2026-06-10") == 1.0
    assert s.value_at_or_before("github", "a/b", "stars", "2026-05-01") is None


def test_fetch_status_roundtrip(tmp_path):
    s = make_storage(tmp_path)
    assert s.get_status("github", "a/b") is None
    assert s.last_fetch_at() is None

    s.update_status(
        "github",
        "a/b",
        last_attempt_at="2026-07-01T06:00:00+00:00",
        last_error="boom",
        consecutive_failures=1,
        next_run_at="2026-07-01T07:00:00+00:00",
    )
    st = s.get_status("github", "a/b")
    assert st["last_error"] == "boom"
    assert st["consecutive_failures"] == 1

    s.update_status(
        "github",
        "a/b",
        last_success_at="2026-07-01T07:05:00+00:00",
        last_error=None,
        consecutive_failures=0,
    )
    st = s.get_status("github", "a/b")
    assert st["last_error"] is None
    assert st["last_success_at"] == "2026-07-01T07:05:00+00:00"
    assert s.last_fetch_at() == "2026-07-01T07:05:00+00:00"
    assert len(s.all_statuses()) == 1
