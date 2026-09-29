# Anker / Server 2 — QuickPlay 2.7.9

Unified QuickPlay supports two catalogs via **Settings → Game Store Server**:

| Setting label | Internal id | Upstream | Workers |
|---------------|-------------|----------|---------|
| **Server 1** | `server1` | playzip.com mirrors | `worker/` → `dl-resolver` |
| **Server 2** | `server2` | ankergames.to (fallback ankergames.net) | `worker-anker/` → `anker-resolver` + `anker-dlresolver` |

User-facing UI shows **Server 1 / Server 2** only — no PlayZip or Anker branding.

## Source layout (this repo)

```
plazipanker/
├── store_manager.py          # picks client + license Worker per active server
├── anker/                    # Server 2 Python client + docs
│   ├── anker_api.py          # browse, search, get_download_url()
│   ├── anker_game_details.py # game detail panel (JSON-LD + Steam API)
│   ├── FORMULA.md            # upstream download/browse API recipe
│   └── README.md             # module guide + smoke tests
├── worker-anker/             # Cloudflare Workers (license + download resolve)
│   ├── src/anker-resolver/   # POST /license/check
│   ├── src/anker-dlresolver/ # POST /resolve → CDN URL
│   ├── src/shared/           # auth, upstream formula, HW snapshot
│   └── README.md             # deploy, secrets, endpoints
└── client_secrets.example.py # WORKER_URL + ANKER_*_WORKER_URL + tokens
```

## Client secrets (unified EXE)

```python
# Server 1 (PlayZip signing)
WORKER_URL = "https://dl-resolver.hs2424.workers.dev"

# Server 2 (Anker license + download resolve)
ANKER_LICENSE_WORKER_URL = "https://anker-resolver.hs2424.workers.dev"
ANKER_DL_WORKER_URL = "https://anker-dlresolver.hs2424.workers.dev"

APP_TOKEN = "..."       # same on all Workers
SIGNING_SECRET = "..."  # same on all Workers
```

`store_manager.py` switches `license_manager.WORKER_URL` when the user changes server.

## Download flow (Server 2)

1. Client → `POST /resolve` on **anker-dlresolver** (HMAC + HWID license gate)
2. Worker → CSRF session → game page → `generate-download-url` → gate page → **dlproxy / datanodes CDN URL**
3. Client (residential IP) downloads the file via `idm_downloader`
   - If Worker returns an Anker gate URL (`ankergames.net/download/...`), client resolves one more hop locally (`anker_api._resolve_gate_to_cdn`)
   - **dlproxy** URLs (`*.dlproxy.uk/download/...`) are the final file — HEAD may return 403; downloader probes size with `GET Range: bytes=0-0`

Browse/search/game details always hit the Anker base URL from the client (no Worker). Configure in `anker/config.py`; reachability probe in `anker/base_urls.py`.

## Upstream incidents

### 2026-09-29 — Registrar suspension + interim domain **ankergames.to**

**Symptom:** `ankergames.net` DNS/registrar outage; browse and download fail for all users.

**Official interim host:** `https://ankergames.to/` (Anker Discord). QuickPlay 2.7.9 sets this as primary; Workers use `ANKER_BASE_URL=https://ankergames.to` in `wrangler.anker-dlresolver.jsonc`.

**Client:** `pick_reachable_base_url()` tries `BASE_URL` then `FALLBACK_BASE_URLS`. Gate detection accepts both `.to` and `.net` hosts.

When the old domain is restored, add it to `FALLBACK_BASE_URLS` or swap `BASE_URL` — keep client and Worker in sync.

## Upstream incidents (ankergames.net — historical)

### 2026-08-10 — CSRF session strictness (Server 2 downloads broken)

**Symptom:** All Server 2 resolves failed with `502 (csrf_expired)` in QuickPlay logs;
upstream returned HTTP **419** `CSRF token mismatch` on `POST /generate-download-url`.

**Cause:** ankergames.net began returning a **different** CSRF token in the game page HTML
`<meta name="csrf-token">` than the token from `GET /csrf-token` after the game page
rotates the Laravel session. The **anker-dlresolver** Worker (since dual-server beta) overwrote
the JSON token with the meta token — that path worked for ~5 days, then failed once upstream
session handling tightened.

**Fix (Worker, no client EXE required):**

- `worker-anker/src/shared/anker_upstream.js` — keep `/csrf-token` JSON value for POST;
  do **not** substitute the HTML meta token; one automatic CSRF refresh retry on 419.
- Redeploy: `npx wrangler deploy -c wrangler.anker-dlresolver.jsonc`
- Deployed: `anker-dlresolver.hs2424.workers.dev` (2026-08-10).

See also [anker/FORMULA.md](anker/FORMULA.md) step 3 note.

## Server 2–only behaviors

When **Server 2** is active (`store_manager.is_anker`):

- Archive rename strips `-AnkerGames` suffix before extract
- Post-extract cleanup removes junk HTML/BAT files from repacks
- Download logs sanitized (`log_sanitize.set_anker_sanitize(True)`)

## Auto-failover

If **Server 1** is unreachable or search returns no results, the app may auto-switch to **Server 2** (persisted in `settings.json` → `store`).

## Docs

| Doc | Contents |
|-----|----------|
| [anker/README.md](anker/README.md) | Python module, smoke tests, file map |
| [anker/FORMULA.md](anker/FORMULA.md) | HTTP recipe: CSRF, gate pages, browse URLs, dlproxy |
| [worker-anker/README.md](worker-anker/README.md) | Worker deploy, secrets, API endpoints |

## Build & test

```powershell
cd "D:\My Drive\Workspace\plazipanker"
pip install -r requirements.txt
copy client_secrets.example.py client_secrets.py   # fill tokens
python main.py                                   # dev — toggle Server 1/2 in Settings

python -m PyInstaller --noconfirm --clean QuickPlay.spec
# → dist\QuickPlay.exe
```

Standalone Anker-only test EXE (optional, legacy):

```powershell
python -m anker.test_client browse
# or build via anker/main_anker.py + patch_store (see anker/README.md)
```
