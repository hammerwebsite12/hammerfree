# QuickPlay 2.9.2 — One-paste installer (test channel)

**Not for stable users.** Stable stays on `quickplay` branch (currently 2.8.0).

## Install (beta testers)

```powershell
irm https://raw.githubusercontent.com/hammerwebsite12/hammerfree/refs/heads/quickplay-beta/install.ps1 | iex
```

## What it does

1. Elevates to Administrator (required for QuickPlay).
2. Downloads **QuickPlay 2.9.2** (`QuickPlay.zip`, ~26 MB).
3. Extracts to `C:\Program Files (x86)\QuickPlay`.
4. Creates Desktop shortcut.
5. Registers **QuickPlay** in Programs and Features (`DisplayVersion` shows **2.9.2**).

Payload: [quickplay-beta branch](https://github.com/hammerwebsite12/hammerfree/tree/quickplay-beta) (primary). [Release quickplay-v2.9.2-beta](https://github.com/hammerwebsite12/hammerfree/releases/tag/quickplay-v2.9.2-beta) is a fallback mirror.

## Maintainer

From repo root (after `build-protected.ps1` → `dist\QuickPlay.exe`):

```powershell
.\installer-publish-beta\pack-beta.ps1
.\installer-publish-beta\deploy-beta.ps1
```

Pushes to `hammerwebsite12/hammerfree` branch **`quickplay-beta`** only (does not touch stable `quickplay`).
