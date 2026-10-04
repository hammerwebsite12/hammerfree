# QuickPlay — One-paste installer

Open **Windows PowerShell** (a UAC admin prompt will appear automatically) and paste:

```powershell
irm https://raw.githubusercontent.com/hammerwebsite12/hammerfree/refs/heads/quickplay/install.ps1 | iex
```

## What it does

1. Requests Administrator rights (UAC).
2. Downloads the QuickPlay payload (`QuickPlay.zip`, ~30 MB) — **QuickPlay 2.8.0** (Fling trainers, library artwork heal, download cleanup).
3. Installs to `C:\Program Files (x86)\QuickPlay`.
4. Creates a Desktop shortcut **QuickPlay**.
5. Registers an entry in **Control Panel > Programs and Features** (uninstall via `uninstall.ps1`).

Payload: `QuickPlay.zip` on the [quickplay branch](https://github.com/hammerwebsite12/hammerfree/tree/quickplay) (primary). [Release quickplay-v2.8.0](https://github.com/hammerwebsite12/hammerfree/releases/tag/quickplay-v2.8.0) is a fallback mirror.
