"""Public routes: /graph/...svg and /embed/... — no auth.

Both a 5-minute response cache and a 60 req/min per-IP limiter live in-process:
braggraphs runs as a single gunicorn worker (the scheduler requires it), so
plain dicts are correct and no extra dependency is needed.
"""
from __future__ import annotations

import time
from datetime import date, timedelta

from flask import Blueprint, Response, abort, current_app, render_template, request

from core.config import FLOW_UNITS, METRIC_KINDS
from render.svg import render_chart
from render.themes import DEFAULT_THEME, THEMES

public_bp = Blueprint(
    "public", __name__, template_folder="../render/templates"
)

CACHE_TTL = 300  # seconds
RATE_LIMIT = 60  # requests per minute per IP

_cache: dict[str, tuple[float, bytes, str]] = {}
_hits: dict[str, tuple[int, int]] = {}

PERIODS = {"30d": 30, "90d": 90, "1y": 365, "all": None}


def _rate_limited(ip: str) -> bool:
    limit = current_app.config.get("BG_RATE_LIMIT", RATE_LIMIT)
    window = int(time.time() // 60)
    prev_window, count = _hits.get(ip, (window, 0))
    if prev_window != window:
        count = 0
    count += 1
    _hits[ip] = (window, count)
    if len(_hits) > 10_000:  # bound memory under IP churn
        stale = [k for k, (w, _) in _hits.items() if w != window]
        for k in stale:
            del _hits[k]
    return count > limit


CACHE_MAX_ENTRIES = 2_000


def _cache_get(key: str):
    entry = _cache.get(key)
    if entry and entry[0] > time.time():
        return entry
    return None


def _cache_put(key: str, body: bytes, mimetype: str) -> None:
    ttl = current_app.config.get("BG_CACHE_TTL", CACHE_TTL)
    if ttl <= 0:
        return
    now = time.time()
    if len(_cache) >= CACHE_MAX_ENTRIES:
        for k in [k for k, v in _cache.items() if v[0] <= now]:
            del _cache[k]
    if len(_cache) >= CACHE_MAX_ENTRIES:
        # Still full of live entries (burst or param-stuffing): hard-evict the
        # soonest-to-expire tenth so the cache never grows past the cap.
        evict = sorted(_cache.items(), key=lambda kv: kv[1][0])
        for k, _ in evict[: max(1, CACHE_MAX_ENTRIES // 10)]:
            del _cache[k]
    _cache[key] = (now + ttl, body, mimetype)


def _cache_key(p: dict) -> str:
    """Canonical key from the *parsed* params only — unknown query args never
    reach the key, so ?x=1 / ?x=2 / bare all share one entry."""
    return request.path + "|" + "|".join(f"{k}={p[k]}" for k in sorted(p))


RESERVED_SOURCES = {"ga4", "cloudflare"}


def _guard_and_resolve(owner: str, repo: str, metric: str) -> tuple[str, str]:
    """Rate-limit, then map the URL to a tracked (source, project) or 404.

    Non-GitHub sources reserve their name as the first path segment:
    /graph/ga4/{label}/{metric}.svg, /graph/cloudflare/{label}/{metric}.svg.
    Anything else is github with project 'owner/repo'.
    """
    if _rate_limited(request.remote_addr or "?"):
        abort(429)
    if owner in RESERVED_SOURCES:
        source, project = owner, repo
    else:
        source, project = "github", f"{owner}/{repo}"
    if (source, project, metric) not in current_app.config["BG_TRACKED"]:
        abort(404)
    return source, project


def _int_arg(name: str, default: int, lo: int, hi: int) -> int:
    try:
        v = int(request.args.get(name, default))
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def _bool_arg(name: str, default: bool) -> bool:
    raw = request.args.get(name)
    if raw is None:
        return default
    return raw.lower() not in ("0", "false", "no", "off")


def _hex_arg(name: str) -> str | None:
    raw = (request.args.get(name) or "").lstrip("#")
    if len(raw) in (3, 6) and all(c in "0123456789abcdefABCDEF" for c in raw):
        return f"#{raw}"
    return None


# Chrome toggles: every line/label on the graph can be removed per-request.
TOGGLES = ("label", "value", "grid", "baseline", "dot", "dates", "border")


DEFAULT_PERIOD = "30d"  # default look window on every graph; period=all for full history


def _graph_params() -> dict:
    theme = request.args.get("theme", DEFAULT_THEME)
    period = request.args.get("period", DEFAULT_PERIOD)
    # sparkline=1 is shorthand for "just the line": all chrome off, dot on,
    # transparent background. Individual params still override it.
    sparkline = _bool_arg("sparkline", False)
    chrome_default = not sparkline
    bg = request.args.get("bg")
    if bg != "transparent":
        bg = _hex_arg("bg") or ("transparent" if sparkline else None)
    return {
        "w": _int_arg("w", 400, 40, 1600),
        "h": _int_arg("h", 120, 16, 900),
        "theme": theme if theme in THEMES else DEFAULT_THEME,
        "period": period if period in PERIODS else DEFAULT_PERIOD,
        # flow-metric value readout: sum over the trailing N days (0 = last day)
        "window": _int_arg("window", 30, 0, 365),
        "accent": _hex_arg("accent"),
        "bg": bg,
        **{
            f"show_{name}": _bool_arg(name, True if name == "dot" else chrome_default)
            for name in TOGGLES
        },
    }


@public_bp.get("/graph/<owner>/<repo>/<metric>.svg")
def graph(owner: str, repo: str, metric: str):
    p = _graph_params()
    cache_key = _cache_key(p)
    cached = _cache_get(cache_key)
    if cached:
        if _rate_limited(request.remote_addr or "?"):
            abort(429)
        return Response(cached[1], mimetype=cached[2], headers=_cache_headers())

    source, project = _guard_and_resolve(owner, repo, metric)
    days = PERIODS[p["period"]]
    since = (date.today() - timedelta(days=days)).isoformat() if days else None

    storage = current_app.extensions["bg_storage"]
    series = storage.get_series(source, project, metric, since=since)
    kind = METRIC_KINDS[metric]
    unit = "total" if kind == "cumulative" else FLOW_UNITS[metric]
    svg = render_chart(
        series,
        kind=kind,
        label=f"{project} · {metric.replace('_', ' ')}",
        unit=unit,
        theme=p["theme"],
        w=p["w"],
        h=p["h"],
        accent=p["accent"],
        bg=p["bg"],
        value_window_days=p["window"] or None,
        **{k: v for k, v in p.items() if k.startswith("show_")},
    )
    body = svg.encode("utf-8")
    _cache_put(cache_key, body, "image/svg+xml")
    return Response(body, mimetype="image/svg+xml", headers=_cache_headers())


@public_bp.get("/embed/<owner>/<repo>/<metric>")
def embed(owner: str, repo: str, metric: str):
    source, project = _guard_and_resolve(owner, repo, metric)
    p = _graph_params()
    # forward the full query string so every graph param works in the iframe too
    qs = request.query_string.decode()
    return render_template(
        "embed.html",
        title=f"{project} · {metric.replace('_', ' ')}",
        graph_url=f"/graph/{owner}/{repo}/{metric}.svg" + (f"?{qs}" if qs else ""),
        theme=THEMES[p["theme"]],
        w=p["w"],
        h=p["h"],
        powered_by=current_app.config["BG_CONFIG"].embed.powered_by,
    )


def _cache_headers() -> dict:
    ttl = current_app.config.get("BG_CACHE_TTL", CACHE_TTL)
    return {"Cache-Control": f"public, max-age={max(ttl, 0)}"}


@public_bp.errorhandler(429)
def _too_many(e):
    return Response("rate limit exceeded\n", status=429, mimetype="text/plain")
