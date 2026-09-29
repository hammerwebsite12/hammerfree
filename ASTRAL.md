# AstralGames / Server 3 — discovery & plan

**Site:** https://astralgames.net/  
**Status (2026-09-29):** Client code in `astral/` + Worker `store=server3` path in `anker-dlresolver`. **Not shown in QuickPlay 2.7.9 Settings** (`SERVER3_UI_ENABLED = False` in `store_manager.py`) until mirrors/gates are stable (mocha, fileq, etc.).

## How it differs from Server 2 (Anker)

- **Not** a drop-in Anker fork: no `/csrf-token`, no `generateDownloadUrl()`, no `ankergames.net/download/...` gate.
- **Next.js** app (`self.__next_f` flight data). Game pages embed JSON in HTML, for example:
  - `"download_link":"https://pearcrypt.lol/container/..."`
  - `"downloadOptions":[{"name":"Base Game","url":"..."}]`
  - `"hasDirectDownload":true`
- Assets/images may use `api.bonker.dev` (separate CDN/API).
- `/api/games` returns **401** without credentials (not used for public browse today).
- `/games` and `/trending` paths return **404**; catalog is on `/` with sections (Latest, Trending, Queue).

## Worker / licensing

Server 3 uses the **same HWID flow as Server 2**:

- **Startup license:** `ANKER_LICENSE_WORKER_URL` → `anker-resolver` `POST /license/check`
- **Download:** `ANKER_DL_WORKER_URL` → `anker-dlresolver` `POST /resolve` with **`store: "server3"`** (same `APP_TOKEN`, `SIGNING_SECRET`, `QUICKPLAY_HWID_PAT` as S2)

Astral URL fetch runs inside `anker-dlresolver` (`astral_upstream.js`). Standalone **`worker-astral/`** is optional/legacy.

## Shipped in repo (UI off for 2.7.9)

1. **`astral/astral_api.py`** — browse/search + Worker resolve (`store: server3`).
2. **`store_manager.py`** — `server3` client; hidden from `public_store_options()` until enabled.
3. **Gates** — `mocha_verify.py`, `fileq_verify.py` for mirror HTML hops.
4. **Android** — not updated in this release (see `progressreport929.txt`).

## Before public Server 3 toggle

- Set `SERVER3_UI_ENABLED = True` in `store_manager.py`.
- Re-test pearcrypt / mocha / fileq mirrors end-to-end.
- Update changelog and i18n; keep “Server 3” user-facing label only.

## Risks

- **URL in HTML** may change when Astral updates Next.js payload shape → update `astral_upstream.js` regex.
- **pearcrypt.lol** may be HTML landing page, not direct archive — downloader must follow redirects / parse final CDN.
- **Worker fetch** may be blocked later (less likely than Anker CSRF path, but monitor).
- **Legal/branding** — user-facing label should stay “Server 3”, hide Astral/AstralGames in UI per AGENTS.md.

## Smoke test (manual)

```powershell
# Game page contains pearcrypt link
curl -s -A "Mozilla/5.0" https://astralgames.net/game/graveyard-keeper-2 | findstr pearcrypt
```

After Worker deploy, signed `POST /resolve` with `game_id=graveyard-keeper-2` should return `download_url` containing `pearcrypt.lol`.
