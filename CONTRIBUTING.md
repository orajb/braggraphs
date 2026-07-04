# Contributing to braggraphs

PRs welcome — especially new connectors. The codebase is deliberately small;
please keep it that way.

## Dev setup

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest          # full suite, no network, sub-second
```

Run locally against real data:

```sh
cp .env.example .env      # fill in GITHUB_PAT + BRAGGRAPHS_ADMIN_PASSWORD
cp config.yml.example config.yml
.venv/bin/python app.py   # dev server + scheduler on :8000
```

## How to add a connector

A connector is **one file** in `connectors/`. The v1 pair (`github.py`,
`ga4.py`) are the reference implementations.

1. **Subclass `Connector`** (`core/connector_base.py`):

   ```python
   from core.connector_base import Connector, Point

   class CloudflareConnector(Connector):
       name = "cloudflare"

       def fetch(self, item) -> list[Point]:
           # current values; called daily
           ...

       def backfill(self, item) -> list[Point]:
           # optional: historic seed, called once when the item has no data
           return []
   ```

   Return `Point(project, metric, kind, value, date)` where `kind` is
   `"cumulative"` (running total, like stars) or `"flow"` (amount per day/week,
   like pageviews) and `date` is a UTC `YYYY-MM-DD`. Storage upserts by date,
   so refetching a range is always safe.

2. **Config model** — add a block to `core/config.py` (Pydantic model +
   wire it into `AppConfig.items()`), including a metric allowlist and the
   new metrics' entries in `METRIC_KINDS` / `FLOW_UNITS`.

3. **Register it** — add the construction branch in
   `core/scheduler.py::build_scheduler` (read secrets from env vars only).

4. **Routing** — if the source isn't GitHub-shaped, reserve its slug as the
   first URL segment the way `ga4` is in `web/public.py::_guard_and_resolve`.

5. **Tests** — record a real API response once, commit it as JSON under
   `tests/connectors/fixtures/`, and replay it (see `test_github.py` for
   `responses`-based HTTP mocking, `test_ga4.py` for stub-client injection).
   Cover: fetch mapping, backfill, and one error case with a helpful message.

That's the whole surface. Scheduler cadence, retry/backoff, storage,
rendering, and the admin dashboard all pick the new source up automatically.

## Ground rules

- **Secrets** come from env vars, are never logged, and never touch SQLite.
- **The renderer stays hand-rolled** — no chart libraries, output < 5KB.
- **New themes are one dict** in `render/themes.py` (seven colour roles); they
  appear in the admin theme switcher and embed builder automatically.
- **Snapshot tests**: if you intentionally change SVG output, regenerate with
  `UPDATE_SNAPSHOTS=1 pytest tests/test_render.py` and eyeball the diff.
- **Admin CSS** is compiled Tailwind, checked in at `web/static/admin.css`.
  After editing templates: `npx @tailwindcss/cli -i input.css -o web/static/admin.css --minify`
  with an `input.css` of `@import "tailwindcss"; @source "./web/templates";`.
- Python 3.11+, no new runtime dependencies without a strong reason.
