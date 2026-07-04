import responses

from core import doctor


CONFIG = """
github:
  repos:
    - owner: zubrafex
      repo: braggraphs
      metrics: [stars]

ga4:
  properties:
    - label: zubrafex.com
      property_id: "123456789"
      metrics: [pageviews]

admin:
  enabled: true
"""


def write_config(tmp_path, text=CONFIG):
    p = tmp_path / "config.yml"
    p.write_text(text)
    return str(p)


def by_label(checks):
    return {label: (ok, detail) for label, ok, detail in checks}


def test_invalid_config_short_circuits(tmp_path):
    checks = doctor.run_checks(write_config(tmp_path, "admin: {enabled: true}\n"))
    assert len(checks) == 1
    assert checks[0][1] is False


def test_missing_secrets_reported(tmp_path, monkeypatch):
    for var in ("GITHUB_PAT", "GOOGLE_APPLICATION_CREDENTIALS", "BRAGGRAPHS_ADMIN_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("BRAGGRAPHS_DATA_DIR", str(tmp_path / "data"))
    got = by_label(doctor.run_checks(write_config(tmp_path)))
    assert got["GITHUB_PAT set"][0] is False
    assert got["GOOGLE_APPLICATION_CREDENTIALS set"][0] is False
    assert got["BRAGGRAPHS_ADMIN_PASSWORD set"][0] is False
    assert got[f"data dir writable ({tmp_path / 'data'})"][0] is True


@responses.activate
def test_all_green(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_PAT", "tok")
    monkeypatch.setenv("BRAGGRAPHS_ADMIN_PASSWORD", "pw")
    key = tmp_path / "key.json"
    key.write_text("{}")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(key))
    monkeypatch.setenv("BRAGGRAPHS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(
        "connectors.ga4.list_properties",
        lambda: [{"property_id": "123456789", "display_name": "zubrafex.com", "account": "a"}],
    )
    responses.get(
        f"{doctor.GITHUB_API}/rate_limit",
        json={"resources": {"core": {"remaining": 4999}}},
    )
    responses.get(f"{doctor.GITHUB_API}/repos/zubrafex/braggraphs", json={"id": 1})

    checks = doctor.run_checks(write_config(tmp_path))
    assert all(ok for _, ok, _ in checks), checks
    got = by_label(checks)
    assert "4999" in got["GitHub token valid"][1]


@responses.activate
def test_bad_token_and_missing_repo(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_PAT", "expired")
    monkeypatch.setenv("BRAGGRAPHS_ADMIN_PASSWORD", "pw")
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.setenv("BRAGGRAPHS_DATA_DIR", str(tmp_path / "data"))
    responses.get(f"{doctor.GITHUB_API}/rate_limit", status=401)

    got = by_label(doctor.run_checks(write_config(tmp_path)))
    assert got["GitHub token valid"][0] is False
    assert "401" in got["GitHub token valid"][1]
    # repo checks are skipped when the token itself is dead
    assert "repo zubrafex/braggraphs reachable" not in got


@responses.activate
def test_repo_typo_flagged(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_PAT", "tok")
    monkeypatch.setenv("BRAGGRAPHS_ADMIN_PASSWORD", "pw")
    monkeypatch.setenv("BRAGGRAPHS_DATA_DIR", str(tmp_path / "data"))
    config = write_config(
        tmp_path,
        "github:\n  repos:\n    - owner: zubrafex\n      repo: typo\n      metrics: [stars]\n",
    )
    responses.get(
        f"{doctor.GITHUB_API}/rate_limit",
        json={"resources": {"core": {"remaining": 5000}}},
    )
    responses.get(f"{doctor.GITHUB_API}/repos/zubrafex/typo", status=404)

    got = by_label(doctor.run_checks(config))
    assert got["repo zubrafex/typo reachable"][0] is False
    assert "404" in got["repo zubrafex/typo reachable"][1]


def test_cloudflare_checks(tmp_path, monkeypatch):
    monkeypatch.setenv("BRAGGRAPHS_ADMIN_PASSWORD", "pw")
    monkeypatch.setenv("BRAGGRAPHS_DATA_DIR", str(tmp_path / "data"))
    config = write_config(
        tmp_path,
        'cloudflare:\n  account_id: "abcdef0123456789abcdef0123456789"\n'
        "  sites:\n"
        '    - label: mysite\n      site_tag: "0123456789abcdef0123456789abcdef"\n'
        "      metrics: [visits]\n"
        '    - label: silent\n      site_tag: "fedcba9876543210fedcba9876543210"\n'
        "      metrics: [visits]\n",
    )
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    got = by_label(doctor.run_checks(config))
    assert got["CLOUDFLARE_API_TOKEN set"][0] is False

    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "tok")
    monkeypatch.setattr(
        "connectors.cloudflare.list_sites",
        lambda token, account_id, **kw: [
            {"site_tag": "0123456789abcdef0123456789abcdef", "host": "mysite", "visits": 10}
        ],
    )
    got = by_label(doctor.run_checks(config))
    assert got["Cloudflare token + account work"][0] is True
    assert got["Cloudflare site mysite has recent data"][0] is True
    assert got["Cloudflare site silent has recent data"][0] is False
    assert "Web Analytics enabled" in got["Cloudflare site silent has recent data"][1]


@responses.activate  # no responses registered: any network call would error
def test_offline_mode_skips_network(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_PAT", "tok")
    monkeypatch.setenv("BRAGGRAPHS_ADMIN_PASSWORD", "pw")
    key = tmp_path / "key.json"
    key.write_text("{}")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(key))
    monkeypatch.setenv("BRAGGRAPHS_DATA_DIR", str(tmp_path / "data"))

    checks = doctor.run_checks(write_config(tmp_path), offline=True)
    assert all(ok for _, ok, _ in checks), checks
    labels = {label for label, _, _ in checks}
    assert "GITHUB_PAT set" in labels
    assert "GA4 key file exists" in labels
    assert "GitHub token valid" not in labels  # network check skipped
    assert "GA4 service account works" not in labels


def test_ga4_missing_viewer_grant(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_PAT", "tok")
    monkeypatch.setenv("BRAGGRAPHS_ADMIN_PASSWORD", "pw")
    key = tmp_path / "key.json"
    key.write_text("{}")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(key))
    monkeypatch.setenv("BRAGGRAPHS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr("connectors.ga4.list_properties", lambda: [])
    config = write_config(
        tmp_path,
        'ga4:\n  properties:\n    - label: x\n      property_id: "42"\n'
        "      metrics: [pageviews]\n",
    )
    got = by_label(doctor.run_checks(config))
    assert got["GA4 property x (42) accessible"][0] is False
    assert "Viewer" in got["GA4 property x (42) accessible"][1]
