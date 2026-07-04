"""Public route tests: /graph, /embed, /healthz, cache, rate limit."""
from datetime import date, timedelta


def days_ago(n):
    return (date.today() - timedelta(days=n)).isoformat()


def seed(storage):
    # relative dates: the default look window is 30d, so fixed dates would
    # silently age out of every graph
    storage.upsert_points(
        [
            ("github", "zubrafex/braggraphs", "stars", "cumulative", 100, days_ago(45)),
            ("github", "zubrafex/braggraphs", "stars", "cumulative", 150, days_ago(15)),
            ("github", "zubrafex/braggraphs", "stars", "cumulative", 210, days_ago(1)),
            ("ga4", "zubrafex.com", "pageviews", "flow", 900, days_ago(1)),
        ]
    )


def test_graph_svg(client, storage):
    seed(storage)
    r = client.get("/graph/zubrafex/braggraphs/stars.svg")
    assert r.status_code == 200
    assert r.mimetype == "image/svg+xml"
    body = r.get_data(as_text=True)
    assert body.startswith("<svg")
    assert "210" in body


def test_default_window_is_30_days(client, storage):
    seed(storage)
    # default: the 45-day-old point falls outside the window...
    body = client.get("/graph/zubrafex/braggraphs/stars.svg").get_data(as_text=True)
    assert days_ago(45) not in body
    assert days_ago(15) in body  # first visible point
    # ...but period=all still shows full history
    body = client.get("/graph/zubrafex/braggraphs/stars.svg?period=all").get_data(
        as_text=True
    )
    assert days_ago(45) in body


def test_graph_ga4_reserved_prefix(client, storage):
    seed(storage)
    r = client.get("/graph/ga4/zubrafex.com/pageviews.svg")
    assert r.status_code == 200
    # flow readout defaults to the trailing 30-day sum
    assert "/30d" in r.get_data(as_text=True)


def test_flow_window_param(client, storage):
    seed(storage)
    # window=0 falls back to the single last-day figure with the /day unit
    body = client.get("/graph/ga4/zubrafex.com/pageviews.svg?window=0").get_data(
        as_text=True
    )
    assert "/day" in body
    body = client.get("/graph/ga4/zubrafex.com/pageviews.svg?window=7").get_data(
        as_text=True
    )
    assert "/7d" in body


def test_graph_cloudflare_reserved_prefix(client, storage):
    storage.upsert_point("cloudflare", "zubrafex.com", "visits", "flow", 512, days_ago(1))
    r = client.get("/graph/cloudflare/zubrafex.com/visits.svg")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "/30d" in body and "512" in body
    # same label under a different source is a different series
    assert client.get("/graph/cloudflare/zubrafex.com/pageviews.svg").status_code == 404


def test_graph_params(client, storage):
    seed(storage)
    r = client.get("/graph/zubrafex/braggraphs/stars.svg?w=600&h=200&theme=dark&period=90d")
    body = r.get_data(as_text=True)
    assert 'width="600"' in body and 'height="200"' in body
    assert "#0d1117" in body  # dark bg
    # nonsense params fall back to defaults instead of erroring
    r = client.get("/graph/zubrafex/braggraphs/stars.svg?w=zzz&theme=neon&period=5y")
    assert r.status_code == 200
    assert 'width="400"' in r.get_data(as_text=True)


def test_untracked_404(client, storage):
    seed(storage)
    assert client.get("/graph/zubrafex/braggraphs/open_issues.svg").status_code == 404
    assert client.get("/graph/nobody/nothing/stars.svg").status_code == 404
    assert client.get("/graph/ga4/other.com/pageviews.svg").status_code == 404


def test_tracked_but_empty_renders_placeholder(client):
    r = client.get("/graph/zubrafex/braggraphs/forks.svg")
    assert r.status_code == 200
    assert "no data yet" in r.get_data(as_text=True)


def test_cache_hit(app, client, storage, monkeypatch):
    seed(storage)
    app.config["BG_CACHE_TTL"] = 300
    calls = {"n": 0}
    import web.public as public

    real = public.render_chart

    def counting(*a, **kw):
        calls["n"] += 1
        return real(*a, **kw)

    monkeypatch.setattr(public, "render_chart", counting)
    assert client.get("/graph/zubrafex/braggraphs/stars.svg").status_code == 200
    assert client.get("/graph/zubrafex/braggraphs/stars.svg").status_code == 200
    assert calls["n"] == 1
    # different params → different cache entry
    assert client.get("/graph/zubrafex/braggraphs/stars.svg?theme=dark").status_code == 200
    assert calls["n"] == 2


def test_cache_ignores_unknown_params(app, client, storage, monkeypatch):
    """?x=1 / ?x=2 / bare URL must share one cache entry — no cache stuffing
    via junk query args."""
    seed(storage)
    app.config["BG_CACHE_TTL"] = 300
    calls = {"n": 0}
    import web.public as public

    real = public.render_chart

    def counting(*a, **kw):
        calls["n"] += 1
        return real(*a, **kw)

    monkeypatch.setattr(public, "render_chart", counting)
    base = "/graph/zubrafex/braggraphs/stars.svg"
    assert client.get(base).status_code == 200
    assert client.get(base + "?x=1").status_code == 200
    assert client.get(base + "?x=2&junk=yes").status_code == 200
    assert calls["n"] == 1
    assert len(public._cache) == 1


def test_cache_hard_cap(app, client, storage):
    """Live (unexpired) entries must never grow the cache past the cap."""
    seed(storage)
    app.config["BG_CACHE_TTL"] = 300
    import web.public as public

    cap = 50
    orig_max = public.CACHE_MAX_ENTRIES
    public.CACHE_MAX_ENTRIES = cap
    try:
        # distinct *legit* params (accent) → distinct live entries
        for i in range(cap * 2):
            r = client.get(f"/graph/zubrafex/braggraphs/stars.svg?accent={i:06x}")
            assert r.status_code == 200
            assert len(public._cache) <= cap
    finally:
        public.CACHE_MAX_ENTRIES = orig_max
        public._cache.clear()


def test_rate_limit(app, client, storage):
    seed(storage)
    app.config["BG_RATE_LIMIT"] = 5
    for _ in range(5):
        assert client.get("/graph/zubrafex/braggraphs/stars.svg").status_code == 200
    r = client.get("/graph/zubrafex/braggraphs/stars.svg")
    assert r.status_code == 429


def test_embed(client, storage):
    seed(storage)
    r = client.get("/embed/zubrafex/braggraphs/stars?theme=dark")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "/graph/zubrafex/braggraphs/stars.svg?" in html
    assert "theme=dark" in html
    assert "powered by braggraphs" in html
    assert client.get("/embed/nobody/nothing/stars").status_code == 404


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    data = r.get_json()
    assert data["status"] == "ok"
    assert data["db"] is True
    assert data["last_fetch_at"] is None
