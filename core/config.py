"""YAML config loading + validation (Pydantic) and env-var access.

Secrets never live in config.yml — they come from environment variables only:
GITHUB_PAT, GOOGLE_APPLICATION_CREDENTIALS, CLOUDFLARE_API_TOKEN,
BRAGGRAPHS_ADMIN_PASSWORD.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

GITHUB_METRICS = {"stars", "forks", "open_issues", "commits_weekly"}
GA4_METRICS = {"pageviews", "sessions", "active_users"}
CLOUDFLARE_METRICS = {"visits", "pageviews"}

# 'cumulative' (running total, e.g. stars) vs 'flow' (amount per period,
# e.g. pageviews/day). Drives axis labelling and the renderer's zero baseline.
METRIC_KINDS = {
    "stars": "cumulative",
    "forks": "cumulative",
    "open_issues": "cumulative",
    "commits_weekly": "flow",
    "pageviews": "flow",
    "sessions": "flow",
    "active_users": "flow",
    "visits": "flow",
}

# Unit suffix shown next to the current value on flow graphs.
FLOW_UNITS = {
    "commits_weekly": "/wk",
    "pageviews": "/day",
    "sessions": "/day",
    "active_users": "/day",
    "visits": "/day",
}


class ConfigError(Exception):
    """Raised for invalid config or missing required secrets."""


def load_dotenv(path: str | Path = ".env") -> int:
    """Minimal .env loader (KEY=VALUE lines, # comments, optional quotes).

    docker-compose reads .env natively; this makes the bare-Python path behave
    identically without a python-dotenv dependency. Real environment variables
    always win over .env values. Returns the number of variables loaded.
    """
    path = Path(path)
    if not path.exists():
        return 0
    loaded = 0
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value
            loaded += 1
    return loaded


class GitHubRepo(BaseModel):
    owner: str
    repo: str
    metrics: list[str] = Field(default_factory=lambda: ["stars"])

    @field_validator("metrics")
    @classmethod
    def _known_metrics(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("metrics list must not be empty")
        unknown = set(v) - GITHUB_METRICS
        if unknown:
            raise ValueError(
                f"unknown github metrics {sorted(unknown)}; allowed: {sorted(GITHUB_METRICS)}"
            )
        return v

    @property
    def project(self) -> str:
        return f"{self.owner}/{self.repo}"


class GitHubConfig(BaseModel):
    cadence: str = "daily"
    repos: list[GitHubRepo]

    @field_validator("repos")
    @classmethod
    def _non_empty(cls, v: list[GitHubRepo]) -> list[GitHubRepo]:
        if not v:
            raise ValueError("github.repos must list at least one repo")
        return v


class GA4Property(BaseModel):
    label: str
    property_id: str
    metrics: list[str] = Field(default_factory=lambda: ["pageviews"])

    @field_validator("metrics")
    @classmethod
    def _known_metrics(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("metrics list must not be empty")
        unknown = set(v) - GA4_METRICS
        if unknown:
            raise ValueError(
                f"unknown ga4 metrics {sorted(unknown)}; allowed: {sorted(GA4_METRICS)}"
            )
        return v

    @field_validator("property_id", mode="before")
    @classmethod
    def _stringify(cls, v):
        return str(v)

    @property
    def project(self) -> str:
        return self.label


class GA4Config(BaseModel):
    cadence: str = "daily"
    properties: list[GA4Property]

    @field_validator("properties")
    @classmethod
    def _non_empty(cls, v: list[GA4Property]) -> list[GA4Property]:
        if not v:
            raise ValueError("ga4.properties must list at least one property")
        return v


class CloudflareSite(BaseModel):
    label: str
    site_tag: str
    metrics: list[str] = Field(default_factory=lambda: ["visits"])

    @field_validator("metrics")
    @classmethod
    def _known_metrics(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("metrics list must not be empty")
        unknown = set(v) - CLOUDFLARE_METRICS
        if unknown:
            raise ValueError(
                f"unknown cloudflare metrics {sorted(unknown)}; "
                f"allowed: {sorted(CLOUDFLARE_METRICS)}"
            )
        return v

    @field_validator("site_tag")
    @classmethod
    def _hex_tag(cls, v: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{32}", v):
            raise ValueError(
                "site_tag must be the 32-char hex Web Analytics site tag "
                "(list them with `flask cf-sites`)"
            )
        return v

    @property
    def project(self) -> str:
        return self.label


class CloudflareConfig(BaseModel):
    cadence: str = "daily"
    account_id: str
    sites: list[CloudflareSite]

    @field_validator("account_id")
    @classmethod
    def _hex_account(cls, v: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{32}", v):
            raise ValueError("account_id must be the 32-char hex Cloudflare account id")
        return v

    @field_validator("sites")
    @classmethod
    def _non_empty(cls, v: list[CloudflareSite]) -> list[CloudflareSite]:
        if not v:
            raise ValueError("cloudflare.sites must list at least one site")
        return v


class AdminConfig(BaseModel):
    enabled: bool = True


class EmbedConfig(BaseModel):
    powered_by: bool = True


class AppConfig(BaseModel):
    github: GitHubConfig | None = None
    ga4: GA4Config | None = None
    cloudflare: CloudflareConfig | None = None
    admin: AdminConfig = Field(default_factory=AdminConfig)
    embed: EmbedConfig = Field(default_factory=EmbedConfig)

    def items(self):
        """Yield (source, item) for every configured repo/property/site."""
        if self.github:
            for repo in self.github.repos:
                yield "github", repo
        if self.ga4:
            for prop in self.ga4.properties:
                yield "ga4", prop
        if self.cloudflare:
            for site in self.cloudflare.sites:
                yield "cloudflare", site

    def tracked(self) -> set[tuple[str, str, str]]:
        """Every configured (source, project, metric) triple."""
        out = set()
        for source, item in self.items():
            for metric in item.metrics:
                out.add((source, item.project, metric))
        return out


def load_config(path: str | Path) -> AppConfig:
    path = Path(path)
    if not path.exists():
        raise ConfigError(
            f"config file not found: {path} — copy config.yml.example to config.yml"
        )
    with open(path) as f:
        raw = yaml.safe_load(f) or {}
    try:
        config = AppConfig.model_validate(raw)
    except ValidationError as e:
        raise ConfigError(f"invalid config in {path}:\n{e}") from e
    if config.github is None and config.ga4 is None and config.cloudflare is None:
        raise ConfigError(
            f"{path} configures no sources — add a github:, ga4:, or cloudflare: block"
        )
    return config


def check_secrets(config: AppConfig) -> None:
    """Fail fast at startup when a configured source is missing its secret."""
    missing = []
    if config.github and not os.environ.get("GITHUB_PAT"):
        missing.append("GITHUB_PAT (required by the github: block in config.yml)")
    if config.ga4 and not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        missing.append(
            "GOOGLE_APPLICATION_CREDENTIALS (required by the ga4: block in config.yml)"
        )
    if config.cloudflare and not os.environ.get("CLOUDFLARE_API_TOKEN"):
        missing.append(
            "CLOUDFLARE_API_TOKEN (required by the cloudflare: block; an API "
            "token with the Account Analytics:Read permission)"
        )
    if config.admin.enabled and not os.environ.get("BRAGGRAPHS_ADMIN_PASSWORD"):
        missing.append(
            "BRAGGRAPHS_ADMIN_PASSWORD (required while admin.enabled is true)"
        )
    if missing:
        raise ConfigError(
            "missing required environment variables:\n  - " + "\n  - ".join(missing)
        )
