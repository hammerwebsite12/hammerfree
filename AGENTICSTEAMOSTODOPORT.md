# AGENTICSTEAMOSTODOPORT.md — QuickPlay 2.7.5 beta → SteamOS Port

> **Audience:** AI agent (or human) merging **QuickPlay 2.7.5 beta** (Windows unified Server 1 + Server 2)
> into the existing **SteamOS port** from `playzipdl`.
>
> **Read this entire file before editing.** Preserve Linux-only code; do not blindly overwrite
> SteamOS files with Windows copies.

---

## 1. Repositories & paths

| What | Path / URL |
|------|------------|
| **GitHub (private)** | `https://github.com/dvahana2424-web/playzipdl` |
| **Windows 2.0 source of truth** | `D:\My Drive\Workspace\plazipanker` |
| **SteamOS port (in repo)** | `SteamOS port/QuickPlaySteamOS/` |
| **SteamOS build scripts** | `SteamOS port/build-appimage.sh`, `SteamOS port/requirements.txt` |
| **PlayZip Worker (Server 1)** | `worker/` → `dl-resolver` |
| **Anker Workers (Server 2)** | `worker-anker/` → `anker-resolver`, `anker-dlresolver` |
| **Anker docs** | `ANKER.md`, `anker/`, `worker-anker/README.md` |

**Known Steam Deck clone path (example):**

```
/home/deck/Downloads/quickplayPORT/QuickPlaySteamOS/
```

SteamOS port uses **`client_secrets.py`** inside `QuickPlaySteamOS/` (not `worker/.secrets.json` —
that file is deploy-time only on the PC that ran `wrangler secret put`).

---

## 2. What the SteamOS port already is (baseline)

From `playzipdl` → `SteamOS port/` (pre–QuickPlay 2.0):

- **Single store** — PlayZip only (`playzip_api.py`), no Anker / Server 2
- **Linux launcher** — pywebview + FastAPI (WebKit/GTK, not WebView2)
- **AppImage packaging** — `packaging/AppRun`, `quickplay.desktop`, `build-appimage.sh`
- **Steam integration** — `steam_shortcuts.py`, `steam_paths.py`, `steam_artwork.py`
- **No Windows-only bootstrap** — no mandatory Admin/UAC (`win_elevate.py` skipped or gated)
- **No Defender** — `defender_utils` hidden when `is_windows()` is false
- **7-Zip** — bundled `7zz` (Linux binary) via `app_paths.bundled_7z_path()`
- **Secrets** — `QuickPlaySteamOS/client_secrets.py` with `WORKER_URL`, `APP_TOKEN`, `SIGNING_SECRET`

### Typical SteamOS tree

```
SteamOS port/
├── README.md
├── build-appimage.sh
├── requirements.txt
└── QuickPlaySteamOS/
    ├── main.py                    # Linux entry — NO win_elevate
    ├── app_paths.py               # AppImage paths + 7zz
    ├── backend/server.py
    ├── web/                       # Same UI stack as Windows
    ├── playzip_api.py
    ├── game_details.py
    ├── download_service.py
    ├── steam_shortcuts.py         # LINUX ONLY — keep
    ├── steam_paths.py             # LINUX ONLY — keep
    ├── steam_artwork.py           # LINUX ONLY — keep
    ├── packaging/
    │   ├── AppRun
    │   ├── quickplay.desktop
    │   └── quickplay.png
    └── client_secrets.py          # git-ignored on device
```

---

## 3. What QuickPlay 2.0 adds (Windows — must port)

**Version:** `APP_VERSION = "2.0"` in `settings_manager.py`

### 3.1 Dual store (Server 1 / Server 2)

| UI label | Setting `store` | Client | License Worker | Download Worker |
|----------|-----------------|--------|----------------|-----------------|
| Server 1 | `server1` | `PlayZipClient` | `WORKER_URL` | (sign via `/sign`, client fetches link) |
| Server 2 | `server2` | `AnkerGamesClient` | `ANKER_LICENSE_WORKER_URL` | `ANKER_DL_WORKER_URL` → `/resolve` |

- User-facing strings: **Server 1 / Server 2** only — never show PlayZip or Anker in UI/logs.
- `store_manager.py` centralizes client selection, failover, license URL switching, SSE `store_changed`.

### 3.2 New / heavily changed files (copy or merge from `plazipanker`)

| File | Action |
|------|--------|
| `store_manager.py` | **ADD** (new) |
| `anker/anker_api.py` | **ADD** entire folder |
| `anker/anker_game_details.py` | **ADD** |
| `anker/config.example.py` | **ADD** |
| `anker/__init__.py` | **ADD** |
| `anker/FORMULA.md`, `anker/README.md` | **ADD** (docs) |
| `worker-anker/` | **ADD** (optional in AppImage runtime; needed for deploy docs) |
| `ANKER.md` | **ADD** (docs) |
| `settings_manager.py` | **MERGE** — add `store`, `APP_VERSION`, `store_options` |
| `backend/server.py` | **MERGE** — wire `StoreManager`, browse/search/settings |
| `download_service.py` | **MERGE** — inject `StoreManager`, Anker extract flags |
| `web/app.js` | **MERGE** — store dropdown, auto catalog reload |
| `web/index.html` | **MERGE** — store select + version label |
| `web/i18n.js` | **MERGE** — Server 1/2 strings (all langs) |
| `log_sanitize.py` | **MERGE** — `set_anker_sanitize()` |
| `archive_utils.py` | **MERGE** — `remove_store_junk` param |
| `idm_downloader.py` | **MERGE** — `_probe_download_url` (dlproxy HEAD 403 fix) |
| `library_manager.py` | **MERGE** — safe load, atomic save, backup restore, `merge_from_file`, `update_entry` |
| `library_scan.py` | **ADD** — scan download folder for missing install dirs |
| `library_artwork.py` | **ADD** — resolve listing poster URLs (Server 1 + 2 search) |
| `pending_exe_store.py` | **MERGE** — atomic save, `entry_id` field |
| `client_secrets.example.py` | **MERGE** — add `ANKER_*_WORKER_URL` |
| `license_manager.py` | **VERIFY** — uses runtime `WORKER_URL` (already switched by store) |
| `playzip_api.py` | **DIFF** — only if Windows changed mirror/rate-limit; prefer plazipanker if unsure |
| `game_details.py` | **DIFF** — minor; SteamOS may match already |

### 3.3 Server 2–only runtime behavior

When `store_manager.is_anker` is true:

1. **Archive rename** — strip `-AnkerGames` from downloaded filename before extract
2. **Post-extract cleanup** — remove junk HTML/BAT from repacks (`archive_utils.remove_store_junk`)
3. **Log sanitization** — `log_sanitize.set_anker_sanitize(True)`

### 3.4 Auto-failover

If Server 1 unreachable or search empty → auto-switch to Server 2 (persisted in `settings.json`).

Triggered in:

- `store_manager.ensure_startup_store()` (app start)
- `store_manager.browse()` / `search()` (runtime)

Frontend handles `store_switched` in API responses + SSE `store_changed`.

### 3.5 Anker browse / details fixes (critical for Server 2 UX)

| Feature | Implementation |
|---------|----------------|
| **Latest sort** | `GET /recent-updates` — **not** `?sort=latest` (upstream 500) |
| **Recent listings parser** | `_parse_recent_updates()` — `aria-label="Game update: …"` articles |
| **Game details** | JSON-LD `VideoGame` schema + Steam API merge (`anker_game_details.py`) |
| **Download gate** | Multi-hop `downloadPage('…')`; **dlproxy** URLs are final file URLs |
| **Download 403** | `idm_downloader._probe_download_url()` — HEAD 403 → GET `Range: bytes=0-0` |

### 3.6 UI: server switch must refresh catalog

In `web/app.js`:

- `#storeSelect` `change` → save settings + `reloadCategories()` + **`loadBrowse()`**
- `switchTab("browse")` alone is **not** enough — always call `loadBrowse()` after store change
- Close game detail modal on switch (`closeGameDetail()`)
- Reset `page`, `searchQuery`, `category`, re-render category chips

### 3.7 Library smart auto-heal (Windows 2.0 — port to SteamOS)

**Problem solved:** Games extracted on disk but missing from My Library; corrupt `library.json`; wrong/missing cover art after scan.

| Feature | Implementation |
|---------|----------------|
| **Safe load** | `library_manager.py` — JSON decode errors → backup as `library.json.corrupt.*.bak`, set `load_warning` |
| **Atomic save** | Temp file + `os.replace()` for `library.json` and `.quickplay_pending_exe.json` |
| **Auto-restore backup** | `LibraryManager.try_restore_from_backup()` — newest valid `.corrupt.*.bak` |
| **Legacy merge** | `merge_from_file()` — import from `download_dir/library.json` (old installs) |
| **Auto-import on startup** | `_auto_heal_library()` scans `download_dir` for game folders not in library → silent import |
| **Cover art** | `library_artwork.py` — search/listing poster first (never `og:image` hero); `store.get_client_for()` |
| **Artwork backfill** | Background thread after heal/import; `backfill_library_artwork(force=…)` |
| **EXE picker optional** | Game added to library **before** EXE confirm; **Later** clears pending, no restart prompt |
| **Pending EXE persist** | `.quickplay_pending_exe.json` in download folder; `entry_id` links to library row |
| **Startup reconcile** | `_reconcile_pending_exe_with_library()` — recover library rows for old pending state |

**API routes (backend):**

| Route | Role |
|-------|------|
| `GET /api/library` | Returns `entries`, `load_warning`, `library_path`, `heal_summary` |
| `POST /api/library/reconcile` | Manual re-sync (optional **Re-scan folder** button) |
| `POST /api/library/scan` | Same heal + returns remaining candidate preview |
| `POST /api/library/import` | Explicit import (legacy; auto-heal makes this optional) |
| `POST /api/library/refresh-artwork` | Force cover art backfill |

**UI (user-facing):**

- **No manual Scan required** — heal runs on every startup and when download folder changes (`apply_settings`).
- **Re-scan folder** — optional subtle button; no confirm dialog; toast only.
- **Warning banner** — only if `library_corrupt_unrecoverable` (auto-heal failed completely).
- **Toasts:** `library.healImported`, `library.healRestored`, `library.healUpToDate` (en/zh/es/tl).

**SSE:**

| Event | UI action |
|-------|-----------|
| `library_healed` | Toast if imported/restored; refresh library grid |
| `library_updated` | Refresh library + browse “in library” badges |

**Data locations (same as Windows — see AGENTS.md):**

- `library.json` → next to EXE / AppImage root (`app_root_dir()`)
- Download queue + pending EXE → user `download_dir`

**EXE picker on restart:**

- **Later / Skip** → pending cleared; **no modal** on next start; game stays in library (best-guess EXE).
- **Dismiss without Later** → pending saved; **no auto-modal** on start; Downloads tab badge + “Choose EXE” banner.
- **PLAY without valid EXE** → opens library EXE picker.

---

## 4. Agent workflow (recommended order)

### Phase A — Prep

1. Clone / open `playzipdl` and create branch, e.g. `feature/quickplay-2-steamos`.
2. Open parallel reference: `plazipanker` (Windows 2.0).
3. Read SteamOS `SteamOS port/README.md` and `QuickPlaySteamOS/main.py` — note Linux entry differences.
4. Read this file + `ANKER.md` + `anker/FORMULA.md`.

### Phase B — Copy new modules

```bash
# From plazipanker → SteamOS port/QuickPlaySteamOS/
cp store_manager.py          → QuickPlaySteamOS/
cp -r anker/                 → QuickPlaySteamOS/anker/
cp ANKER.md                  → SteamOS port/   # or repo root
cp -r worker-anker/          → repo root (sibling to SteamOS port)  # docs/deploy only
```

Update `client_secrets.example.py` and document that existing Deck `client_secrets.py` needs:

```python
WORKER_URL = "https://dl-resolver.hs2424.workers.dev"
ANKER_LICENSE_WORKER_URL = "https://anker-resolver.hs2424.workers.dev"
ANKER_DL_WORKER_URL = "https://anker-dlresolver.hs2424.workers.dev"
APP_TOKEN = "..."
SIGNING_SECRET = "..."
```

### Phase C — Merge shared files (DO NOT clobber Linux code)

For each file below, use **three-way merge**: SteamOS base + plazipanker changes + keep Linux branches.

#### `backend/server.py`

From plazipanker, port:

- `from store_manager import StoreManager, store_label`
- `init_backend()`: create `StoreManager`, `ensure_startup_store()`, pass `store` to `DownloadService`
- Routes: `/api/browse`, `/api/search`, `/api/categories`, `/api/settings` use `get_store()` not bare `_client`
- `PUT /api/settings`: call `store_mgr.set_store()` when `store` changes
- Keep SteamOS-specific routes if any (Steam shortcuts API, disk space, etc.)

#### `download_service.py`

From plazipanker:

- Constructor takes `store: StoreManager`
- `self.client` → `self.store.get_client()`
- `_resolve_and_queue` uses store client `get_download_url(game_id, title)`
- Extract: `remove_store_junk=self.store.is_anker`
- On init/resume: `self.store.set_store(self.settings.store, persist=False)`
- **`_auto_heal_library()`** on init + `apply_settings()` — corrupt restore, legacy merge, auto-import, artwork queue
- **`_post_extract()`** — always `library.add()` before EXE picker; `pick_best_exe()` as default
- **`skip_exe_picker()`** — dismiss pending; game remains in library
- **`reconcile_library()`** / **`backfill_library_artwork()`** — manual + background cover art

**Linux note:** game launch via Proton/Wine stays in SteamOS code — do not import `win_elevate`.

#### `settings_manager.py`

Add:

```python
APP_VERSION = "2.0"
STORE_SERVER1 = "server1"
STORE_SERVER2 = "server2"
DEFAULT_STORE = STORE_SERVER1
# AppSettings.store: str = DEFAULT_STORE
# _normalize_store(), set_store(), to_dict() → store_options from STORE_OPTIONS
```

#### `main.py` (SteamOS)

**Keep** Linux version. Ensure:

- Does **not** call `ensure_admin_or_exit()` (Windows only)
- Does **not** require Defender warnings
- Still calls `init_backend()` (which now creates StoreManager)
- `NativeApi.pick_folder` — Linux file dialog via pywebview (already different from Windows `cmd /c start`)
- `NativeApi.open_url` — use `webbrowser.open()` or `xdg-open` on Linux, not Windows `cmd`

If plazipanker `main.py` has fixes (e.g. announcement), merge logic only, not Windows shell calls.

#### `web/*`

Merge from plazipanker:

- `index.html` — `#storeSelect`, `#appVersionLabel`, `#libraryScanBtn` (Re-scan), `#libraryWarning`, `#libraryPathHint`
- `i18n.js` — `settings.storeLabel`, `settings.storeHint`, `library.heal*`, `library.rescan*`, `library.warnUnrecoverable*`
- `app.js` — `populateStoreSelect`, store SSE handler, browse reload fix, **`showLibraryHealToast`**, **`library_healed` SSE**, optional re-scan (no confirm dialog)

#### `idm_downloader.py`

**Must merge** `_probe_download_url` and `_content_length_from_probe` — Server 2 dlproxy downloads fail without this on Linux too.

#### `archive_utils.py` + `log_sanitize.py`

Merge Anker junk removal and log sanitization from plazipanker.

### Phase D — Packaging

1. **`requirements.txt`** — ensure `requests`, `fastapi`, `uvicorn`, `pywebview` versions match; no Windows-only deps.
2. **`build-appimage.sh`** — add `anker/` to data files copied into AppImage (mirror `QuickPlay.spec` datas).
3. **Hidden imports** — if using PyInstaller on Linux, include:
   - `store_manager`, `anker`, `anker.anker_api`, `anker.anker_game_details`, `anker.config`, `client_secrets`
   - `library_manager`, `library_scan`, `library_artwork`
4. **`app_paths.py`** — keep Linux `7zz` logic; optionally add `.png` icon fallback for Steam grid.

### Phase E — Steam-specific (preserve & test)

Do **not** delete or overwrite without review:

| File | Role |
|------|------|
| `steam_shortcuts.py` | Add non-Steam games to Steam library |
| `steam_paths.py` | Resolve Steam install / userdata paths on Deck |
| `steam_artwork.py` | Custom artwork for Steam shortcuts |

After port, verify **Add to Steam** / PLAY still works for both Server 1 and Server 2 games.

### Phase F — Test matrix (Steam Deck or Linux VM)

| # | Test | Expected |
|---|------|----------|
| 1 | Fresh start, Server 1 | Browse loads PlayZip catalog |
| 2 | Settings → Server 2 | Browse **immediately** shows different titles |
| 3 | Server 2 → Latest | `/recent-updates` list (not same as Views) |
| 4 | Server 2 → open Cuphead (or any) | Trailer, screenshots, description |
| 5 | Server 2 → download large game | No 403 at start; progress > 0% |
| 6 | Server 1 → download | Still works via `/sign` |
| 7 | License gate | Unlicensed → registration dialog |
| 8 | Switch server mid-browse | Modal closes, grid refreshes |
| 9 | Auto-failover | Block Server 1 DNS → starts on Server 2 |
| 10 | AppImage relaunch | Resume queue, `settings.store` preserved |
| 11 | Steam shortcut | Game launches via Proton after EXE pick |
| 12 | Delete library row, restart | Auto-import from download folder + toast |
| 13 | Corrupt `library.json` | Auto-restore from `.corrupt.*.bak` or scan-import |
| 14 | Scan-import cover art | Vertical listing poster (not og:image hero) |
| 15 | EXE picker → Later, restart | No modal; game in library; EXE via button or PLAY |

---

## 5. File-by-file diff checklist

Use `diff -ru SteamOS/QuickPlaySteamOS/ plazipanker/` (excluding Windows-only files).

### Windows-only — do NOT port to SteamOS runtime

| File | Reason |
|------|--------|
| `win_elevate.py` | UAC / Admin |
| `playzip.manifest` | Windows manifest |
| `defender_utils.py` | Keep file but UI hidden via `is_windows()` |
| `QuickPlay.spec` / `build.ps1` | Use `build-appimage.sh` instead |
| `native_dialog.py` | Windows tkinter bootstrap — SteamOS may use simpler startup |

### Must match plazipanker (functional parity)

- [ ] `store_manager.py`
- [ ] `anker/anker_api.py` (incl. `_browse_path` latest → `/recent-updates`, `_resolve_gate_to_cdn`, `_parse_recent_updates`)
- [ ] `anker/anker_game_details.py` (JSON-LD parser)
- [ ] `idm_downloader.py` (probe HEAD 403)
- [ ] `download_service.py` (store injection)
- [ ] `backend/server.py` (store routes)
- [ ] `settings_manager.py` (store + v2.0)
- [ ] `web/app.js` (store change → loadBrowse)
- [ ] `web/i18n.js`
- [ ] `web/index.html`
- [ ] `log_sanitize.py`
- [ ] `archive_utils.py` (`remove_store_junk`)
- [ ] `library_manager.py` (safe load, atomic save, backup restore)
- [ ] `library_scan.py`
- [ ] `library_artwork.py`
- [ ] `pending_exe_store.py` (atomic save, `entry_id`)
- [ ] `client_secrets.example.py`

---

## 6. Cloudflare Workers (no SteamOS-specific deploy)

Workers run on Cloudflare — **same** for Windows and SteamOS clients.

| Worker | URL (production) |
|--------|------------------|
| dl-resolver | `https://dl-resolver.hs2424.workers.dev` |
| anker-resolver | `https://anker-resolver.hs2424.workers.dev` |
| anker-dlresolver | `https://anker-dlresolver.hs2424.workers.dev` |

Deploy from Windows/Linux PC with wrangler — **not** from Steam Deck required.

Secrets on Workers (`QUICKPLAY_HWID_PAT`, etc.) are set via Cloudflare dashboard / `wrangler secret put` — **not** shipped in AppImage.

---

## 7. Settings schema change

`settings.json` new field:

```json
{
  "store": "server1",
  "language": "en",
  "download_dir": "/home/deck/games",
  ...
}
```

Migration: if `store` missing, default to `server1`. Existing SteamOS users stay on Server 1 until they switch.

---

## 8. SSE events (frontend must handle)

| Event | Payload | UI action |
|-------|---------|-----------|
| `store_changed` | full settings dict incl. `store`, `store_label` | repopulate select, close detail, reload categories + browse |
| `settings_updated` | settings dict | refresh settings form if open |
| `library_healed` | `{ auto_imported, merged_legacy, restored_backup, recovered_corrupt, silent, entry_count }` | toast + refresh library |
| `library_updated` | `{}` | refresh library grid / browse badges |

---

## 9. Known gotchas (from Windows 2.0 debugging)

1. **`sort=latest` on Anker `/games`** → HTTP 500. Use `/recent-updates`.
2. **dlproxy URLs** — final download URL; do not fetch as HTML again (433MB mistake).
3. **HEAD 403 on dlproxy** — normal; use ranged GET for size probe.
4. **Store switch without `loadBrowse()`** — catalog looks stale; user reported this bug on Windows.
5. **Cache method naming** — in `store_manager`, cached clients are `_cached_playzip` / `_cached_anker`, methods `_get_playzip_client()` / `_get_anker_client()` (never name attribute same as method).
6. **`client_secrets.py` on Deck** — may only have `WORKER_URL`; add Anker URLs for Server 2 downloads/license.
7. **PyArmor** — Windows protected build obfuscates modules; SteamOS port is usually **plain Python** in AppImage — ensure `anker_api._worker_settings()` reads `client_secrets` at runtime.
8. **Library cover art** — use browse/search `image_url` (vertical poster), not game-page `og:image` (wide hero/screenshot).
9. **Auto-heal on startup** — do not require manual “Scan folder”; keep Re-scan as optional advanced action.
10. **`library.json` location** — AppImage root / config dir, **not** download folder (same rule as Windows).

---

## 10. Build AppImage (after merge)

```bash
cd "SteamOS port"
chmod +x build-appimage.sh
./build-appimage.sh
# Output: QuickPlay-*.AppImage (see README in SteamOS port)
```

Before building:

- [ ] `client_secrets.py` present locally (git-ignored) with all three Worker URLs
- [ ] `vendor/7zip/7zz` bundled for Linux
- [ ] `anker/` included in bundle datas
- [ ] Test `python3 main.py` from `QuickPlaySteamOS/` on Deck first

---

## 11. Suggested commit structure

1. `feat(steamos): add store_manager and anker module from QuickPlay 2.0`
2. `feat(steamos): merge dual-store backend and settings`
3. `feat(steamos): merge web UI Server 1/2 selector and catalog reload`
4. `fix(steamos): idm_downloader dlproxy HEAD 403 probe`
5. `fix(steamos): library auto-heal, scan-import, and cover art resolution`
6. `docs(steamos): ANKER.md and update SteamOS port README`
7. `build(steamos): AppImage bundles anker package`

---

## 12. Reference docs (plazipanker)

| Document | Purpose |
|----------|---------|
| [ANKER.md](../ANKER.md) | Server 2 architecture overview |
| [anker/FORMULA.md](../anker/FORMULA.md) | Anker HTTP API recipe |
| [anker/README.md](../anker/README.md) | Python module + CLI tests |
| [worker-anker/README.md](../worker-anker/README.md) | Worker deploy + endpoints |
| [AGENTS.md](../AGENTS.md) | Full app maintainer guide (Windows-focused but architecture applies) |

---

## 13. Agent completion criteria

Port is **done** when:

- [ ] SteamOS AppImage runs on Deck with **Version 2.0** in Settings
- [ ] Server 1 and Server 2 both browse, show details, and download
- [ ] Latest sort works on Server 2
- [ ] Store switch refreshes catalog without manual tab hack
- [ ] Steam shortcut / PLAY flow still works
- [ ] No PlayZip/Anker branding in user-visible UI
- [ ] Library auto-heal on startup (corrupt restore + missing folder import)
- [ ] Optional Re-scan folder works; cover art uses listing posters
- [ ] EXE picker Later does not re-prompt on restart
- [ ] `SteamOS port/README.md` updated with 2.0 notes and `client_secrets` template
- [ ] This file updated with any SteamOS-specific deviations discovered during port

---

*Last updated: 2026-08-04 — includes library smart auto-heal, cover art fix, and optional Re-scan from QuickPlay 2.0 Windows (`plazipanker`).*
