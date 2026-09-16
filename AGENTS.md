# AGENTS.md — QuickPlay

Guide for AI agents debugging/maintaining this project. Read this first before
touching download, resume, library, or build logic.

> **Branding:** The app is shipped as **QuickPlay** (window title, UI, and
> `dist/QuickPlay.exe`). It is based on playzip.com but the playzip origin is
> intentionally hidden from end users. Keep user-visible strings/filenames
> QuickPlay-branded; playzip references are internal only.

## What this app is

A Windows desktop app (**QuickPlay 2.7.5 beta**) that browses two game catalogs
(**Server 1** = playzip mirrors, **Server 2** = ankergames.net), downloads
games with an IDM-style multi-connection downloader, auto-extracts archives,
and manages an installed-games library with a PLAY button.

User-facing labels are **Server 1 / Server 2** only — hide PlayZip/Anker branding.

- **Stack:** Python 3.14 + FastAPI (backend) + pywebview/WebView2 (shell) +
  vanilla HTML/CSS/JS (`web/`).
- **Packaging:** PyInstaller one-file EXE via `build.ps1` (dev) or `build-protected.ps1`
  (PyArmor on licensing/API modules) → `dist/QuickPlay.exe`. Protected spec: `release.spec`.
  (UAC) because of Windows Defender exclusion handling (`playzip.manifest`,
  `win_elevate.py`).

## How to run / build

```powershell
cd "D:\My Drive\Workspace\plazipanker"
pip install -r requirements.txt
python main.py        # dev run
python -m PyInstaller --noconfirm --clean QuickPlay.spec   # → dist\QuickPlay.exe
.\build.ps1           # standard build → dist\QuickPlay.exe (if script present)
.\build-protected.ps1 # PyArmor-protected build (licensing/API modules obfuscated)
```

Build gotcha: PyInstaller fails with `PermissionError: [WinError 5] Access is
denied: ...QuickPlay.exe` if the app is still running. The running instance is
often elevated (Admin), so it usually cannot be killed programmatically — ask
the user to close it manually, then rebuild.

## Architecture map

| File | Responsibility |
|------|----------------|
| `main.py` | pywebview launcher, starts FastAPI, opens window |
| `backend/server.py` | FastAPI routes, SSE event stream, `init_backend()` wires globals, calls `resume_pending_downloads()` on startup |
| `download_service.py` | Orchestration: resolve URL → download → extract → library → EXE picker. Owns task state (`_tasks`, `_task_views`, `_task_meta`, `_downloaders`, `_pending_exe`) |
| `idm_downloader.py` | Multi-connection HTTP download with pause/resume/retry |
| `download_queue.py` | Persists in-progress downloads to `.quickplay_downloads.json` (in the **download folder**). Auto-migrates the legacy `.playzip_downloads.json` name |
| `pending_exe_store.py` | Persists pending EXE-picker states to `.quickplay_pending_exe.json` (auto-migrates legacy `.playzip_pending_exe.json`) |
| `library_manager.py` | `library.json` CRUD, folder-name sanitizing, HTML-entity decoding |
| `store_manager.py` | Dual-store manager: **Server 1** (`PlayZipClient`) / **Server 2** (`AnkerGamesClient`), license Worker URL switching, browse/search failover, `store_changed` SSE |
| `settings_manager.py` | `settings.json`; `library_path` pinned to app root; `store` (`server1`/`server2`); `APP_VERSION` / `APP_TITLE` (e.g. `2.7.5-beta`) |
| `playzip_api.py` | Server 1 upstream: browse/search/resolve download URL, mirror failover, Worker `/sign` |
| `anker/anker_api.py` | Server 2 upstream: browse/search, Worker `/resolve` or local CSRF formula |
| `anker/anker_game_details.py` | Server 2 game detail panel (JSON-LD + Steam API) |
| `client_secrets.py` | **git-ignored**. `WORKER_URL` (S1), `ANKER_LICENSE_WORKER_URL` + `ANKER_DL_WORKER_URL` (S2), `APP_TOKEN`, `SIGNING_SECRET`. Template: `client_secrets.example.py` |
| `archive_utils.py` | 7z/zip/rar extraction. Prefers the **bundled** 7-Zip (`vendor/7zip/7z.exe`) → system PATH → common install paths, so users need no 7-Zip install |
| `single_instance.py` | Windows named-mutex guard; a 2nd launch shows a themed notice and exits so resume/download flows never collide |
| `native_dialog.py` | Themed tkinter dialogs (admin required, single instance, Defender warning) matching `web/` dark theme — used **before** pywebview starts |
| `win_elevate.py` | **Mandatory** Administrator on every non-elevated start (Run as Admin / Exit); no bypass |
| `defender_utils.py` | Auto-add download folder to Defender exclusions; startup warning + manual steps if auto fails |
| `license_manager.py` | One-time startup license probe (`perform_first_startup_license_check`), registration dialog, HWID helpers |
| `device_fingerprint.py` | 64-char SHA-256 device fingerprint (UUID + MachineGuid + disk + BIOS) for Custom OS manual activation |
| `hwid_obfuscation.py` | 42-char registration code encode/decode (Hammer-compatible) |
| `hardware_snapshot.py` | One-time dxdiag-like snapshot sent on first licensed `/sign` until `hardware_snapshot_submitted` flag is set |
| `announcement.py` / `landing_page.py` | Fetch startup announcement text / "follow our page" URL from GitHub |
| `path_utils.py` | Windows long-path helpers, `remove_tree()` for deleting game folders |
| `web/app.js`, `index.html`, `style.css` | UI; `web/i18n.js` holds translations (en/zh/es/tl); store dropdown + auto catalog reload on server change |
| `worker/` | Server 1 Cloudflare Worker (`dl-resolver`). Deploy with `wrangler deploy` from that folder |
| `worker-anker/` | Server 2 Workers (`anker-resolver`, `anker-dlresolver`). See `worker-anker/README.md` |
| `ANKER.md` | Server 2 architecture overview (Workers + client + docs index) |
| `legacy_main_tk.py` | Old Tkinter UI, kept only as backup — do not extend |

### Internationalization (i18n)

- `web/i18n.js` exposes `t(key, params)` and `applyI18n()`. Static markup is
  tagged with `data-i18n` / `data-i18n-ph` / `data-i18n-title` / `data-i18n-html`.
- Language is stored in `settings.json` (`language`), applied on load and live on
  change (no restart). Add a language by extending `I18N_STRINGS` +
  `SUPPORTED_LANGUAGES` in `settings_manager.py`.

## Data & state files (IMPORTANT: two locations)

1. **App root** (next to the EXE / repo): `settings.json`, `library.json`,
   `cache/`.
   - `library.json` lives here **on purpose** so the installed-games list
     survives changing the download folder. Each entry stores an absolute
     `install_dir`.
2. **Download folder** (user-chosen, e.g. `G:\`): downloaded archives,
   extracted game folders, `.part`/`.part.partN` fragments,
   `.quickplay_downloads.json` (resume queue), `.quickplay_pending_exe.json`.

When debugging "missing" data, always check BOTH locations, and check the
current `download_dir` in `settings.json` first.

## Download & resume model (most bug-prone area)

- Multi-connection: workers write into one pre-allocated `<dest>.part` at byte
  offsets (no end-of-download merge pass). Resume uses `<dest>.part.progress`
  (per-range `done[]`). Legacy `<dest>.part.partN` files are migrated once on
  resume. Single-connection still uses a growing `<dest>.part`.
- On completion, `os.replace()` `<dest>.part` → final `<dest>`.
- Signed download URLs from playzip **expire**, so on resume we always
  re-resolve a fresh URL (`_resolve_and_queue(..., force_dest=..., force_connections=...)`)
  while keeping the original `dest_path` so existing fragments are reused.
- `resume_pending_downloads()` (startup) reads `.quickplay_downloads.json`.
  A download is treated as **finished only when the final file exists AND no
  `.part`/`.part.partN` fragments remain**. This guard exists because a stale
  completed file with the same name (from a previous download) must not cancel
  a resume — that was a real bug (Chill with You). See the `has_partial` check
  in `download_service.resume_pending_downloads`.
- Transient errors (`IncompleteRead`, `ConnectionError`, `Timeout`,
  `ProtocolError`) are retried with backoff: `MAX_RETRIES = 8`,
  `RETRY_BACKOFF = 3.0` in `idm_downloader.py`.
- Rate-limit resolve loop is capped at `max_rate_limit_retries = 8` in
  `_resolve_and_queue`; after that the task goes to an error state with a Retry
  button instead of looping forever.

## Download-link signing (Cloudflare Worker)

The upstream download API requires a per-request digest:
`SHA256(timestamp + game_id + PLAYZIP_SECRET_KEY)`. To keep that secret out of
the distributed EXE, signing is delegated to a Cloudflare Worker.

- **Worker source lives in [`worker/`](worker/)** in this repo. Deploy with
  `wrangler deploy` from that folder. Only the algorithm/recipe *shape* is in the
  code — the secret *values* stay in Cloudflare (`wrangler secret`), never
  committed. **Keep the GitHub repo private**, since the recipe shape + worker
  URL are documented there.
- **Worker endpoint:** `POST /sign { game_id, hwid, device_fp, ts, nonce, sig, token, hw_snapshot? }` →
  `{ timestamp, digest, cookie, snapshot_recorded? }` or `403 license_required` (+ `custom_os`).
  The `SECRET_KEY` and hashing algorithm never leave the server.
- **Auth:** client sends `token` (APP_TOKEN) + `sig` =
  `HMAC-SHA256(SIGNING_SECRET, "{ts}.{nonce}.{game_id}.{hwid}.{device_fp}")`. Worker verifies
  token + timestamp window (±120s) + HMAC (timing-safe), plus KV-backed nonce
  replay dedupe and per-IP rate limiting.
- **Licensing:** Worker checks HWID file in `hammerwebsite12/quickplayusr` via
  `QUICKPLAY_HWID_PAT` (needs **read + write**). Manual-approve `.user` files include
  `Manual-Approved: quickplay-win` + `Device-FP:` (see activateme / Unified Activator).
- **HW snapshot:** `worker/src/hwid_snapshot.js` — one-time append on first licensed sign.
- **Why the client still calls playzip directly (hybrid model):** playzip
  **blocks datacenter/Cloudflare IPs** on the download API (returns a "403
  Forbidden" HTML page). So the Worker cannot fetch the link itself. Instead the
  Worker only *signs*, and the client (residential IP) performs the actual
  warmup GET + `POST /api/getGamesDownloadUrl` using the returned digest+cookie.
- **Consequence:** the playzip domain is still visible in the client's network
  traffic (only the signing secret is hidden). Fully hiding the source would
  require routing the upstream call through a residential proxy.
- **Worker secrets** (set via `wrangler secret put` / `secret bulk`):
  `APP_TOKEN`, `SIGNING_SECRET`, `PLAYZIP_SECRET_KEY`, `PLAYZIP_AUTH_COOKIE`,
  `QUICKPLAY_HWID_PAT`, `DISCORD_WEBHOOK_URL` (optional).
  `client_secrets.py` (client) must match `WORKER_URL` + `APP_TOKEN` +
  `SIGNING_SECRET`. Rotate by updating both sides.
- **Note:** browse/search work with NO cookies; only the download API path
  needs the gate cookie, which the Worker returns.
- The actual game archive is served from a CDN (e.g. `cdn1.trashbytes.to`),
  not playzip itself.

## Server 2 (Anker) — Workers + client

See **[ANKER.md](ANKER.md)**, **[anker/FORMULA.md](anker/FORMULA.md)**, **[worker-anker/README.md](worker-anker/README.md)**.

- **Browse/search/details:** client → ankergames.net directly (no Worker).
- **License check:** `anker-resolver` `POST /license/check` when Server 2 active
  (`ANKER_LICENSE_WORKER_URL` in `client_secrets.py`).
- **Download resolve:** `anker-dlresolver` `POST /resolve` → CDN / dlproxy URL;
  fallback local formula in `anker_api.py` when Worker URL unset.
- **Gate / dlproxy:** Anker gate HTML embeds next hop via `downloadPage('...')`.
  **dlproxy** URLs (`*.dlproxy.uk/download/...`) are the final file — HEAD often
  403, GET works; `idm_downloader._probe_download_url()` handles this.
- **Latest sort:** `GET /recent-updates` (not `?sort=latest` — upstream 500).
- **Server 2 only:** zip rename without `-AnkerGames`, post-extract junk cleanup,
  log sanitization (`log_sanitize.set_anker_sanitize`).

## Task lifecycle / UI sync

- Backend emits SSE events (`task_update`, `library_updated`, `exe_picker`,
  `settings_updated`). `task_update` payload may carry `cancelled: true` or
  `removed: true` — the frontend deletes the task in both cases.
- `_remove_task()` clears a finished task from all backend collections and
  emits `removed`. Called after a game is added to the library or an EXE picker
  is created (extraction done = download dock entry should disappear).
- Frontend `loadTasks()` does an **authoritative rebuild** of `state.tasks`
  from the backend, and `connectSSE()` calls it on (re)connect to kill phantom
  "Resolving download link..." entries.

## Known fixed bugs (don't regress these)

- HTML entities in titles (`Assassin&#39;s`): decoded server-side
  (`html.unescape` in `playzip_api.py`, `library_manager.py`,
  `pending_exe_store.py`) and client-side (`decodeHtml`/`text`/`displayText`
  in `app.js`).
- Deleting a library game must also delete its folder (`remove_tree` in
  `delete_entry`).
- Library disappearing when download folder changed: fixed by pinning
  `library_path` to app root.
- Download folder not applied: `web/app.js` auto-saves the chosen folder via
  `PUT /api/settings` immediately after picking it.
- Interrupted downloads must auto-resume on restart (see resume model above).
- Cancel download must call `_remove_task()` and `_cleanup_part_files()` — use
  `glob.escape(part_path)` when globbing `.part.partN` (dots in filenames like
  `Tale.of.Immortal.rar` are glob wildcards otherwise).
- Pause/resume: `on_status` must sync `TaskView.state` for Paused/Downloading;
  `idm_downloader` uses 32 KB chunks for responsive pause.
- Non-elevated launches must show native admin dialog (not proceed without Admin).
- **First startup only:** `perform_first_startup_license_check()` calls Worker
  `POST /license/check` once; result stored in `settings.json` as
  `first_license_check_done`. No internet on first launch → warning dialog, flag
  **not** set (retries next start). After flag is set, no online check at launch;
  license still enforced on **download** via `/sign`.

## Debugging checklist

1. Logs are in-memory only (see the Downloads tab log panel); there is no log
   file. Check `settings.json` for the active `download_dir`.
2. For resume issues: inspect the download folder for `.quickplay_downloads.json`
   (the queue), leftover `.part`/`.part.partN` fragments, and any same-named
   final file.
3. For "stuck resolving"/phantom tasks: check `_task_views` vs. what SSE emits;
   confirm `loadTasks()` rebuild and `removed` handling.
4. Reproduce interrupted-download: start a large download, force-close, relaunch
   — it should re-resolve a fresh link and continue from saved bytes.
