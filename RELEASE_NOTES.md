# QuickPlay Release Notes

## v2.7.5 beta — September 16, 2026

### Download performance
- **In-place parallel download** — eight connections write directly into one `.part` file at the correct byte offsets; **no separate “Merging” phase** (less disk I/O, faster completion)
- **Resume** — `.part.progress` JSON tracks per-connection bytes; legacy `.part.partN` chunks are migrated once on resume

### Download UX
- **Refresh link** — button on the download dock and Downloads tab; fetches a new signed URL without deleting partial files
- **Live resolve status** — UI shows steps such as contacting signer, preparing download page, trying mirror, rate-limit countdown
- **Progress save fix (Windows)** — thread-safe `.progress` writes (fixes `WinError 32` during parallel download)

### App branding
- Window title and top bar: **QuickPlay 2.7.5 beta**
- Settings shows version `2.7.5-beta`

### Build
- Rebuilt `dist/QuickPlay.exe` via `build.ps1`
- Source branch: `quickplay-2.7.5-beta` on `hammerwebsite12/hammerfree`

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
