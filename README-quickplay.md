# QuickPlay — One-paste installer

Open **Windows PowerShell** (a UAC admin prompt will appear automatically) and paste:

```powershell
irm https://cdn.jsdelivr.net/gh/hammerwebsite12/hammerfree@quickplay/install.ps1 | iex
```

Direct GitHub raw (if CDN is unavailable):

```powershell
irm https://raw.githubusercontent.com/hammerwebsite12/hammerfree/quickplay/install.ps1 | iex
```

## What it does

1. Requests Administrator rights (UAC).
2. Downloads the QuickPlay payload (`QuickPlay.zip`, ~91 MB).
3. Installs to `C:\Program Files (x86)\QuickPlay`.
4. Creates a Desktop shortcut **QuickPlay**.
5. Registers an entry in **Control Panel > Programs and Features** (uninstall via `uninstall.ps1`).

Payload: [GitHub Release quickplay-v1.0](https://github.com/hammerwebsite12/hammerfree/releases/tag/quickplay-v1.0)
