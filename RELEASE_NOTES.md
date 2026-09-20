# QuickPlay Release Notes

## v2.7.8 — September 20, 2026

### Download UX
- **Instant pre-allocation** — multi-GB `.part` files no longer block on Windows zero-fill during "Preparing download file…"
- **Preparing indicator** — green progress bar, reserved size, free disk display
- **Readable sizes** — e.g. `1.2 GB out of 68 GB` (i18n: en / zh / es / tl)
- **Interrupted downloads** — after crash/cancel, choose **Resume** or **Delete partial files** to free disk space

### Extract
- **Disk usage** while unpacking (free / total), throttled polling

### Docs & ports
- `PORT_AGENTIC_INSTRUCTION.md` — detailed playbook for Android (GameHub) and SteamOS (`DUALSERVER-STEAMOS-PORT`)

### App branding
- Window title: **QuickPlay 2.7.8** (`APP_VERSION = 2.7.8`)

### Build & distribution
- Rebuilt `dist/QuickPlay.exe` via `build-protected.ps1`
- Source: `quickplay-2.7.8-beta` on private [dvahana2424-web/playzipdl](https://github.com/dvahana2424-web/playzipdl)
- Public installer: [quickplay-v2.7.8](https://github.com/hammerwebsite12/hammerfree/releases/tag/quickplay-v2.7.8)

```powershell
irm https://raw.githubusercontent.com/hammerwebsite12/hammerfree/quickplay/install.ps1 | iex
```

---

## v2.7.5 — September 16, 2026

### Fixes & features (this release)
- **Post-download extract** — finished archives are no longer deleted before 7-Zip runs (large downloads such as GTA SA)
- **Game runtime installer** — VC++ 2008–2022 + DirectX June 2010 from Settings; progress via SSE
- **Catalog after idle** — session renew, retry, and UI refresh when browse/search goes stale
- **Server responsiveness** — download start and space-check run off the main event loop (`asyncio.to_thread`)
- **Game detail** — download from catalog; extras load in the background

### Download (unchanged from 2.7.x line)
- In-place parallel download with `.part.progress` resume; **Refresh link** on the download dock
- Thread-safe progress writes on Windows (`WinError 32` fix)

### App branding
- Window title: **QuickPlay 2.7.5** (`APP_VERSION = 2.7.5`)

### Build & distribution
- Rebuilt `dist/QuickPlay.exe` via `build-protected.ps1` (PyArmor on licensing modules)
- Source backup: `quickplay-2.7.5-beta` on private [dvahana2424-web/playzipdl](https://github.com/dvahana2424-web/playzipdl)
- Public installer: `quickplay` on [hammerwebsite12/hammerfree](https://github.com/hammerwebsite12/hammerfree) — tag **quickplay-v2.7.5**

```powershell
irm https://raw.githubusercontent.com/hammerwebsite12/hammerfree/quickplay/install.ps1 | iex
```

---

## v1.1.0 — July 9, 2026

### Licensing & Custom OS (QuickPlay for Windows)
- **Device fingerprint** — SHA-256 of UUID + MachineGuid + disk serial + BIOS serial
- **Registration code** — 42-char Hammer-compatible obfuscated code from machine UUID
- **License gate** — Worker returns `license_required` when HWID not activated; blocks download until seller activates
- **Custom OS detection** — shared/abnormal HWID stems show fingerprint + **Copy Both** in license modal
- **Hardware snapshot** — one-time dxdiag-like snapshot on first licensed download (Worker writes to `.user` + Discord alert)

### Download UX fixes
- **Cancel download** — confirmation dialog; full task cleanup; removes `.part` and `.part.partN` fragments (fixed glob bug with dotted filenames)
- **Pause / Resume** — confirmation on pause; optimistic UI update; smaller chunks (32 KB) for snappier pause
- **Download dock** — no ghost progress bar after cancel

### Windows bootstrap dialogs (themed, English)
- **Mandatory Administrator** — every non-elevated start shows **Run as Admin** / **Exit** (Defender exclusions)
- **Single instance** — dark dialog: "QuickPlay is still running…"
- **Defender exclusion failed** — startup warning + step-by-step manual exclusion instructions; also shown in Settings

### Cloudflare Worker (`dl-resolver`)
- HWID license check against `quickplayusr` GitHub repo
- `/sign` accepts `device_fp`, optional `hw_snapshot`
- `hwid_snapshot.js` — one-time append to `.user` with `Hardware-Snapshot-Recorded:` marker
- `abnormal_hwids.js` — custom OS / shared HWID stem validation
- Requires `QUICKPLAY_HWID_PAT` with **read + write** on `hammerwebsite12/quickplayusr`

### New / updated source files
- `device_fingerprint.py`, `hwid_obfuscation.py`, `license_manager.py`, `hardware_snapshot.py`
- `native_dialog.py` — pre-webview themed dialogs matching app UI
- `worker/src/hwid_snapshot.js`, `worker/src/abnormal_hwids.js`

### Build
- Rebuilt `dist/QuickPlay.exe` (~92 MB)

---

## v1.0.0 — QuickPlay rebrand (prior release on `feature/quickplay-rebrand`)
- Rebrand from PlayZip Downloader → **QuickPlay**
- Bundled 7-Zip, i18n (en/zh/es/tl), single-instance guard
- Cloudflare Worker download-link signer (`worker/`)
- IDM-style multi-connection downloader with resume on restart
