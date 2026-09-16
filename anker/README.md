# AnkerGames module (Server 2)

Python client for [ankergames.net](https://ankergames.net/) — used by **QuickPlay 2.7.5 beta**
when Settings → **Server 2** is selected. Wired through `store_manager.py`; not
a manual import swap anymore.

**Related docs:** [FORMULA.md](FORMULA.md) (upstream API), [../ANKER.md](../ANKER.md) (architecture),
[../worker-anker/README.md](../worker-anker/README.md) (Cloudflare Workers).

## Setup

```powershell
cd "D:\My Drive\Workspace\plazipanker"
copy anker\config.example.py anker\config.py   # optional — defaults work for prod
copy client_secrets.example.py client_secrets.py
```

Edit `client_secrets.py`: set `ANKER_LICENSE_WORKER_URL`, `ANKER_DL_WORKER_URL`,
`APP_TOKEN`, `SIGNING_SECRET` (see `worker-anker/README.md`).

## Quick test (CLI)

From repo root:

```powershell
python -m anker.test_client browse
python -m anker.test_client search "cuphead"
python -m anker.test_client download kingdom-rush-frontiers-tower-defense
```

## Unified app (normal path)

```powershell
python main.py
```

Settings → **Server 2** → browse, open game details, download. License uses
`anker-resolver`; download URL uses `anker-dlresolver` when configured in
`client_secrets.py`.

## Files

| File | Purpose |
|------|---------|
| `anker_api.py` | `AnkerGamesClient` — browse, search, `get_download_url()` |
| `anker_game_details.py` | Detail panel: JSON-LD + Steam trailer/screenshots |
| `FORMULA.md` | Upstream HTTP recipe (browse + download + gate/dlproxy) |
| `config.example.py` | `BASE_URL`, recaptcha bypass, download cooldown |
| `patch_store.py` | Monkey-patch for standalone Anker test build |
| `main_anker.py` | Standalone Anker-only pywebview launcher (legacy test) |
| `test_client.py` | CLI smoke tests |

## game_id format

Anker uses URL **slugs** (`cuphead`, `project-zomboid`), not PlayZip numeric IDs.
Library entries and download tasks store the slug in `game_id`.

## Browse sort mapping

| UI sort | Anker URL |
|---------|-----------|
| Views (default) | `GET /games?page={n}` |
| Latest | `GET /recent-updates` (different HTML — parsed via `aria-label`) |
| Trending | `GET /trending` |
| Category | `GET /genre/{slug}?page={n}` |

Do **not** use `?sort=latest` on `/games` — upstream returns HTTP 500.

## Download resolver priority

1. **Worker** (`ANKER_DL_WORKER_URL`) — license-gated `POST /resolve`
2. **Local fallback** — CSRF + `generate-download-url` in `anker_api.py` (dev only)

After resolve, client may still open Anker gate pages locally; **dlproxy** URLs are
treated as the final file URL (see FORMULA.md § Step 4).
