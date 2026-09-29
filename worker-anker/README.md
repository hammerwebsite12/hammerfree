# Anker Workers — license + download resolver (Server 2)

Separate from **`worker/`** (PlayZip `dl-resolver`). Does **not** modify or redeploy
the PlayZip Worker.

**Repo root:** `D:\My Drive\Workspace\plazipanker\worker-anker`

| Worker | Config | Purpose |
|--------|--------|---------|
| **anker-resolver** | `wrangler.anker-resolver.jsonc` | HWID license gate |
| **anker-dlresolver** | `wrangler.anker-dlresolver.jsonc` | Licensed download URL resolve |

Both use the **same** quickplayusr HWID files and HMAC client auth as PlayZip.

Overview for the unified app: [../ANKER.md](../ANKER.md).

---

## Source layout

```
worker-anker/
├── src/
│   ├── anker-resolver/index.js    # POST /license/check
│   ├── anker-dlresolver/index.js  # POST /resolve
│   └── shared/
│       ├── auth.js                # HMAC verify, license gate, rate limit
│       ├── anker_upstream.js      # CSRF → generate-download-url → gate → CDN
│       ├── hwid_snapshot.js       # one-time HW record on first licensed resolve
│       └── abnormal_hwids.js      # Custom OS detection
├── wrangler.anker-resolver.jsonc
├── wrangler.anker-dlresolver.jsonc
├── deploy-all.ps1
├── setup.ps1
├── copy-secrets.ps1
└── package.json
```

Upstream formula details: [../anker/FORMULA.md](../anker/FORMULA.md).

---

## API endpoints

### anker-resolver

```
GET  /              → { ok: true }

POST /license/check
  Body: { hwid, device_fp, ts, nonce, sig, token }
  sig = HMAC_SHA256(SIGNING_SECRET, "{ts}.{nonce}.{hwid}.{device_fp}")

→ 200 { licensed: bool, custom_os?: bool }
```

Used by `license_manager.py` when **Server 2** is active (`ANKER_LICENSE_WORKER_URL`).

### anker-dlresolver

```
GET  /         → { ok: true }

POST /resolve
  Body: { game_id, hwid, device_fp, ts, nonce, sig, token, hw_snapshot? }
  game_id = Anker slug (e.g. cuphead)
  sig = HMAC_SHA256(SIGNING_SECRET, "{ts}.{nonce}.{game_id}.{hwid}.{device_fp}")

→ 200 { download_url, snapshot_recorded?: true }
→ 403 { error: "license_required", custom_os?: bool }
→ 429 { error: "rate_limited", wait: number }
```

Client: `anker/anker_api.py` → `_resolve_via_worker()` when `ANKER_DL_WORKER_URL` is set.

**No license / HWID** → `403 license_required` (download will not start).

---

## Deployed URLs (production)

- **anker-resolver:** `https://anker-resolver.hs2424.workers.dev`
- **anker-dlresolver:** `https://anker-dlresolver.hs2424.workers.dev`

PlayZip workers unchanged (`dl-resolver`, `playzip-resolver`).

Client template: [../client_secrets.example.py](../client_secrets.example.py).

### 2026-09-29 catalog host

Set `ANKER_BASE_URL` in `wrangler.anker-dlresolver.jsonc` (currently `https://ankergames.to`).
Must match `anker/config.py` `BASE_URL` on the Windows client. On CSRF/gate failure from Worker egress, dlresolver returns `{ client_resolve: true }` for the PC to finish the gate.

### 2026-08-10 upstream note

Anker host now mismatches HTML meta CSRF vs `GET /csrf-token` after game page load.
`anker_upstream.js` must use the JSON token only (see [../ANKER.md](../ANKER.md) incident log).
Redeploy **anker-dlresolver** after any upstream formula change.

---

## One-time setup

```powershell
cd "D:\My Drive\Workspace\plazipanker\worker-anker"
npm install
npx wrangler login
.\setup.ps1                 # creates ANKER_KV + patches wrangler configs
```

### Secrets (same APP_TOKEN / SIGNING_SECRET as PlayZip dl-resolver)

```powershell
npx wrangler secret put APP_TOKEN           -c wrangler.anker-resolver.jsonc
npx wrangler secret put SIGNING_SECRET      -c wrangler.anker-resolver.jsonc
npx wrangler secret put QUICKPLAY_HWID_PAT  -c wrangler.anker-resolver.jsonc
npx wrangler secret put DISCORD_WEBHOOK_URL -c wrangler.anker-resolver.jsonc  # optional

npx wrangler secret put APP_TOKEN              -c wrangler.anker-dlresolver.jsonc
npx wrangler secret put SIGNING_SECRET         -c wrangler.anker-dlresolver.jsonc
npx wrangler secret put QUICKPLAY_HWID_PAT     -c wrangler.anker-dlresolver.jsonc
npx wrangler secret put ANKER_RECAPTCHA_BYPASS -c wrangler.anker-dlresolver.jsonc
npx wrangler secret put DISCORD_WEBHOOK_URL    -c wrangler.anker-dlresolver.jsonc  # optional
```

`ANKER_RECAPTCHA_BYPASS` = `development-mode` (matches local Anker client).

Copy from existing PlayZip worker secrets:

```powershell
.\copy-secrets.ps1   # reads ..\worker\.secrets.json if present
```

---

## Deploy

```powershell
npm run deploy:all
# or:
npm run deploy:resolver
npm run deploy:dl
```

---

## Isolation from PlayZip

- **Separate Worker names** (`anker-resolver`, `anker-dlresolver`)
- **Separate KV** binding `ANKER_KV` (keys prefixed `anker:`)
- **No changes** to `../worker/wrangler.jsonc` or dl-resolver code

---

## Client integration (QuickPlay 2.7.5 beta)

Already wired in unified build:

```python
# client_secrets.py
ANKER_LICENSE_WORKER_URL = "https://anker-resolver.hs2424.workers.dev"
ANKER_DL_WORKER_URL = "https://anker-dlresolver.hs2424.workers.dev"
APP_TOKEN = "..."
SIGNING_SECRET = "..."
```

`store_manager.py` sets `license_manager.WORKER_URL` from the active server.
Downloads call `AnkerGamesClient.get_download_url()` → Worker `/resolve` when URL is set.

If Worker returns an Anker HTML gate URL, the client resolves the remaining hop
locally; **dlproxy** URLs are downloaded directly (see FORMULA.md).
