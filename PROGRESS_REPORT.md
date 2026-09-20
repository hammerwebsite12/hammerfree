# QuickPlay Progress Report

**Date:** September 20, 2026  
**Version:** **2.7.8** (`APP_VERSION = 2.7.8`, `APP_TITLE = QuickPlay 2.7.8`)  
**Repo:** [dvahana2424-web/playzipdl](https://github.com/dvahana2424-web/playzipdl) (private)  
**Branch:** `quickplay-2.7.8-beta`

## Summary

QuickPlay 2.7.8 is the Windows desktop build (Server 1 / Server 2 catalogs, Worker-backed downloads, protected EXE). This report covers the **September 20, 2026** session: instant `.part` pre-allocation, preparing/download progress UX, disk usage during prepare and extract, interrupted-download recovery prompt, public installer **2.7.8**, and `PORT_AGENTIC_INSTRUCTION.md` for Android/SteamOS ports.

## 2.7.8 session (2026-09-20)

| Area | Status | Notes |
|------|--------|-------|
| **Preparing download UX** | Done | Windows `truncate()` zero-filled 68 GB before download; replaced with seek+write sparse sizing (~0 s vs minutes). Green preparing bar; `on_prepare` reports reserved bytes + disk free/total. |
| **Progress text** | Done | `X out of Y` size labels; extract line separated from download byte summary (fixes mixed 96% + "download complete"). |
| **Partial-byte accounting** | Done | Progress from `.part.progress` / `partial_bytes_for_dest()` — not raw `.part` size on disk. |
| **Interrupted download recovery** | Done | Startup holds partials; Downloads banner **Resume** / **Delete partial files**; `GET/POST /api/downloads/recovery`; held paths exempt from orphan sweep. |
| **Extract disk usage** | Done | `disk_usage_for_path` polled ≤1 Hz during 7-Zip extract; shown in task card + dock. |
| **Port playbook** | Done | `PORT_AGENTIC_INSTRUCTION.md` — Android (merge-phase progress) + SteamOS (2.6.8 → 2.7.8 merge guide). |
| **Protected EXE** | Done | `dist/QuickPlay.exe` via `build-protected.ps1`. |
| **Public one-paste installer** | Done | `hammerwebsite12/hammerfree` `quickplay` — `install.ps1` **2.7.8**, release [quickplay-v2.7.8](https://github.com/hammerwebsite12/hammerfree/releases/tag/quickplay-v2.7.8). |

## 2.7.5 session (2026-09-16)

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

## Download resume (2.7.8)

| Stage | Auto-resume on restart? |
|-------|-------------------------|
| In-progress download with partial files on disk | **Prompt** — user chooses **Resume** or **Delete partial files** (reclaim space) |
| Queue entry without partials | **Yes** — auto fresh signed URL |
| Wait queue | **Yes** |
| Extract | **No full job** — archive kept until extract succeeds |

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

## File changelog (2026-09-20 session)

| File | Change |
|------|--------|
| `idm_downloader.py` | Fast `.part` allocation; `on_prepare`; `partial_bytes_for_dest` / `part_disk_bytes`; multi-conn `_prepare_resume` fix |
| `download_service.py` | `prepare_*` / `disk_*` fields; recovery hold; extract disk polling; recovery API helpers |
| `backend/server.py` | `/api/downloads/recovery` |
| `web/app.js` / `style.css` / `i18n.js` | Preparing bar, disk usage, recovery banner, `formatExtractMeta` |
| `settings_manager.py` | `APP_VERSION` 2.7.8 |
| `PORT_AGENTIC_INSTRUCTION.md` | Android + SteamOS port guide |
| `installer-publish/*` | Installer 2.7.8 + `QuickPlay.zip` |
| `dist/QuickPlay.exe` | Protected 2.7.8 build |

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
