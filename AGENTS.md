# AGENTS.md — deploying braggraphs for a user

You are an AI coding agent. Your job: stand up braggraphs on the user's
machine/server AND wire the resulting graph embeds into their existing
website. Follow the steps in order. Every step has a verification; do not
proceed past a failed verification — jump to §8 (Recovery) instead.

braggraphs is a Flask + SQLite service. It polls GitHub (and optionally GA4)
daily and serves SVG line charts at `/graph/{owner}/{repo}/{metric}.svg`.

Steps 1–4 mirror the human onboarding in [SETUP.md](SETUP.md) — same actions,
same order (SETUP.md steps 1–6). If you change one file, change both. Your
main verification tool throughout is the preflight:

```sh
docker compose run --rm braggraphs python -m core.doctor
# bare-Python installs:
.venv/bin/python -m core.doctor
```

It checks config validity, every secret, live GitHub token + per-repo access,
and per-property GA4 Viewer grants, printing a fix hint per failing line.

Docker is NOT required — it's the default because it's one command, but the
bare-Python variant of every step is equivalent (same `.env`, same
`config.yml`, same `./data` directory). Pick one path in step 1 and use its
command variants throughout.

## 1. Detect context

- Work from a clone of this repo, **in its own directory, never inside the
  user's website project** (braggraphs is a sibling service the site only
  references by URL — the sole footprint in the site is the `<img>` tags
  from step 6):
  `git clone https://github.com/orajb/braggraphs ~/apps/braggraphs && cd ~/apps/braggraphs`
- Pick the install path:
  - **Bare Python** is the default (needs `python3 --version` ≥ 3.11):
    `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`.
    Run the pip install *now*, not lazily — the step-4 preflight imports the
    app's dependencies, and a missing install surfaces as a confusing
    `ModuleNotFoundError` instead of a red check.
    Launch command for step 4:
    `.venv/bin/gunicorn -w 1 --threads 4 -b 0.0.0.0:8000 'app:create_app(start_scheduler=True)'`
    (exactly one worker — the scheduler runs in-process). On a server, wrap
    it in a systemd unit so it survives reboots; on Windows use
    `.venv\Scripts\python app.py`.
  - **Docker** (`docker compose version` succeeds) if the user prefers
    containers or Python 3.11+ isn't available.
  - Only ask the user to install something if *neither* Docker nor
    Python 3.11+ is present.
- Locate the user's existing website project (you'll edit its HTML in step 6).
  Ask if ambiguous.
- Sanity-check your `config.yml` any time without network or launch:
  `python -m core.doctor --offline` validates config syntax and secret
  presence only (the full doctor needs network).
- **Inventory the site while you're there — this is not optional.** Read its
  HTML/CSS and record two things:
  1. **Every project it showcases** (project cards, portfolio grids, nav
     links, "things I've built" sections — including ones rendered from JS
     data arrays). This list drives which repos/properties you track in
     step 3; missing a project that's on the site is the most common deploy
     mistake.
  2. **Its design system**: CSS colour variables / palette, light/dark
     theming, card structure and sizes. This drives where each graph goes and
     how it's styled in step 6.

## 2. Secrets → gitignored .env

- **Before writing any secret**, verify `.env` is listed in `.gitignore`:
  `grep -qx '\.env' .gitignore` must succeed. If it doesn't, add the line
  first. Never let secrets reach a commit.
- `cp .env.example .env`, then collect from the user and fill in (this is
  SETUP.md steps 2–4):
  - `GITHUB_PAT` — GitHub personal access token, created at
    https://github.com/settings/tokens; never echo it back or log it.
    braggraphs only ever *reads*, so recommend the least-privileged token
    that works:
    - Public repos: a classic PAT with **no scopes at all**, or a
      **fine-grained PAT** with "Public repositories (read-only)" — both are
      near-powerless and ideal to hand to an agent. (`public_repo` also
      works but includes write access.)
    - Private repos: classic `repo` scope, or a fine-grained PAT granted
      read access on those specific repos.
    **Privacy option:** the user doesn't have to paste secrets to you at all —
    offer to let them edit `.env` in their own editor, then confirm with the
    preflight (it reports validity without revealing values).
  - `BRAGGRAPHS_ADMIN_PASSWORD` — let the user choose, or generate one and
    show it to them once.
  - `GOOGLE_APPLICATION_CREDENTIALS` — only if the user wants GA4 traffic
    graphs. Walk the user through SETUP.md step 4 (they must do the browser
    parts themselves): create a GCP service account + JSON key, enable the
    Analytics **Data** and **Admin** APIs, grant the service account
    **Viewer** on their GA4 property. Mount the key in docker-compose (see
    the commented volume line) and set this var to the in-container path
    (e.g. `/secrets/ga4.json`).
  - `CLOUDFLARE_API_TOKEN` — only if the user wants Cloudflare Web Analytics
    traffic graphs (visits/pageviews, bot traffic excluded by design). The
    user creates it at dash.cloudflare.com/profile/api-tokens: Custom token,
    single permission **Account · Account Analytics · Read** (SETUP.md
    step 4b). Web Analytics must be enabled on the site.
- GA4 and Cloudflare are optional: if the user only wants GitHub graphs,
  leave the tokens unset and omit the `ga4:` / `cloudflare:` config blocks
  entirely. If the user's sites are behind Cloudflare, suggest the Cloudflare
  connector first — it's the lightest setup and its numbers exclude bots.

## 3. Configure config.yml

- `cp config.yml.example config.yml`.
- **GitHub repos**: build the candidate list from BOTH sources, then confirm
  with the user:
  1. the **site inventory from step 1** — every project showcased on the
     site is a tracking candidate; map each to its GitHub repo and flag any
     you can't match instead of silently dropping it;
  2. the user's **GitHub profile** — public repos with stars the site
     doesn't mention yet. Mechanism:
     `curl -s -H "Authorization: Bearer $GITHUB_PAT" "https://api.github.com/users/<user>/repos?per_page=100&sort=updated"`
     and look at `stargazers_count`.
  A site project you can't map to a repo gets **flagged to the user with the
  why** ("closed source — no repo to track; GA4 could still chart its
  traffic"), and mentioned again in the step-6 placement plan so its absence
  is a decision, not an oversight. Do not track only the repo the user
  happened to name first. For each:

  ```yaml
  github:
    repos:
      - owner: <owner>
        repo: <repo>
        metrics: [stars]          # any of: stars, forks, open_issues, commits_weekly
  ```

- **GA4 properties** (only if step 2 set up GA4): run the property picker —

  ```sh
  docker compose run --rm braggraphs flask ga4-properties
  # bare-Python: .venv/bin/flask ga4-properties
  ```

  It prints `property_id  display_name  (account)` for every property the
  service account can access. Present the list to the user, let them choose,
  and write the picks into `config.yml`:

  ```yaml
  ga4:
    properties:
      - label: <site.com>          # short slug; becomes the graph URL segment
        property_id: "<id from picker>"
        metrics: [pageviews]       # any of: pageviews, sessions, active_users
  ```

- **Cloudflare sites** (only if step 2 set up the token): the `account_id`
  is the 32-hex segment of the user's dashboard URL; then run the picker —

  ```sh
  docker compose run --rm braggraphs flask cf-sites
  # bare-Python: .venv/bin/flask cf-sites
  ```

  It lists every Web Analytics site tag with recent data. Write the picks
  into the `cloudflare:` block (`metrics: [visits]` or `[visits, pageviews]`).

  Metrics outside those allowlists fail config validation on boot — v1 is
  deliberately constrained.

## 4. Preflight, then launch

Run the preflight and fix anything red before starting the service:

```sh
docker compose run --rm braggraphs python -m core.doctor
```

All green? Launch (bare-Python: the gunicorn command from step 1):

```sh
docker compose up -d --build
```

Wait for health (retry up to ~60s):

```sh
curl -fsS http://localhost:8000/healthz
```

Expect `{"status": "ok", "db": true, "last_fetch_at": ...}`. The scheduler
runs immediately on boot and backfills history on first fetch (full star
history via the stargazers API; 365 days of GA4 traffic). Within a minute or
two `last_fetch_at` becomes non-null — poll `/healthz` until it does. If it
stays null, check `docker compose logs` and see §8.

## 5. Expose

The graph URLs must be publicly reachable for embeds to work. Pick what
matches the user's infra:

- **Existing reverse proxy** (Caddy, nginx): proxy a subdomain (e.g.
  `numbers.example.com`) to `localhost:8000`.
- **Cloudflare Tunnel** (no open ports): add a public hostname on the user's
  existing tunnel pointing at `http://localhost:8000`. If the user runs the
  OCI + Cloudflare Tunnel pattern, reuse that flow.

Verify from outside: `curl -fsS https://<public-host>/healthz`.

**Do not proceed to step 6 until the public hostname is settled** — step 6
writes absolute URLs into the user's site, and a hostname you invented is a
broken embed. If exposure is deferred, confirm the intended hostname with the
user explicitly and say the embeds will 404 until step 5 completes.

## 6. Wire the embeds into the user's site (the payoff)

- **Start from the step-1 design inventory and present a placement plan
  before touching any file.** For every tracked project, propose: which
  graph, where on the page (e.g. a star sparkline inside each project card's
  stat row, the full star-history card in an "open source" section, the
  traffic graph near the footer), at what size, with which theme/`accent`
  taken from the site's own CSS variables. One line per embed, then let the
  user adjust before you produce diffs. Do not embed a single graph and
  call it done when the site showcases several projects.
- Choose the graph URLs to embed, **matching the site's look**:
  - `https://<public-host>/graph/<owner>/<repo>/stars.svg`
  - Pick the nearest `theme=` (light, dark, mono, mono-dark, midnight,
    terminal, sunset, paper, oat, pine, sage), then read the site's CSS for
    its brand colour and pass it as `accent=<hex-no-#>`; `bg=transparent`
    makes the graph sit directly on any card/surface.
  - Size with `w=`/`h=` (default 400×120). For small portfolio cards or
    stat rows use `sparkline=1&w=120&h=32` (line + end dot only,
    transparent) — sizes work down to 40×16.
  - Individual elements can be removed with `label=0 value=0 grid=0
    baseline=0 dot=0 dates=0 border=0` if the full card is too busy.
  - When unsure, open `/admin/builder` — it builds the exact tag
    interactively.
- Locate a sensible spot in the user's existing site HTML (portfolio project
  card, About section, project README).
- Wrap every insertion in marker comments so re-runs are **idempotent**:

  ```html
  <!-- braggraphs:start owner/repo/stars -->
  <img src="https://numbers.example.com/graph/owner/repo/stars.svg?theme=dark"
       width="400" height="120" alt="owner/repo star history" loading="lazy"
       style="max-width:100%;height:auto">
  <!-- braggraphs:end -->
  ```

  (`max-width:100%` matters — full-size cards overflow fluid mobile layouts
  without it.)

  **Templated / JS-rendered markup** (project cards built from a data array):
  don't interpolate per-repo markers into the template output — a re-run
  grepping for `braggraphs:start owner/repo/...` in the *source* file won't
  find them. Instead, edit the template once and put ONE static marker
  comment in the source right where you modified it, e.g.
  `// braggraphs:template star-sparkline`.

  Before editing any file, grep it for `braggraphs:` — if any marker is
  present, **update the existing marked block in place — never duplicate
  it.**
- **Show the user a diff of the site edit and apply it only on their
  approval.** Never silently edit a live site.

## 7. Verify

For every embedded graph URL:

- `curl -fsS <url>` returns HTTP 200 with `Content-Type: image/svg+xml`.
- The body starts with `<svg` and — where data is expected — does **not**
  contain `no data yet`. (A brand-new repo with zero history may legitimately
  show it until the first fetch completes.)
- Load the user's edited page and confirm the graphs render.

Then report a checklist: service URL, admin URL (`/admin`, password from
step 2), each repo/property tracked, each page edited with which embeds.

## 8. Recovery

**First move for any setup problem:** re-run the preflight
(`docker compose run --rm braggraphs python -m core.doctor`) — it pinpoints
bad tokens, repo typos, missing GA4 grants, and unwritable data dirs with a
fix hint per line. Then:

| Symptom | Fix |
|---|---|
| `healthz` refused / container restarting | `docker compose logs braggraphs`. A `ConfigError` names exactly what's missing (env var or config key). |
| `missing required environment variables` on boot | `.env` incomplete — step 2. Compose reads `.env` automatically from the repo root. |
| GitHub 401 in `/admin/settings` last-error | Bad/expired PAT. Rotate the token, update `.env`, `docker compose up -d`. |
| GitHub 404 for a repo | Typo in `owner`/`repo`, or private repo with a PAT lacking `repo` scope. |
| GA4 `Viewer role` error | The service account email isn't added on the GA4 property (GA4 Admin → Property access management → add as Viewer). |
| `ga4-properties` prints nothing | Same as above — the account sees zero properties until granted access. |
| Port 8000 taken | Change the left side of `ports:` in `docker-compose.yml` (e.g. `"8010:8000"`) and re-expose. |
| Graph shows "no data yet" persistently | Check `/admin/settings` for the item's last error; the scheduler retries on a 1h→4h→24h backoff. Use **Fetch now** to retry immediately. |
| Embeds broken on the site but curl works | Mixed content (site is https, embed src is http) or the tunnel/DNS record isn't live yet. |

Failed fetches never crash the service; per-item status is always visible at
`/admin/settings` and machine-readable at `/healthz`.
