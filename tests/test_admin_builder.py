"""Theme switcher + embed builder in the admin."""


def login(client):
    return client.post("/admin/login", data={"password": "test-password"})


def test_dashboard_theme_switcher(client):
    login(client)
    html = client.get("/admin/").get_data(as_text=True)
    for name in ("light", "dark", "pine", "oat", "midnight"):
        assert name in html
    html = client.get("/admin/?theme=pine").get_data(as_text=True)
    assert "stars.svg?theme=pine" in html
    # bogus theme falls back to default URLs
    html = client.get("/admin/?theme=bogus").get_data(as_text=True)
    assert "stars.svg?theme=bogus" not in html
    assert "stars.svg" in html


def test_builder_page(client):
    login(client)
    r = client.get("/admin/builder")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "/graph/zubrafex/braggraphs/stars.svg" in html
    assert "/graph/ga4/zubrafex.com/pageviews.svg" in html
    assert "sparkline 120×32" in html
    assert "micro 80×20" in html
    assert 'data-theme="terminal"' in html
    assert "Embed tag" in html


def test_builder_requires_auth(client):
    r = client.get("/admin/builder")
    assert r.status_code == 302
    assert "/admin/login" in r.headers["Location"]
