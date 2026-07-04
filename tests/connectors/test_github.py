import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import responses

from connectors.github import API_BASE, GitHubConnector, GitHubError
from core.config import GitHubRepo
from core.connector_base import utc_today

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name):
    return json.loads((FIXTURES / name).read_text())


def make_connector(**kw):
    kw.setdefault("sleep", lambda s: None)
    return GitHubConnector("test-token", **kw)


ITEM = GitHubRepo(owner="zubrafex", repo="braggraphs", metrics=["stars", "forks", "commits_weekly"])
REPO_URL = f"{API_BASE}/repos/zubrafex/braggraphs"


@responses.activate
def test_fetch_maps_fixture_to_points():
    responses.get(REPO_URL, json=fixture("github_repo.json"))
    responses.get(f"{REPO_URL}/stats/participation", json=fixture("github_participation.json"))

    points = make_connector().fetch(ITEM)
    today = utc_today()
    by_metric = {}
    for p in points:
        by_metric.setdefault(p.metric, []).append(p)

    assert by_metric["stars"][0].value == 250
    assert by_metric["stars"][0].kind == "cumulative"
    assert by_metric["stars"][0].date == today
    assert by_metric["forks"][0].value == 12
    assert "open_issues" not in by_metric  # not in item.metrics

    # participation has 5 weeks; the current partial week is dropped
    commits = sorted(by_metric["commits_weekly"], key=lambda p: p.date)
    assert [p.value for p in commits] == [2, 5, 0, 7]
    assert all(p.kind == "flow" for p in commits)
    today_d = datetime.now(timezone.utc).date()
    week_start = today_d - timedelta(days=(today_d.weekday() + 1) % 7)
    assert commits[-1].date == (week_start - timedelta(weeks=1)).isoformat()


@responses.activate
def test_fetch_participation_202_skips_commits():
    responses.get(REPO_URL, json=fixture("github_repo.json"))
    responses.get(f"{REPO_URL}/stats/participation", status=202)

    points = make_connector().fetch(ITEM)
    metrics = {p.metric for p in points}
    assert "stars" in metrics and "commits_weekly" not in metrics


@responses.activate
def test_backfill_builds_cumulative_series():
    responses.get(REPO_URL, json=fixture("github_repo.json"))
    responses.get(f"{REPO_URL}/stargazers", json=fixture("github_stargazers_p1.json"))
    responses.get(f"{REPO_URL}/stargazers", json=fixture("github_stargazers_p2.json"))

    points = make_connector(page_size=3).backfill(ITEM)
    assert [(p.date, p.value) for p in points] == [
        ("2026-06-01", 2.0),
        ("2026-06-02", 3.0),
        ("2026-06-03", 4.0),
        ("2026-06-05", 5.0),
    ]
    assert all(p.kind == "cumulative" and p.metric == "stars" for p in points)
    # star+json media type requested
    assert (
        responses.calls[1].request.headers["Accept"] == "application/vnd.github.star+json"
    )


@responses.activate
def test_backfill_skipped_over_star_cap():
    responses.get(REPO_URL, json=fixture("github_repo.json"))  # 250 stars
    points = make_connector(max_backfill_stars=100).backfill(ITEM)
    assert points == []
    assert len(responses.calls) == 1  # never touched /stargazers


def test_backfill_skipped_when_stars_untracked():
    item = GitHubRepo(owner="zubrafex", repo="braggraphs", metrics=["forks"])
    assert make_connector().backfill(item) == []


@responses.activate
def test_error_raises_with_hint():
    responses.get(REPO_URL, status=404)
    with pytest.raises(GitHubError, match="404.*not found"):
        make_connector().fetch(ITEM)


@responses.activate
def test_auth_header_and_throttle():
    responses.get(REPO_URL, json=fixture("github_repo.json"))
    sleeps = []
    conn = GitHubConnector("test-token", sleep=sleeps.append, throttle_seconds=1.0)
    conn.fetch(GitHubRepo(owner="zubrafex", repo="braggraphs", metrics=["stars"]))
    assert responses.calls[0].request.headers["Authorization"] == "Bearer test-token"
    assert sleeps == [1.0]
