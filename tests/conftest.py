import pytest

TEST_CONFIG = """
github:
  repos:
    - owner: zubrafex
      repo: braggraphs
      metrics: [stars, forks, commits_weekly]

ga4:
  properties:
    - label: zubrafex.com
      property_id: "123456789"
      metrics: [pageviews]

cloudflare:
  account_id: "abcdef0123456789abcdef0123456789"
  sites:
    - label: zubrafex.com
      site_tag: "0123456789abcdef0123456789abcdef"
      metrics: [visits]

admin:
  enabled: true

embed:
  powered_by: true
"""


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_PAT", "test-pat")
    monkeypatch.setenv("BRAGGRAPHS_ADMIN_PASSWORD", "test-password")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(tmp_path / "key.json"))
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "cf-test-token")
    config_path = tmp_path / "config.yml"
    config_path.write_text(TEST_CONFIG)

    from app import create_app

    app = create_app(config_path=config_path, data_dir=tmp_path / "data")
    app.config["TESTING"] = True
    # Tests exercise cache/rate-limit explicitly; keep them out of the way otherwise.
    app.config["BG_CACHE_TTL"] = 0
    app.config["BG_RATE_LIMIT"] = 100_000

    yield app

    from web import public

    public._cache.clear()
    public._hits.clear()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def storage(app):
    return app.extensions["bg_storage"]
