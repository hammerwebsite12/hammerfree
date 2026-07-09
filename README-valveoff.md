# ValveOFF — Windows installer (`valveoff` branch)

`main` is unchanged — game manifests and DLLs stay there. This branch adds the one-paste ValveOFF Windows installer.

Open **Windows PowerShell** and paste:

```powershell
irm https://cdn.jsdelivr.net/gh/hammerwebsite12/hammerfree@valveoff/install.ps1 | iex
```

Direct GitHub raw:

```powershell
irm https://raw.githubusercontent.com/hammerwebsite12/hammerfree/valveoff/install.ps1 | iex
```

Payload: [GitHub Release valveoff-v1.0](https://github.com/hammerwebsite12/hammerfree/releases/tag/valveoff-v1.0)

## What it does

1. Requests Administrator rights (UAC).
2. Downloads `ValveOFF.zip.001` + `ValveOFF.zip.002` and reassembles them (~189 MB).
3. Installs to `C:\Program Files (x86)\ValveOFF`.
4. Creates a Desktop shortcut **ValveOFF**.
5. Registers Control Panel uninstall via `Uninstall.exe`.
