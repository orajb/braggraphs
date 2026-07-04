import os

import pytest

from core.config import ConfigError, check_secrets, load_config, load_dotenv


def write_config(tmp_path, text):
    p = tmp_path / "config.yml"
    p.write_text(text)
    return p


GOOD = """
github:
  cadence: daily
  repos:
    - owner: zubrafex
      repo: braggraphs
      metrics: [stars, forks, commits_weekly]

ga4:
  properties:
    - label: zubrafex.com
      property_id: "123456789"
      metrics: [pageviews, sessions]

admin:
  enabled: true
"""


def test_example_config_parses():
    config = load_config("config.yml.example")
    assert config.github is not None
    assert config.github.repos[0].project == "your-github-username/your-repo-name"
    assert config.admin.enabled
    assert config.embed.powered_by


def test_full_config(tmp_path):
    config = load_config(write_config(tmp_path, GOOD))
    assert ("github", "zubrafex/braggraphs", "stars") in config.tracked()
    assert ("ga4", "zubrafex.com", "pageviews") in config.tracked()
    sources = [s for s, _ in config.items()]
    assert sources == ["github", "ga4"]


def test_ga4_block_optional(tmp_path):
    config = load_config(
        write_config(
            tmp_path,
            "github:\n  repos:\n    - owner: a\n      repo: b\n      metrics: [stars]\n",
        )
    )
    assert config.ga4 is None


def test_unknown_github_metric_rejected(tmp_path):
    with pytest.raises(ConfigError, match="unknown github metrics"):
        load_config(
            write_config(
                tmp_path,
                "github:\n  repos:\n    - owner: a\n      repo: b\n      metrics: [mrr]\n",
            )
        )


def test_unknown_ga4_metric_rejected(tmp_path):
    with pytest.raises(ConfigError, match="unknown ga4 metrics"):
        load_config(
            write_config(
                tmp_path,
                'ga4:\n  properties:\n    - label: x\n      property_id: "1"\n'
                "      metrics: [bounce_rate]\n",
            )
        )


def test_empty_repos_rejected(tmp_path):
    with pytest.raises(ConfigError):
        load_config(write_config(tmp_path, "github:\n  repos: []\n"))


def test_cloudflare_block(tmp_path):
    config = load_config(
        write_config(
            tmp_path,
            'cloudflare:\n  account_id: "abcdef0123456789abcdef0123456789"\n'
            "  sites:\n"
            '    - label: example.com\n      site_tag: "0123456789abcdef0123456789abcdef"\n'
            "      metrics: [visits, pageviews]\n",
        )
    )
    assert ("cloudflare", "example.com", "visits") in config.tracked()


def test_cloudflare_validation(tmp_path):
    with pytest.raises(ConfigError, match="unknown cloudflare metrics"):
        load_config(
            write_config(
                tmp_path,
                'cloudflare:\n  account_id: "abcdef0123456789abcdef0123456789"\n'
                "  sites:\n"
                '    - label: x\n      site_tag: "0123456789abcdef0123456789abcdef"\n'
                "      metrics: [requests]\n",
            )
        )
    with pytest.raises(ConfigError, match="32-char hex"):
        load_config(
            write_config(
                tmp_path,
                'cloudflare:\n  account_id: "nope"\n  sites:\n'
                '    - label: x\n      site_tag: "0123456789abcdef0123456789abcdef"\n'
                "      metrics: [visits]\n",
            )
        )


def test_cloudflare_secret_required(tmp_path, monkeypatch):
    config = load_config(
        write_config(
            tmp_path,
            'cloudflare:\n  account_id: "abcdef0123456789abcdef0123456789"\n'
            "  sites:\n"
            '    - label: x\n      site_tag: "0123456789abcdef0123456789abcdef"\n'
            "      metrics: [visits]\n"
            "admin:\n  enabled: false\n",
        )
    )
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    with pytest.raises(ConfigError, match="CLOUDFLARE_API_TOKEN"):
        check_secrets(config)
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "t")
    check_secrets(config)  # no raise


def test_no_sources_rejected(tmp_path):
    with pytest.raises(ConfigError, match="no sources"):
        load_config(write_config(tmp_path, "admin:\n  enabled: true\n"))


def test_missing_file():
    with pytest.raises(ConfigError, match="not found"):
        load_config("does-not-exist.yml")


def test_check_secrets(tmp_path, monkeypatch):
    config = load_config(write_config(tmp_path, GOOD))
    for var in (
        "GITHUB_PAT",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "BRAGGRAPHS_ADMIN_PASSWORD",
    ):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(ConfigError) as e:
        check_secrets(config)
    msg = str(e.value)
    assert "GITHUB_PAT" in msg
    assert "GOOGLE_APPLICATION_CREDENTIALS" in msg
    assert "BRAGGRAPHS_ADMIN_PASSWORD" in msg

    monkeypatch.setenv("GITHUB_PAT", "x")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/k.json")
    monkeypatch.setenv("BRAGGRAPHS_ADMIN_PASSWORD", "pw")
    check_secrets(config)  # no raise


def test_load_dotenv(tmp_path, monkeypatch):
    monkeypatch.delenv("BG_TEST_A", raising=False)
    monkeypatch.delenv("BG_TEST_B", raising=False)
    monkeypatch.setenv("BG_TEST_C", "already-set")
    env = tmp_path / ".env"
    env.write_text(
        "# comment\n"
        "\n"
        "BG_TEST_A=hello\n"
        "BG_TEST_B='quoted value'\n"
        "BG_TEST_C=from-file\n"
        "not a valid line\n"
    )
    loaded = load_dotenv(env)
    assert loaded == 2
    assert os.environ["BG_TEST_A"] == "hello"
    assert os.environ["BG_TEST_B"] == "quoted value"
    assert os.environ["BG_TEST_C"] == "already-set"  # real env wins
    monkeypatch.delenv("BG_TEST_A")
    monkeypatch.delenv("BG_TEST_B")


def test_load_dotenv_missing_file(tmp_path):
    assert load_dotenv(tmp_path / "nope.env") == 0
