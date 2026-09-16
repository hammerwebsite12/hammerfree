# todosteamOSport2.md — Agent TODO: SteamOS 2.7.1 (remaining work)

> **Scope (ito lang ang kailangan i-port):**
> - **Phase 4** — Download queue / cancel / pause-resume / auto-resume fixes (2.7.1)
> - **Phase 5** — Controller navigation lang (**SKIP Big Picture mode**)
>
> **Tapos na (huwag na ulitin):** Phase 0–3 — dual-store, Metaphor fix, cover cache, gate verification,
> store persistence, worker TLS. Assume merged na sa SteamOS tree.

| | |
|---|---|
| **Windows source** | `D:\My Drive\Workspace\plazipanker` |
| **SteamOS target** | `SteamOS port/QuickPlaySteamOS/` |
| **Target version** | `APP_VERSION = "2.7.5-beta"` (Windows baseline; bump SteamOS fork as needed) |

**Reference docs:** [AGENTS.md](AGENTS.md) (download/resume rules) · [AGENTICSTEAMOSTODOPORT.md](AGENTICSTEAMOSTODOPORT.md) · [STEAMOSPORT.md](STEAMOSPORT.md)

---

## Agent rules

1. **Three-way merge** — huwag i-overwrite ang Linux-only files (`steam_shortcuts.py`, `steam_paths.py`, `steam_artwork.py`, `app_paths.py`).
2. **Functional parity** sa download queue + controller nav; **huwag** i-port ang Big Picture stack.
3. Tapusin ang **Test checklist (§Test)** bago i-mark done.

---

## Phase 4 — Download queue & cancel fixes (2.7.1) ✅ ACTIVE

> Commit ref: `c3dc326` — `download_service` / queue portions

### Files to merge (Windows → SteamOS)

| File | Action |
|------|--------|
| `download_queue.py` | **MERGE** — `remove_by_game_id()` |
| `download_wait_queue.py` | **MERGE** — wait-queue persistence helpers |
| `download_service.py` | **MERGE** — `_purge_download_persistence`, `_cancelled_pending`, cancel during gate/resolving, queue position refresh, auto-resume loop guards |
| `idm_downloader.py` | **MERGE** — pause/resume status sync; stale progress tick guard |
| `web/app.js` | **MERGE** — download dock/queue UI; pause/resume/cancel button handling |

### Checklist

- [ ] Cancel queued download → removed from `.quickplay_downloads.json` **and** wait queue
- [ ] Cancel during "Resolving download link…" / gate → `abort_pending_gate()`, cleanup `.part` files
- [ ] Pause → UI shows **Paused**; resume → continues from fragments
- [ ] Interrupt download → restart app → auto-resume (no infinite resolve loop)
- [ ] Walang phantom "Resolving download link…" pag SSE reconnect / relaunch
- [ ] `has_partial` guard intact — stale final file must not cancel resume ([AGENTS.md](AGENTS.md))
- [ ] Queue 2+ downloads → isa lang active; iba may position number sa UI

### Key symbols to verify after merge

`download_service.py`:

- `_purge_download_persistence()`
- `_cancelled_pending`
- `_refresh_queue_positions()`
- `cancel_task()` — queued / resolving / gate / download branches
- `resume_pending_downloads()` — `has_partial` check bago i-skip ang resume

`download_queue.py`:

- `remove_by_game_id()`

`idm_downloader.py`:

- Pause/resume updates `TaskView.state` via `on_status`
- Stale progress ticks do not overwrite Paused/Cancelled

---

## Phase 5 — Controller navigation only (2.7.1) ✅ ACTIVE

> **SKIP:** Big Picture mode — huwag i-port ang `bigpicture.js`, `bigpicture.css`, `banner_cache.py`,
> `/api/banners`, Library Big Picture button, `allow_big_picture` setting, o auto-launch on startup.

> Commit ref: `c3dc326` — gamepad portions lang

### Files to add

| File | Action |
|------|--------|
| `web/gamepad.js` | **ADD** |
| `gamepad_input.py` | **COPY** — no-ops on Linux (`sys.platform != "win32"`) |

### Files to merge

| File | What to port |
|------|----------------|
| `settings_manager.py` | `controller_enabled` lang — **huwag** idagdag ang `allow_big_picture` |
| `backend/server.py` | `GET /api/gamepad/state`; `start_gamepad_bridge()` on init — **huwag** ang banner route |
| `web/index.html` | Checkbox: **Enable controller navigation**; `<script src="gamepad.js">` — **huwag** Big Picture button/scripts |
| `web/app.js` | `GamepadNav.init`, `setEnabled`, SSE/gamepad activity hook — **huwag** `BigPicture`, `openBigPictureMode`, `maybeStartBigPictureOnLaunch` |
| `web/i18n.js` | Controller strings lang (en/zh/es/tl) — **huwag** Big Picture keys |
| `web/style.css` | `.controller-mode` focus styles kung may diff sa Windows — **huwag** Big Picture button styles |
| `build-appimage.sh` | Include `web/gamepad.js`, `gamepad_input.py` — **huwag** bigpicture/banner files |

### Checklist

- [ ] Settings → **Enable controller navigation** → save → persists sa `settings.json`
- [ ] Steam Deck controller: D-pad/stick navigates tabs, browse grid, library cards, download buttons
- [ ] A = activate/select; B = back/close modal
- [ ] LB/RB = switch tabs
- [ ] **SteamOS path:** browser `Gamepad API` via `gamepad.js` `pollGamepads()` — primary
- [ ] `gamepad_input.py` XInput bridge optional/no-op on Linux (HTTP poll sa `/api/gamepad/state` walang data = OK)
- [ ] **Hindi** dapat lumabas ang Big Picture UI o auto-open on launch

### Windows-only portions — do not break on Deck

Sa `web/gamepad.js`, i-port ang buong file pero **alisin o huwag i-wire** ang:

- `window.BigPicture?.isOpen?.()` checks (safe kung wala ang BigPicture object)
- `hooks` na tumatawag sa Big Picture open/close

Kung mas simple: i-merge ang `gamepad.js` mula Windows at siguraduhing **walang** `<script src="bigpicture.js">` sa SteamOS `index.html`.

---

## SKIP list (Phase 4–5 scope)

| Item | Reason |
|------|--------|
| `web/bigpicture.js`, `web/bigpicture.css` | User requested skip Big Picture |
| `banner_cache.py`, `GET /api/banners/{entry_id}` | Big Picture only |
| `allow_big_picture` setting + UI | Big Picture only |
| `settings_manager.allow_big_picture` | Do not add to SteamOS |
| Big Picture i18n keys, Library BP button, `maybeStartBigPictureOnLaunch` | Big Picture only |
| `win_elevate`, PyArmor, `dist/QuickPlay.exe` | Windows-only (unchanged) |

---

## Test checklist (Steam Deck) — remaining work only

### Download queue (Phase 4)

- [ ] Queue 2+ downloads → only one active; others show position
- [ ] Cancel queued item → gone from UI + `.quickplay_downloads.json` + wait queue
- [ ] Cancel during gate resolve → task removed; no phantom task on relaunch
- [ ] Pause/resume mid-download → UI state correct
- [ ] Force-close during download → relaunch auto-resumes from fragments

### Controller navigation (Phase 5)

- [ ] Enable controller navigation in Settings → navigates browse/library/downloads/settings
- [ ] Game detail modal + EXE picker navigable via controller
- [ ] Disable setting → normal mouse/keyboard only
- [ ] **No** Big Picture button, overlay, or auto-launch

### Regression smoke (quick)

- [ ] Server 2 download + gate still works (ported na — huwag masira ng merge)
- [ ] Store setting still persists restart

---

## Commit series (suggested)

```
fix(steamos): download queue cancel/resume persistence (2.7.1)
feat(steamos): controller navigation via gamepad.js (no Big Picture)
build(steamos): bundle gamepad.js in AppImage
```

---

## Agent prompt (copy-paste)

```
Port remaining QuickPlay 2.7.1 items to SteamOS (playzipdl/SteamOS port/QuickPlaySteamOS/).

Phases 0–3 are DONE — do not redo gate, store, cover cache, worker_tls, etc.

ACTIVE WORK ONLY:

Phase 4 — Download queue fixes:
- Merge download_queue.py (remove_by_game_id)
- Merge download_wait_queue.py
- Merge download_service.py (cancel/purge/auto-resume/queue positions)
- Merge idm_downloader.py (pause/resume UI sync)
- Merge web/app.js download dock pause/resume/cancel handling

Phase 5 — Controller navigation ONLY:
- ADD web/gamepad.js, COPY gamepad_input.py (Linux no-op OK)
- MERGE settings_manager.py (controller_enabled only — NO allow_big_picture)
- MERGE backend/server.py (/api/gamepad/state, start_gamepad_bridge)
- MERGE web/index.html, app.js, i18n.js, style.css for controller setting + GamepadNav
- UPDATE build-appimage.sh for gamepad.js

SKIP Big Picture entirely:
- No bigpicture.js/css, banner_cache.py, /api/banners, allow_big_picture, Library BP button

Three-way merge — preserve steam_shortcuts.py, steam_paths.py, app_paths.py.
Deck uses browser Gamepad API (gamepad.js pollGamepads).

Run Test checklist in todosteamOSport2.md (Phase 4 + 5 sections only).
```

---

*Last updated: 2026-08-21 — scoped to Phase 4 + controller nav only; Big Picture explicitly skipped.*
