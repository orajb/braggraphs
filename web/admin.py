"""Password-gated admin dashboard: tile grid, settings, fetch-now.

Auth is a session cookie set after comparing (constant-time) against the
BRAGGRAPHS_ADMIN_PASSWORD env var — single-user self-host needs nothing more.
"""
from __future__ import annotations

import hmac
import os
import threading
from datetime import date, timedelta
from functools import wraps

from flask import (
    Blueprint,
    current_app,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from render.themes import THEMES

admin_bp = Blueprint(
    "admin", __name__, url_prefix="/admin", template_folder="templates"
)


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("admin_authed"):
            return redirect(url_for("admin.login", next=request.path))
        return f(*args, **kwargs)

    return wrapper


def _url_segments(source: str, item) -> tuple[str, str]:
    """Map a configured item to the /graph/{a}/{b}/... URL segments."""
    if source != "github":
        return source, item.label
    return item.owner, item.repo


def _fmt(v: float | None) -> str:
    if v is None:
        return "–"
    return f"{int(v):,}" if v == int(v) else f"{v:,.1f}"


def _tiles(theme: str | None = None):
    config = current_app.config["BG_CONFIG"]
    storage = current_app.extensions["bg_storage"]
    week_ago = (date.today() - timedelta(days=7)).isoformat()
    suffix = f"?theme={theme}" if theme else ""
    tiles = []
    for source, item in config.items():
        seg_a, seg_b = _url_segments(source, item)
        for metric in item.metrics:
            latest = storage.latest(source, item.project, metric)
            prev = storage.value_at_or_before(source, item.project, metric, week_ago)
            delta = None
            if latest and prev is not None:
                delta = latest[1] - prev
            tiles.append(
                {
                    "source": source,
                    "project": item.project,
                    "metric": metric,
                    "graph_url": f"/graph/{seg_a}/{seg_b}/{metric}.svg{suffix}",
                    "embed_url": f"/embed/{seg_a}/{seg_b}/{metric}",
                    "value": _fmt(latest[1]) if latest else "–",
                    "delta": (f"{delta:+,.0f} this week" if delta is not None else None),
                    "delta_up": (delta or 0) >= 0,
                }
            )
    return tiles


def _statuses():
    storage = current_app.extensions["bg_storage"]
    return storage.all_statuses()


@admin_bp.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        expected = os.environ.get("BRAGGRAPHS_ADMIN_PASSWORD", "")
        supplied = request.form.get("password", "")
        if expected and hmac.compare_digest(supplied, expected):
            session["admin_authed"] = True
            next_url = request.args.get("next", "")
            if not next_url.startswith("/admin"):
                next_url = url_for("admin.dashboard")
            return redirect(next_url)
        error = "Wrong password."
    return render_template("login.html", error=error)


@admin_bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("admin.login"))


@admin_bp.get("/")
def dashboard():
    if not session.get("admin_authed"):
        return redirect(url_for("admin.login", next="/admin/"))
    theme = request.args.get("theme")
    if theme not in THEMES:
        theme = None
    return render_template(
        "admin.html", tiles=_tiles(theme), themes=THEMES, active_theme=theme
    )


@admin_bp.get("/builder")
@login_required
def builder():
    config = current_app.config["BG_CONFIG"]
    graphs = []
    for source, item in config.items():
        seg_a, seg_b = _url_segments(source, item)
        for metric in item.metrics:
            graphs.append(
                {
                    "path": f"/graph/{seg_a}/{seg_b}/{metric}.svg",
                    "name": f"{item.project} · {metric.replace('_', ' ')}",
                }
            )
    return render_template("builder.html", graphs=graphs, themes=THEMES)


@admin_bp.get("/settings")
@login_required
def settings():
    config = current_app.config["BG_CONFIG"]
    return render_template(
        "settings.html",
        tracked=sorted(config.tracked()),
        statuses=_statuses(),
        note=None,
    )


@admin_bp.post("/fetch")
@login_required
def fetch_now():
    svc = current_app.extensions.get("bg_scheduler")
    if svc is None:
        return (
            render_template(
                "_status.html", statuses=_statuses(), note="Scheduler is not running."
            ),
            503,
        )
    threading.Thread(target=svc.fire_now, daemon=True).start()
    return render_template(
        "_status.html",
        statuses=_statuses(),
        note="Fetch started — reload in a moment for fresh timestamps.",
    )
