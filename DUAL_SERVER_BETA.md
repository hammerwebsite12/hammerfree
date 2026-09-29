# QuickPlay — Dual Server (historical beta notes)

**Current release:** 2.7.9 on branch `quickplay-2.7.9-beta` (Server 1 + Server 2 public; Server 3 in repo, UI hidden).

**Original beta branch:** `dual-server-beta`  
**Date:** August 3, 2026  
**Workspace:** `plazipanker` (Windows unified build)

## Status

Working beta — Server 1 (PlayZip) + Server 2 (Anker) in one app, **Version 2.0**.

| Feature | Status |
|---------|--------|
| Settings → Server 1 / Server 2 (no PlayZip/Anker branding) | Done |
| `store_manager.py` — browse, search, download, license URL switch | Done |
| Anker browse + search + game details (JSON-LD, Steam trailer) | Done |
| Anker **Latest** sort (`/recent-updates`) | Done |
| Auto catalog reload on server switch | Done |
| Auto-failover Server 1 → Server 2 (unreachable / empty search) | Done |
| Anker downloads via `anker-dlresolver` Worker | Done |
| dlproxy HEAD 403 fix (`idm_downloader` ranged GET probe) | Done |
| Server 2 extract: junk cleanup, `-AnkerGames` rename, log sanitize | Done |
| `worker-anker/` source + deploy docs | Done |
| SteamOS port guide (`AGENTICSTEAMOSTODOPORT.md`) | Done (merge pending) |

## Build

```powershell
cd plazipanker
pip install -r requirements.txt
copy client_secrets.example.py client_secrets.py   # fill Worker URLs + tokens
python -m PyInstaller --noconfirm --clean QuickPlay.spec
```

## Docs

- [ANKER.md](ANKER.md) — Server 2 architecture
- [AGENTICSTEAMOSTODOPORT.md](AGENTICSTEAMOSTODOPORT.md) — SteamOS merge guide
- [anker/FORMULA.md](anker/FORMULA.md) — Anker upstream API
- [worker-anker/README.md](worker-anker/README.md) — Anker Workers

## Not in this branch

- SteamOS AppImage merge (see port guide)
- PyArmor protected build (optional; use `build-protected.ps1` on Windows)
