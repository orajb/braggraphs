"""braggraphs — application entrypoint.

Dev:        python app.py            (Flask dev server + scheduler)
Production: gunicorn -w 1 --threads 4 -b 0.0.0.0:8000 'app:create_app(start_scheduler=True)'

Exactly one worker process: the APScheduler loop runs in-process, and the
5-minute response cache + per-IP rate limiter are in-memory.
"""
from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path

from flask import Flask, jsonify
from werkzeug.middleware.proxy_fix import ProxyFix

from core.config import check_secrets, load_config, load_dotenv
from core.storage import Storage


def _secret_key(data_dir: Path) -> str:
    env = os.environ.get("BRAGGRAPHS_SECRET_KEY")
    if env:
        return env
    key_file = data_dir / "secret_key"
    if key_file.exists():
        return key_file.read_text().strip()
    key = secrets.token_hex(32)
    key_file.write_text(key)
    key_file.chmod(0o600)
    return key


def create_app(
    config_path: str | None = None,
    data_dir: str | Path | None = None,
    start_scheduler: bool = False,
) -> Flask:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    load_dotenv()  # no-op under docker-compose (env already set); covers bare Python
    config_path = config_path or os.environ.get("BRAGGRAPHS_CONFIG", "config.yml")
    data_dir = Path(data_dir or os.environ.get("BRAGGRAPHS_DATA_DIR", "data"))
    data_dir.mkdir(parents=True, exist_ok=True)

    config = load_config(config_path)
    check_secrets(config)
    storage = Storage(data_dir / "braggraphs.db")

    app = Flask(__name__, static_folder="web/static", static_url_path="/static")
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1)
    app.secret_key = _secret_key(data_dir)
    app.config["BG_CONFIG"] = config
    app.config["BG_TRACKED"] = config.tracked()
    app.extensions["bg_storage"] = storage

    from web.public import public_bp

    app.register_blueprint(public_bp)
    if config.admin.enabled:
        from web.admin import admin_bp

        app.register_blueprint(admin_bp)

    @app.get("/healthz")
    def healthz():
        return jsonify(status="ok", db=True, last_fetch_at=storage.last_fetch_at())

    scheduler = None
    if start_scheduler:
        from core.scheduler import build_scheduler

        scheduler = build_scheduler(config, storage)
        scheduler.start()
    app.extensions["bg_scheduler"] = scheduler

    @app.cli.command("ga4-properties")
    def ga4_properties():
        """List the GA4 properties the service account can access."""
        from connectors.ga4 import list_properties

        props = list_properties()
        if not props:
            print("No GA4 properties visible to this service account.")
            print("Grant it the Viewer role on each property in GA4 admin.")
            return
        for p in props:
            print(f"{p['property_id']}\t{p['display_name']}\t({p['account']})")

    @app.cli.command("cf-sites")
    def cf_sites():
        """List Cloudflare Web Analytics sites with recent beacon data."""
        from connectors.cloudflare import list_sites

        token = os.environ.get("CLOUDFLARE_API_TOKEN")
        if not config.cloudflare or not token:
            print(
                "Add a cloudflare: block (with account_id) to config.yml and set "
                "CLOUDFLARE_API_TOKEN first — see SETUP.md."
            )
            return
        sites = list_sites(token, config.cloudflare.account_id)
        if not sites:
            print(
                "No Web Analytics sites reported data in the last 30 days. "
                "Enable Web Analytics in the Cloudflare dashboard (Analytics → "
                "Web Analytics) for each site."
            )
            return
        for s in sites:
            print(f"{s['site_tag']}\t{s['host']}\t({s['visits']:.0f} visits/30d)")

    return app


if __name__ == "__main__":
    create_app(start_scheduler=True).run(host="0.0.0.0", port=8000)
