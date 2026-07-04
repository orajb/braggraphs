# Setup

From zero to a live graph in ~10 minutes. Five steps, one optional.
(Deploying with an AI agent? Point it at the repo — [AGENTS.md](AGENTS.md)
follows these same steps automatically.)

**You need:** a GitHub account, plus **either** Docker **or** Python 3.11+ —
Docker is packaging convenience, not a requirement. GA4 is optional (step 4).

## 1. Clone

```sh
git clone https://github.com/orajb/braggraphs
cd braggraphs
cp .env.example .env
cp config.yml.example config.yml
```

**No Docker?** Set up a venv now; the later steps show both variants:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## 2. GitHub access — 1 minute

braggraphs reads your repo stats with a personal access token. It's read-only
in practice and lives only in your `.env` (gitignored) — never in config, the
database, or logs.

1. Go to **github.com → Settings → Developer settings →
   [Personal access tokens](https://github.com/settings/tokens) → Generate new
   token (classic)**.
2. Scope: for public repos, **no scopes at all** is enough — braggraphs only
   reads public data, and an unscoped token is the safest thing to paste
   anywhere. (`public_repo` also works. Private repos need `repo`.
   Prefer fine-grained tokens? "Public repositories (read-only)" — or, for
   private repos, read access granted on just those repos — works too.)
3. Copy the token into `.env`:

   ```
   GITHUB_PAT=ghp_…
   ```

*Rotating later:* generate a new token, update `.env`, `docker compose up -d`.

## 3. Admin password — 10 seconds

Pick any password for the `/admin` dashboard:

```
BRAGGRAPHS_ADMIN_PASSWORD=something-long
```

## 4. Google Analytics access — optional, 5 minutes

Skip this if you only want GitHub graphs (delete the `ga4:` block from
`config.yml` and jump to step 5).

Access has two halves: a **service account** (the identity) and a **Viewer
grant on your property** (the permission).

1. In [Google Cloud Console](https://console.cloud.google.com/): create or
   reuse a project.
2. **APIs & Services → Enable APIs**: enable the **Google Analytics Data API**
   and the **Google Analytics Admin API**.
3. **IAM & Admin → Service accounts → Create service account** (any name, no
   project roles needed). Open it → **Keys → Add key → JSON** — a key file
   downloads. Save it next to the repo as `ga4-credentials.json`
   (it's gitignored).
4. In [GA4](https://analytics.google.com/): **Admin → Property access
   management → +** — add the service account's email (looks like
   `name@project.iam.gserviceaccount.com`) with the **Viewer** role.
5. Wire it up: uncomment the key-file mount in `docker-compose.yml`, and in
   `.env` set:

   ```
   GOOGLE_APPLICATION_CREDENTIALS=/secrets/ga4.json
   ```

*Revoking later:* remove the Viewer grant or delete the key in Google — takes
effect on the next fetch.

## 4b. Cloudflare Web Analytics access — optional, 3 minutes

The lightest traffic source — **bot traffic is excluded by design** (the
numbers come from a browser beacon crawlers never execute). Skip if you don't
use Cloudflare.

1. In the Cloudflare dashboard, make sure **Analytics → Web Analytics** is
   enabled for your site (free; one click for proxied zones).
2. Create a token at **[My Profile → API Tokens](https://dash.cloudflare.com/profile/api-tokens)
   → Create Token → Custom**: single permission **Account · Account
   Analytics · Read**. Nothing else — it can read analytics and do nothing
   more.
3. Add to `.env`:

   ```
   CLOUDFLARE_API_TOKEN=…
   ```

4. Your `account_id` is the 32-char hex string in your dashboard URL
   (`dash.cloudflare.com/<account_id>/…`) — it goes in `config.yml` (it's an
   identifier, not a secret). Site tags come from the picker in step 5.

*Revoking later:* delete the token in the dashboard.

## 5. Tell it what to track

Edit `config.yml`:

```yaml
github:
  repos:
    - owner: you
      repo: your-project
      metrics: [stars, forks, commits_weekly]   # also: open_issues

# only if you did step 4:
ga4:
  properties:
    - label: yoursite.com          # becomes the graph's URL segment
      property_id: "123456789"
      metrics: [pageviews]         # also: sessions, active_users
```

```yaml
# only if you did step 4b:
cloudflare:
  account_id: "<32-hex from your dashboard URL>"
  sites:
    - label: yoursite.com
      site_tag: "<from the picker below>"
      metrics: [visits]            # also: pageviews
```

Don't hunt for IDs — the pickers list what your credentials can see:

```sh
docker compose run --rm braggraphs flask ga4-properties
docker compose run --rm braggraphs flask cf-sites
# or without Docker:
.venv/bin/flask ga4-properties
.venv/bin/flask cf-sites
```

## 6. Preflight, launch, embed

**Preflight** — checks your config, tokens, repo names, and GA4 grants, and
tells you exactly what to fix:

```sh
docker compose run --rm braggraphs python -m core.doctor
# or without Docker:
.venv/bin/python -m core.doctor
```

Every line green? **Launch:**

```sh
docker compose up -d
# or without Docker:
.venv/bin/gunicorn -w 1 --threads 4 -b 0.0.0.0:8000 'app:create_app(start_scheduler=True)'

curl http://localhost:8000/healthz    # {"status": "ok", ...}
```

Both paths read the same `.env` and `config.yml`, and store everything under
`./data`. (Bare-Python notes: keep exactly **one** worker — the scheduler runs
in-process; on Windows, where gunicorn doesn't run, use `python app.py`; on a
server, wrap the gunicorn line in a systemd unit or `tmux` to keep it alive.)

The first fetch runs immediately and backfills history (full star history,
365 days of GA4 traffic), so your graphs start full, not empty.

**Embed** — open `http://localhost:8000/admin/builder`, dial in a graph
(theme, size, sparkline mode), and copy the tag. Or hand-write it:

```html
<img src="http://localhost:8000/graph/you/your-project/stars.svg">
```

To serve embeds on your public site, put braggraphs behind a reverse proxy or
Cloudflare Tunnel and swap in that hostname.

## If something's red

Re-run the preflight after each fix — it names the failing step. The most
common trip-ups:

| Red line | Fix |
|---|---|
| `GitHub token valid` | Token expired/revoked or pasted with whitespace — regenerate, re-paste. |
| `repo … reachable` | Typo in `owner`/`repo`, or private repo without `repo` scope. |
| `GA4 service account works` | Data/Admin API not enabled on the GCP project (step 4.2). |
| `GA4 property … accessible` | Viewer grant missing on that property (step 4.4). |
| `Cloudflare token + account work` | Token lacks Account Analytics:Read, or `account_id` is wrong (step 4b). |
| `Cloudflare site … has recent data` | Web Analytics isn't enabled for that site, or the `site_tag` is wrong (`flask cf-sites`). |
| Graphs empty after launch | Check `/admin/settings` — per-repo status + last error, with a **Fetch now** button. |

More detail: [README](README.md) · agent version of this flow: [AGENTS.md](AGENTS.md)
