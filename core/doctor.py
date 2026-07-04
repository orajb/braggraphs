"""Preflight checks — verify config, secrets, and live API access *before*
launching, so onboarding failures name themselves instead of surfacing as
empty graphs later.

Run locally:      python -m core.doctor
Run via Docker:   docker compose run --rm braggraphs python -m core.doctor

Exit code 0 = everything green (warnings allowed), 1 = at least one failure.
Each check prints a one-line fix hint on failure. Secrets are never printed.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import requests

from core.config import AppConfig, ConfigError, load_config

GITHUB_API = "https://api.github.com"

# a check result: (label, ok, hint-or-detail)
Check = tuple[str, bool, str]


def _github_headers() -> dict:
    return {
        "Authorization": f"Bearer {os.environ['GITHUB_PAT']}",
        "Accept": "application/vnd.github+json",
    }


def _check_config(config_path: str) -> tuple[AppConfig | None, Check]:
    try:
        config = load_config(config_path)
    except ConfigError as e:
        return None, (f"config ({config_path})", False, str(e).split("\n")[0])
    n_repos = len(config.github.repos) if config.github else 0
    n_props = len(config.ga4.properties) if config.ga4 else 0
    return config, (
        f"config ({config_path})",
        True,
        f"{n_repos} repo(s), {n_props} GA4 propert{'y' if n_props == 1 else 'ies'}",
    )


def _check_admin(config: AppConfig) -> list[Check]:
    if not config.admin.enabled:
        return []
    ok = bool(os.environ.get("BRAGGRAPHS_ADMIN_PASSWORD"))
    return [
        (
            "BRAGGRAPHS_ADMIN_PASSWORD set",
            ok,
            "" if ok else "add it to .env (SETUP.md step 3)",
        )
    ]


def _check_github(config: AppConfig, offline: bool = False) -> list[Check]:
    if not config.github:
        return []
    if not os.environ.get("GITHUB_PAT"):
        return [("GITHUB_PAT set", False, "add it to .env (SETUP.md step 2)")]
    results: list[Check] = [("GITHUB_PAT set", True, "")]
    if offline:
        return results
    try:
        r = requests.get(
            f"{GITHUB_API}/rate_limit", headers=_github_headers(), timeout=15
        )
    except requests.RequestException as e:
        return results + [("GitHub reachable", False, f"network error: {e}")]
    if r.status_code != 200:
        return results + [
            (
                "GitHub token valid",
                False,
                f"GitHub replied {r.status_code} — token invalid, expired, or revoked",
            )
        ]
    remaining = r.json().get("resources", {}).get("core", {}).get("remaining", "?")
    results.append(("GitHub token valid", True, f"{remaining} API calls remaining"))
    for repo in config.github.repos:
        rr = requests.get(
            f"{GITHUB_API}/repos/{repo.owner}/{repo.repo}",
            headers=_github_headers(),
            timeout=15,
        )
        ok = rr.status_code == 200
        results.append(
            (
                f"repo {repo.project} reachable",
                ok,
                ""
                if ok
                else f"GitHub replied {rr.status_code} — check owner/repo spelling; "
                "a private repo needs a PAT with `repo` scope",
            )
        )
    return results


def _check_ga4(config: AppConfig, offline: bool = False) -> list[Check]:
    if not config.ga4:
        return []
    creds = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not creds:
        return [
            (
                "GOOGLE_APPLICATION_CREDENTIALS set",
                False,
                "add the service-account key path to .env (SETUP.md step 4)",
            )
        ]
    if not Path(creds).exists():
        return [
            (
                "GA4 key file exists",
                False,
                f"{creds} not found — in Docker, mount it (see docker-compose.yml)",
            )
        ]
    results: list[Check] = [("GA4 key file exists", True, "")]
    if offline:
        return results
    try:
        from connectors.ga4 import list_properties

        visible = {p["property_id"] for p in list_properties()}
    except Exception as e:  # noqa: BLE001 — any auth/API error is a finding here
        return results + [
            (
                "GA4 service account works",
                False,
                f"{str(e)[:160]} — are the Analytics Data + Admin APIs enabled?",
            )
        ]
    results.append(
        ("GA4 service account works", True, f"{len(visible)} propert(ies) visible")
    )
    for prop in config.ga4.properties:
        ok = prop.property_id in visible
        results.append(
            (
                f"GA4 property {prop.label} ({prop.property_id}) accessible",
                ok,
                ""
                if ok
                else "grant the service account the Viewer role on this property "
                "(GA4 Admin → Property access management)",
            )
        )
    return results


def _check_cloudflare(config: AppConfig, offline: bool = False) -> list[Check]:
    if not config.cloudflare:
        return []
    token = os.environ.get("CLOUDFLARE_API_TOKEN")
    if not token:
        return [
            (
                "CLOUDFLARE_API_TOKEN set",
                False,
                "create an API token with Account Analytics:Read and add it to .env "
                "(SETUP.md)",
            )
        ]
    results: list[Check] = [("CLOUDFLARE_API_TOKEN set", True, "")]
    if offline:
        return results
    try:
        from connectors.cloudflare import list_sites

        sites = {s["site_tag"]: s for s in list_sites(token, config.cloudflare.account_id)}
    except Exception as e:  # noqa: BLE001 — any auth/API error is a finding here
        return results + [("Cloudflare token + account work", False, str(e)[:200])]
    results.append(
        (
            "Cloudflare token + account work",
            True,
            f"{len(sites)} site(s) with recent Web Analytics data",
        )
    )
    for site in config.cloudflare.sites:
        ok = site.site_tag in sites
        results.append(
            (
                f"Cloudflare site {site.label} has recent data",
                ok,
                ""
                if ok
                else "no beacon data in the last 30 days — is Web Analytics enabled "
                "for this site? (`flask cf-sites` lists active site tags)",
            )
        )
    return results


def _check_storage() -> list[Check]:
    data_dir = Path(os.environ.get("BRAGGRAPHS_DATA_DIR", "data"))
    try:
        from core.storage import Storage

        data_dir.mkdir(parents=True, exist_ok=True)
        Storage(data_dir / "braggraphs.db")
        return [(f"data dir writable ({data_dir})", True, "")]
    except Exception as e:  # noqa: BLE001
        return [(f"data dir writable ({data_dir})", False, str(e)[:160])]


def run_checks(config_path: str | None = None, offline: bool = False) -> list[Check]:
    """offline=True validates config + secret presence only — no network.
    Useful in CI, dry-runs, and before any credentials exist."""
    config_path = config_path or os.environ.get("BRAGGRAPHS_CONFIG", "config.yml")
    config, config_check = _check_config(config_path)
    checks = [config_check]
    if config is None:
        return checks
    checks += _check_admin(config)
    checks += _check_github(config, offline)
    checks += _check_ga4(config, offline)
    checks += _check_cloudflare(config, offline)
    checks += _check_storage()
    return checks


def main() -> int:
    from core.config import load_dotenv

    load_dotenv()
    offline = "--offline" in sys.argv[1:]
    print(f"braggraphs preflight{' (offline: config + secrets only)' if offline else ''}\n")
    checks = run_checks(offline=offline)
    failed = 0
    for label, ok, detail in checks:
        mark = "✓" if ok else "✗"
        suffix = f" — {detail}" if detail else ""
        print(f"  {mark} {label}{suffix}")
        failed += 0 if ok else 1
    print()
    if failed:
        print(f"{failed} problem(s) found — fix the lines above and re-run.")
        return 1
    print("All good — `docker compose up -d` and you're live.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
