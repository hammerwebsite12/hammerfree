# QuickPlay 2.8.1-beta — One-paste installer (test channel)

**Stable users:** keep using the `quickplay` branch installer (unchanged).

Open **Windows PowerShell** (UAC admin prompt appears automatically) and paste:

```powershell
irm https://raw.githubusercontent.com/hammerwebsite12/hammerfree/refs/heads/quickplay-beta/install.ps1 | iex
```

## What it does

1. Requests Administrator rights (UAC).
2. Downloads **QuickPlay 2.8.1-beta** (`QuickPlay.zip`, ~26 MB).
3. Installs to `C:\Program Files (x86)\QuickPlay Beta` (does **not** overwrite stable QuickPlay in `Program Files (x86)\QuickPlay`).
4. Creates a Desktop shortcut **QuickPlay Beta**.
5. Registers **QuickPlay Beta** in Programs and Features (uninstall via `uninstall.ps1` in the install folder).

Payload: [quickplay-beta branch](https://github.com/hammerwebsite12/hammerfree/tree/quickplay-beta) (primary). [Release quickplay-v2.8.1-beta](https://github.com/hammerwebsite12/hammerfree/releases/tag/quickplay-v2.8.1-beta) is a fallback mirror.

## Maintainer: publish a new beta build

From repo root (after `dist\QuickPlay.exe` is built):

```powershell
.\installer-publish-beta\pack-beta.ps1
.\installer-publish-beta\deploy-beta.ps1
```
