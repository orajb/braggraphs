"""Admin dashboard tests: auth, tiles, settings, fetch-now."""
import threading


def login(client, password="test-password"):
    return client.post("/admin/login", data={"password": password})


def test_unauthenticated_redirects_to_login(client):
    r = client.get("/admin/")
    assert r.status_code == 302
    assert "/admin/login" in r.headers["Location"]
    r = client.get("/admin/settings")
    assert r.status_code == 302


def test_wrong_password_rejected(client):
    r = login(client, "nope")
    assert r.status_code == 200
    assert "Wrong password" in r.get_data(as_text=True)
    assert client.get("/admin/").status_code == 302  # still locked out


def test_login_logout_flow(client):
    r = login(client)
    assert r.status_code == 302
    assert client.get("/admin/").status_code == 200
    client.post("/admin/logout")
    assert client.get("/admin/").status_code == 302


def test_dashboard_tiles(client, storage):
    storage.upsert_points(
        [
            ("github", "zubrafex/braggraphs", "stars", "cumulative", 100, "2026-06-20"),
            ("github", "zubrafex/braggraphs", "stars", "cumulative", 150, "2026-06-30"),
        ]
    )
    login(client)
    html = client.get("/admin/").get_data(as_text=True)
    # one tile per tracked (project, metric):
    # stars, forks, commits_weekly, ga4 pageviews, cloudflare visits
    assert html.count('<img src="/graph/') == 5
    assert "/graph/zubrafex/braggraphs/stars.svg" in html
    assert "/graph/ga4/zubrafex.com/pageviews.svg" in html
    assert "/graph/cloudflare/zubrafex.com/visits.svg" in html
    assert "150" in html


def test_settings_shows_tracked_and_status(client, storage):
    storage.update_status(
        "github", "zubrafex/braggraphs",
        last_success_at="2026-07-01T06:00:00+00:00",
        last_error=None, consecutive_failures=0,
    )
    login(client)
    html = client.get("/admin/settings").get_data(as_text=True)
    assert "zubrafex/braggraphs" in html
    assert "2026-07-01T06:00:00+00:00" in html
    assert "Fetch now" in html


def test_fetch_now_triggers_scheduler(app, client):
    fired = threading.Event()

    class StubScheduler:
        def fire_now(self):
            fired.set()

    app.extensions["bg_scheduler"] = StubScheduler()
    login(client)
    r = client.post("/admin/fetch")
    assert r.status_code == 200
    assert "Fetch started" in r.get_data(as_text=True)
    assert fired.wait(timeout=2)


def test_fetch_now_without_scheduler_503(client):
    login(client)
    assert client.post("/admin/fetch").status_code == 503


def test_admin_disabled_not_registered(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_PAT", "x")
    config = tmp_path / "config.yml"
    config.write_text(
        "github:\n  repos:\n    - {owner: a, repo: b, metrics: [stars]}\n"
        "admin:\n  enabled: false\n"
    )
    from app import create_app

    app = create_app(config_path=config, data_dir=tmp_path / "data")
    client = app.test_client()
    assert client.get("/admin/").status_code == 404
