# QuickPlay Progress Report

**Date:** September 16, 2026  
**Version:** **2.7.5** (`APP_VERSION = 2.7.5`, `APP_TITLE = QuickPlay 2.7.5`)  
**Repo:** [dvahana2424-web/playzipdl](https://github.com/dvahana2424-web/playzipdl) (private)  
**Branch:** `quickplay-2.7.5-beta`

## Summary

QuickPlay 2.7.5 is the Windows desktop build (Server 1 / Server 2 catalogs, Worker-backed downloads, protected EXE). This report covers the **September 16, 2026** session: game runtime installer, browse idle fixes, post-download extract bug fix, and UX polish.

## 2.7.5 session (2026-09-16)

| Area | Status | Notes |
|------|--------|-------|
| **Post-download extract bug** | Fixed | `_cleanup_part_files` was deleting the finished `.zip` right after 100% download (before 7-Zip ran). Affected all large downloads (e.g. GTA SA). Archives are kept until extract succeeds; cancel-only cleanup uses `remove_final=True`. |
| **Game runtime installer** | Done | `redist_installer.py` — VC++ 2008–2022 + DirectX June 2010 offline redist; Settings UI + SSE `redist_progress`; logs to Downloads panel `[Runtimes]`. Fixed Microsoft CDN URLs (404s); `dxwebsetup` → offline `directx_Jun2010_redist.exe`; installer subprocess deadlock fix (`DEVNULL` + heartbeat). |
| **Stale browse / search after idle** | Done | `http_catalog.py` — session renew + retry on transient errors; `Connection: close` on catalog GETs; `POST /api/store/refresh-sessions`; UI fetch timeout/retry; visibility + SSE reconnect refresh. |
| **Download API blocking** | Done | `POST /api/downloads` and space-check run in `asyncio.to_thread` so browse/redist work cannot freeze the server. |
| **Android-style game detail** | Done | Download enabled from catalog; extras load in background (`game_details.py`, `web/app.js`). |
| **Download residue cleanup** | Done | Safer partial/progress sweep; no delete of finished archives during active extract. |
| **Protected EXE rebuild** | Done | `dist/QuickPlay.exe` via `build-protected.ps1`; `release.spec` includes `http_catalog`. |
| **Public one-paste installer** | Done | `hammerwebsite12/hammerfree` branch `quickplay` — `install.ps1` v2.7.5, `QuickPlay.zip` from latest `dist/QuickPlay.exe`; release [quickplay-v2.7.5](https://github.com/hammerwebsite12/hammerfree/releases/tag/quickplay-v2.7.5). |

## Download resume (unchanged behavior, documented)

| Stage | Auto-resume on restart? |
|-------|-------------------------|
| In-progress download (`.part` / `.quickplay_downloads.json` in **download folder**) | **Yes** — fresh signed URL, resume bytes |
| Wait queue | **Yes** |
| Extract | **No full job** — zip is **not** deleted mid-extract after fix; failed extract keeps archive for manual retry |

## Completed (prior releases)

| Area | Status | Notes |
|------|--------|-------|
| Dual-server catalog | Done | `store_manager.py` — Server 1 + Server 2 |
| In-place multi-connection download | Done | `idm_downloader.py` — `.part.progress` resume |
| Refresh download link | Done | Keep partials, new CDN URL |
| Workers (sign / license / Anker resolve) | Done | See `worker/`, `worker-anker/` |
| Protected EXE build | Done | `build-protected.ps1` → `dist/QuickPlay.exe` |

## Build & secrets

- `client_secrets.py` — match Worker secrets; committed on **private** `playzipdl` only
- Standard: `.\build.ps1` · Protected: `.\build-protected.ps1` (PyArmor on licensing modules)
- Close running QuickPlay before rebuild (WinError 5 if EXE locked)

## File changelog (2026-09-16 session)

| File | Change |
|------|--------|
| `download_service.py` | Do not delete final archive after successful download; extract pre-check; cancel `remove_final` |
| `redist_installer.py` | New — Microsoft runtime one-click install |
| `http_catalog.py` | New — catalog HTTP retry + session renew |
| `playzip_api.py` / `anker/anker_api.py` | Stale connection retry; catalog timeouts |
| `store_manager.py` | `invalidate_cached_clients()` |
| `backend/server.py` | Redist routes; store refresh; download `to_thread` |
| `web/app.js` | Redist UI, browse idle refresh, API timeout/retry |
| `web/index.html` / `i18n.js` / `style.css` | Runtime settings block |
| `game_details.py` | Server 1 detail HTML cleanup |
| `release.spec` | `http_catalog` hidden import |
| `dist/QuickPlay.exe` | Rebuilt protected binary |
| `installer-publish/*` | Installer scripts bumped to 2.7.5 (mirrors public `quickplay` branch) |

## Install command (end users)

```powershell
irm https://raw.githubusercontent.com/hammerwebsite12/hammerfree/quickplay/install.ps1 | iex
```

See also [AGENTS.md](AGENTS.md), [RELEASE_NOTES.md](RELEASE_NOTES.md).
