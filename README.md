# QuickPlay 2.7.9

Web-based desktop GUI to browse, download (IDM-style), auto-extract, and manage a games library with a PLAY button.

**Branch:** `quickplay-2.7.9-beta` · **Version:** `2.7.9` (window title: QuickPlay 2.7.9)

**Stack:** Python (FastAPI) + HTML/CSS/JS + pywebview (WebView2 on Windows)

**Workspace:** `D:\My Drive\Workspace\plazipanker`

## Features

- **Dual store (public)** — Settings → **Server 1** / **Server 2** (PlayZip + Anker on **ankergames.to**)
- **Browse Games** — CSS grid with lazy loading, skeleton, gradient overlay
- **Category filters** — Shooter, R18+, Racing, Action, RPG, etc. + Latest/Views sort
- **Search** — find games by title
- **Multi-language UI** — English (default), 中文, Español, Tagalog (Settings → Language)
- **Licensed downloads (Windows)** — registration code + device fingerprint for Custom OS; seller activation required before first download
- **Settings** — download folder, connections (8–64), show/hide logs, Defender exclusion (Windows), language
- **IDM-style download** — configurable connections (8–64), in-place multi-part writes (no merge phase), pause/resume/cancel, auto-resume on restart
- **Refresh download link** — fetch a new CDN URL mid-download (keeps partial files) with live resolve status in the UI
- **Auto extract** — after download, unpack `.7z/.zip/.rar` into the games folder
  - **Bundled 7-Zip** — no separate 7-Zip install required (`vendor/7zip/`)
- **EXE picker** — scan `.exe` files, pick the PLAY button target
- **My Library** — PLAY, change EXE, delete game (with confirmation)
- **Single instance** — one process only; second launch shows a themed notice
- **Administrator required** — Run as Admin on every start (Defender exclusions + reliable downloads)
- **Themed bootstrap dialogs** — English, dark UI matching the app (admin, single instance, Defender help)

## Requirements (Windows)

- Windows 10/11
- **Run as Administrator** (UAC) — mandatory for Defender folder exclusions
- WebView2 runtime (usually preinstalled)

## Dev run

```powershell
cd "D:\My Drive\Workspace\plazipanker"
pip install -r requirements.txt
copy client_secrets.example.py client_secrets.py   # fill in Worker URLs + tokens
python main.py
```

## Build EXE (Windows)

```powershell
cd "D:\My Drive\Workspace\plazipanker"
python -m PyInstaller --noconfirm --clean QuickPlay.spec
# → dist\QuickPlay.exe
```

**Standard build script** (if present):

```powershell
.\build.ps1
```

**Protected release build** (PyArmor obfuscation on licensing, API secrets, and entrypoint):

```powershell
.\build-protected.ps1
```

Obfuscates: `client_secrets.py`, `hwid_obfuscation.py`, `license_manager.py`, `playzip_api.py`, `device_fingerprint.py`, `hardware_snapshot.py` (plain `main.py` stays readable for PyInstaller import tracing).

Output: `dist\QuickPlay.exe` (UAC / Administrator required)

Close any running QuickPlay instance before rebuilding (elevated EXE cannot be overwritten while running).

## Folder layout (app root)

```
plazipanker/
├── QuickPlay.exe
├── library.json                  # installed games list (stays in app root)
├── settings.json                 # app settings (incl. language, store server)
├── cache/covers/                 # optional cover cache
├── vendor/7zip/                  # bundled 7-Zip
└── {Game Name}/                  # extracted game folders (in download dir)
```

In the **download folder**: `.quickplay_downloads.json` (resume queue),
`.quickplay_pending_exe.json` (pending EXE picker), partial `.part` / `.part.partN` files during downloads, and extracted game archives.

## Project structure

```
plazipanker/
├── main.py                  # pywebview launcher + admin + single-instance guard
├── store_manager.py         # Server 1 / Server 2 selection + auto-failover
├── backend/server.py        # FastAPI API + SSE
├── web/                     # HTML/CSS/JS UI (+ i18n.js)
├── download_service.py      # downloads, extract, library orchestration
├── playzip_api.py           # Server 1 upstream client + Worker /sign
├── anker/                   # Server 2 client + FORMULA.md (see ANKER.md)
├── worker/                  # PlayZip Cloudflare Worker (dl-resolver)
├── worker-anker/            # Anker Cloudflare Workers (license + /resolve)
├── license_manager.py       # registration code UI + license gate helpers
├── client_secrets.example.py
├── RELEASE_NOTES.md
├── PROGRESS_REPORT.md
├── ANKER.md                 # Server 2 + Workers overview
└── AGENTS.md                # agent / maintainer guide
```

## Licensing & activation

1. User attempts first download → Worker returns `license_required`
2. QuickPlay shows **Registration Code** (and **Device Fingerprint** on Custom OS)
3. User sends code (+ fingerprint if Custom OS) to seller
4. Seller activates via **Unified Activator** (desktop or Android)
5. User retries download — Worker signs link, download proceeds
6. First licensed download may record a one-time hardware snapshot (discreet, server-side)

See [worker/README.md](worker/README.md) (Server 1) and [worker-anker/README.md](worker-anker/README.md) (Server 2).

## Download-link signing (Cloudflare Workers)

| Server | Workers folder | Client secret keys |
|--------|----------------|-------------------|
| Server 1 | `worker/` | `WORKER_URL` |
| Server 2 | `worker-anker/` | `ANKER_LICENSE_WORKER_URL`, `ANKER_DL_WORKER_URL` |

Server 1 signing is delegated to **dl-resolver** so PlayZip secrets stay off the client.
Server 2 uses **anker-dlresolver** for licensed resolve; browse stays client-side.

See [AGENTS.md](AGENTS.md), [ANKER.md](ANKER.md), [worker/](worker/), [worker-anker/](worker-anker/).

`client_secrets.py` is **git-ignored** — use `client_secrets.example.py` as a template.

## Flow

1. **Browse** → game details → **Download**
2. If not activated → copy registration code → seller activates → retry
3. When complete → auto-extract to `{download folder}/{Game Name}/`
4. EXE picker → **Set as PLAY**
5. **My Library** → **▶ PLAY**

## Docs

| File | Purpose |
|------|---------|
| [ANKER.md](ANKER.md) | Server 2 architecture, Workers, client secrets |
| [anker/README.md](anker/README.md) | Anker Python module + smoke tests |
| [anker/FORMULA.md](anker/FORMULA.md) | Anker upstream HTTP API recipe |
| [worker-anker/README.md](worker-anker/README.md) | Anker Worker deploy + endpoints |
| [worker/README.md](worker/README.md) | PlayZip dl-resolver Worker |
| [RELEASE_NOTES.md](RELEASE_NOTES.md) | Version history |
| [PROGRESS_REPORT.md](PROGRESS_REPORT.md) | Implementation status & deployment notes |
| [AGENTS.md](AGENTS.md) | Maintainer / AI agent guide |
| [AGENTICSTEAMOSTODOPORT.md](AGENTICSTEAMOSTODOPORT.md) | SteamOS port merge guide (QuickPlay 2.7.5 beta → Deck) |
