# Hammer 3.8 — Windows installer (`installer` branch)

`main` is unchanged — game manifests and DLLs stay there. This branch adds the one-paste Windows installer only.

Open **Windows PowerShell** and paste:

```powershell
irm https://cdn.jsdelivr.net/gh/hammerwebsite12/hammerfree@installer/install.ps1 | iex
```

Direct GitHub raw:

```powershell
irm https://raw.githubusercontent.com/hammerwebsite12/hammerfree/installer/install.ps1 | iex
```

Payload: [GitHub Release v3.8](https://github.com/hammerwebsite12/hammerfree/releases/tag/v3.8) (primary).

## What it does

1. Requests Administrator rights (UAC).
2. Downloads `Hammer-3.8.zip.001` + `Hammer-3.8.zip.002` and reassembles them.
3. Installs to `C:\Program Files (x86)\Hammer`.
4. Creates a Desktop shortcut **Hammer 3.8**.
5. Registers Control Panel uninstall via `Uninstall.exe`.
