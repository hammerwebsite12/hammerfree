# QuickPlay Progress Report

**Date:** September 16, 2026  
**Version:** **2.7.5 beta** (`APP_VERSION = 2.7.5-beta`)  
**Repo:** [hammerwebsite12/hammerfree](https://github.com/hammerwebsite12/hammerfree)  
**Branch:** `quickplay-2.7.5-beta`

## Summary

QuickPlay 2.x ships dual-server catalogs (Server 1 / Server 2), licensed downloads via Cloudflare Workers, and a protected Windows EXE build. **2.7.5 beta** focuses on downloader performance and link refresh UX: parallel downloads assemble **in place** into a single `.part` file (no separate merge pass), plus a **Refresh link** control with step-by-step resolve status in the dock and Downloads tab.

## 2.7.5 beta (2026-09-16)

| Area | Status | Notes |
|------|--------|-------|
| In-place multi-connection download | Done | `idm_downloader.py` — offset writes into one `.part`; `.part.progress` for resume |
| Legacy chunk migration | Done | One-time import from `.part.partN` → `.part` on resume |
| Progress file locking (Windows) | Done | Fixes `WinError 32` when multiple threads saved `.progress` |
| Refresh download link | Done | `POST /api/downloads/{id}/refresh-link` — new URL, keep partials |
| Live resolve status | Done | Signer / store / mirror steps pushed to task `status_message` |
| App title + version | Done | `APP_TITLE` — window + top bar show **QuickPlay 2.7.5 beta** |

## Completed (prior releases)

| Area | Status | Notes |
|------|--------|-------|
| Dual-server catalog | Done | `store_manager.py` — Server 1 (PlayZip) + Server 2 (Anker) |
| dl-resolver Worker | Done | `/sign`, `/license/check` — PlayZip download signing |
| anker-resolver Worker | Done | `/license/check` — Server 2 license gate |
| anker-dlresolver Worker | Done | `/resolve` — Server 2 CDN URL resolver |
| Device fingerprint (client) | Done | `device_fingerprint.py` — 64-char hex SHA-256 |
| Registration code (client) | Done | `hwid_obfuscation.py` + license modal |
| Custom OS UI | Done | Fingerprint field + Copy Both in `web/app.js` |
| Worker license gate | Done | `/sign` checks `.user` in `quickplayusr` repo |
| Worker HW snapshot | Done | One-time write + Discord on first licensed sign |
| Worker abuse protections | Done | Nonce replay, optional audit/rate-limit (see KV lite mode) |
| **Worker KV lite mode** | Done | **2026-08-26** — avoids quota 500s on free tier |
| Cancel download fix | Done | `_remove_task` + partial cleanup with `glob.escape` |
| Pause/resume UX | Done | Confirm pause, optimistic UI, 32 KB chunks |
| Themed native dialogs | Done | Admin required, single instance, Defender warning |
| Mandatory admin on start | Done | Run as Admin / Exit every non-elevated launch |
| Defender auto-exclusion | Done | On startup + manual steps if auto fails |
| Hardware snapshot (client) | Done | `hardware_snapshot.py`, settings flag |
| Protected EXE build | Done | `build-protected.ps1` / `build.ps1` → `dist/QuickPlay.exe` |
| Seller activator tools | Done | Unified Activator / KeyGen (separate repos) |

## Deployed

| Service | URL | Account | Last redeploy |
|---------|-----|---------|---------------|
| dl-resolver | https://dl-resolver.hs2424.workers.dev | hanahsong2424@gmail.com | 2026-08-26 (KV lite mode) |
| anker-resolver | https://anker-resolver.hs2424.workers.dev | hanahsong2424@gmail.com | 2026-08-26 |
| anker-dlresolver | https://anker-dlresolver.hs2424.workers.dev | hanahsong2424@gmail.com | 2026-08-26 |
| activateme-api | https://activateme-api.sheryltacipit02.workers.dev | sheryltacipit02@gmail.com | — |
| QuickPlay EXE | `dist/QuickPlay.exe` (local build) | — | 2026-09-16 (2.7.5 beta) |

**Wrangler account ID (hs2424):** `1f3d8591d5ce5b25bb355660031673b0`

## Incident: KV quota → Worker 500 (2026-08-26)

### Symptom

- `GET /` on all Workers returned **200 OK**
- Invalid auth returned correct **400 / 401**
- Valid signed requests (`/sign`, `/license/check`, `/resolve`) returned **HTTP 500** with Cloudflare **error 1101** (uncaught exception)
- Cloudflare email: **KV daily write quota exceeded** (free tier: 1,000 writes/day)

### Root cause

Each authenticated request previously performed **multiple KV writes** (nonce, audit, counters). At ~5+ writes per sign request, the free tier cap was reached quickly.

### Fix (shipped + redeployed)

1. **`KV_LITE_MODE=true`** — minimal KV writes on `/sign` and `/resolve`
2. **`try/catch` around KV abuse checks** — graceful degradation
3. **Top-level fetch handler** — JSON error instead of opaque 1101

See historical file list in git history for `worker/src/kv_policy.js` and related Worker changes.

## Build & secrets

- `client_secrets.py` is git-ignored — must match Worker secrets before building EXE
- Standard build: `.\build.ps1` → `dist\QuickPlay.exe`
- Protected build: `.\build-protected.ps1` (PyArmor on licensing/API modules)

## Next steps (optional)

- [ ] GitHub Release tag `quickplay-v2.7.5-beta` with `QuickPlay.exe`
- [ ] Workers Paid plan if sign volume exceeds free KV tier
- [ ] End-to-end test: refresh link during large Server 1 download + resume after restart

## File changelog (2.7.5 beta — 2026-09-16)

| File | Change |
|------|--------|
| `idm_downloader.py` | In-place multi-connection writes; `.part.progress`; no merge pass |
| `download_service.py` | Refresh link API; resolve status UI; partial/progress cleanup |
| `playzip_api.py` / `anker/anker_api.py` | Resolve status callbacks for UI |
| `store_manager.py` | Wire `on_resolve_status` to store clients |
| `web/app.js` / `web/i18n.js` | Refresh link button + status lines |
| `settings_manager.py` | `APP_VERSION = 2.7.5-beta`, `APP_TITLE` |
| `main.py` / `web/index.html` | Window title + brand show beta version |

See also [RELEASE_NOTES.md](RELEASE_NOTES.md), [AGENTS.md](AGENTS.md), [README.md](README.md).
