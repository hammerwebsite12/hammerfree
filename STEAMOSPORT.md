# STEAMOSPORT.md — QuickPlay 2.7.5 beta SteamOS Port Notes

> **Audience:** AI agent (or human) porting **QuickPlay 2.1–2.3** Windows changes into the
> existing **SteamOS** tree (`SteamOS port/QuickPlaySteamOS/` in `playzipdl`).
>
> **Read first:** [AGENTICSTEAMOSTODOPORT.md](AGENTICSTEAMOSTODOPORT.md) for the full
> 2.0 → SteamOS merge guide. **This file** covers the **2.1–2.3 delta** and bugs you must
> not regress.

---

## 1. Source of truth

| What | Location |
|------|----------|
| Windows 2.3 (latest) | `D:\My Drive\Workspace\plazipanker` — branch `DUAL-SERVER-stable` |
| SteamOS port target | `SteamOS port/QuickPlaySteamOS/` |
| Full merge playbook | [AGENTICSTEAMOSTODOPORT.md](AGENTICSTEAMOSTODOPORT.md) |
| Agent rules (downloads/resume) | [AGENTS.md](AGENTS.md) |

**Version after port:** set `APP_VERSION = "2.3"` in `settings_manager.py` and update the
static fallback in `web/index.html` + `web/app.js`.

---

## 2. Version changelog (2.0 → 2.3)

| Ver | Area | Change |
|-----|------|--------|
| **2.1** | Server 2 extract | Branded repack folders **renamed**, not deleted (Metaphor fix) |
| **2.1** | `archive_utils.py` | `_dir_has_game_content`, `_strip_store_branding_folder_name`, etc. |
| **2.2** | `exe_scanner.py` | Scan `.bat` + `.exe`; `launch_play_target()` for PLAY |
| **2.2** | `download_service.py` | `launch_game()` uses `launch_play_target()` |
| **2.2** | Windows only | `.bat` launched via `ShellExecuteW(..., "runas", ...)` (admin) |
| **2.3** | `cover_cache.py` | **New** — disk cache for library capsule art |
| **2.3** | `backend/server.py` | `GET /api/covers/{entry_id}` serves cached covers locally |
| **2.3** | `download_service.py` | Warm cache on startup; cache on library add / artwork backfill |
| **2.3** | `web/app.js` + `style.css` | Graceful cover fallback when image missing offline |

No new Workers required for 2.1–2.3. Server 2 CSRF worker fix (`anker-dlresolver`) is
deployed server-side only — no SteamOS client change.

---

## 3. Metaphor ReFantazio bug (2.1 — must not regress)

### 3.1 Symptom (Windows 2.0)

User downloads **Metaphor ReFantazio** on **Server 2** (Anker). After extraction:

- Install folder appears **empty** or game files are **gone**
- EXE picker shows wrong paths or nothing useful
- Library entry may point to an install dir with no playable game

### 3.2 Root cause

Server 2 enables post-extract junk cleanup:

```python
# download_service.py — after download completes
extract_archive(..., remove_store_junk=self.store.is_anker)
```

In **2.0**, `archive_utils._remove_extract_junk()` treated any **root-level folder**
whose name contained `ankergames` as promo junk and called **`remove_tree()`** on the
**entire folder**.

Many Anker repacks ship the **full game** inside a top-level folder like:

```
{download_dir}/Metaphor ReFantazio/
  └── Metaphor-ReFantazio-Atlus-35th-Digital-Anniversary-Edition-AnkerGames/
        ├── base.cpk                    # main game payload (~80+ GB)
        ├── Atlus 35th ... Soundtrack/
        └── Atlus 35th ... History Book/
```

The substring `ankergames` in `-AnkerGames` matched → **whole game tree deleted**.

### 3.3 Fix (2.1)

**File:** `archive_utils.py`

| Before (2.0) | After (2.1+) |
|--------------|--------------|
| Root junk-marked folder → always `remove_tree()` | If folder has **game content** → **rename** (strip branding) |
| | If folder is promo-only (HTML/readme, no game) → still delete |

**Rename example:**

```
Metaphor-ReFantazio-Atlus-35th-Digital-Anniversary-Edition-AnkerGames
  → Metaphor-ReFantazio-Atlus-35th-Digital-Anniversary-Edition
```

**EXE scan order** (`download_service.py`): cleanup runs **inside** `extract_archive()`
before `scan_executables()` — picker always sees final paths after rename.

---

## 4. BAT PLAY launcher (2.2)

### 4.1 What changed

Some games ship a user-created or repack `.bat` as the real launcher (e.g. Chill with You).

| File | Change |
|------|--------|
| `exe_scanner.py` | `PLAYABLE_EXTENSIONS = (".exe", ".bat")`; `launch_play_target()` |
| `download_service.py` | `launch_game()` calls `launch_play_target(entry.exe_path)` |

Library UI label stays **"EXE"** (not renamed) — the picker lists both `.exe` and `.bat`.

### 4.2 Windows behavior

```python
# exe_scanner.py — Windows .bat only
ctypes.windll.shell32.ShellExecuteW(None, "runas", target, None, workdir, 1)
```

`.bat` files are elevated because many repack launchers expect admin (Defender exclusions,
registry tweaks, etc.).

### 4.3 SteamOS port notes

**Do not copy Windows `runas` elevation to Linux.**

On SteamOS / Linux, implement `launch_play_target()` like:

```python
if is_batch_launcher(target):
    # Option A: run via bash (if .bat is simple)
    subprocess.Popen(["bash", target], cwd=workdir)
    # Option B: skip .bat on Linux; only list .exe / Proton targets
    # Option C: convert known .bat patterns to equivalent shell script
```

Recommended for Deck:

1. **Scan** `.bat` so users can see them in the picker (parity with Windows library).
2. **Launch** `.bat` via `bash` or `wine cmd /c` only if you validate the script is safe;
   otherwise show a message: "Batch launchers are Windows-only — pick the game .exe for Proton."
3. Keep `subprocess.Popen([target], ...)` for native Linux binaries and Proton `.exe` paths
   (existing SteamOS `launch_game` flow).

---

## 5. Offline library cover cache (2.3)

### 5.1 Problem

My Library showed **broken capsule images** when offline. The web UI loaded `image_url`
directly from remote CDNs (`playzip.com`, `ankergames.to` / legacy `ankergames.net`, etc.) — no local fallback.

### 5.2 Solution (Windows 2.3)

| Component | Role |
|-----------|------|
| `cover_cache.py` | Download + store covers under `{app_root}/cache/covers/{entry_id}.img` |
| `GET /api/covers/{entry_id}` | Serve cached bytes from local disk (no internet) |
| `download_service._library_entry_dict()` | Returns `/api/covers/{id}` when cache hit, else remote URL |
| `_warm_library_cover_cache()` | Background warm-up on startup + after artwork backfill |
| `_schedule_cover_cache()` | Cache when a game is added to library |
| `web/app.js` | On image error → dark gradient placeholder (not broken icon) |

**Flow:**

```
Online:  remote URL → download to cache/covers/ → UI uses /api/covers/{entry_id}
Offline: UI uses /api/covers/{entry_id} → served from disk (if previously cached)
```

**Important:** User needs **one online session** after install/update so covers can warm up.
After that, My Library looks correct offline.

### 5.3 SteamOS port notes

| Item | Action |
|------|--------|
| `cover_cache.py` | **COPY** — uses `requests` + `app_paths.cache_dir()` (works on Linux) |
| `app_paths.cache_dir()` | Already `{app_root}/cache/covers` — same layout on AppImage |
| `backend/server.py` | **MERGE** `GET /api/covers/{entry_id}` endpoint |
| `download_service.py` | **MERGE** `_library_entry_dict`, `_schedule_cover_cache`, `_warm_library_cover_cache` |
| `release.spec` / AppImage spec | Add `cover_cache` to hidden imports if needed |
| `web/app.js` + `style.css` | **MERGE** cover error fallback styles |

Cache path on Deck (AppImage):

```
~/.local/share/QuickPlay/cache/covers/{entry_id}.img   # or app root next to AppImage — match app_paths
```

Verify `app_root_dir()` on SteamOS points where `library.json` lives so cache survives updates.

### 5.4 Do not port from legacy Tk

`legacy_main_tk.py` had a separate PIL-based cover cache — **ignore it**. The web UI cache
in `cover_cache.py` + `/api/covers` is the source of truth for 2.3+.

---

## 6. Files to merge into SteamOS (full 2.1–2.3)

Copy or three-way-merge from Windows `plazipanker` → `QuickPlaySteamOS/`:

| File | Ver | Action |
|------|-----|--------|
| `archive_utils.py` | 2.1 | **MERGE** junk rename logic + helpers |
| `download_service.py` | 2.1–2.3 | **MERGE** extract order, `launch_play_target`, cover cache hooks |
| `exe_scanner.py` | 2.2 | **MERGE** `.bat` scan + `launch_play_target` (adapt Linux branch) |
| `cover_cache.py` | 2.3 | **ADD** (new file) |
| `backend/server.py` | 2.3 | **MERGE** `/api/covers/{entry_id}` route |
| `settings_manager.py` | 2.3 | **MERGE** `APP_VERSION = "2.3"` |
| `web/index.html` | 2.3 | **MERGE** version label |
| `web/app.js` | 2.3 | **MERGE** version fallback + cover error fallback |
| `web/style.css` | 2.3 | **MERGE** `.cover-missing` / `.cover-fallback` styles |
| `release.spec` / AppImage build | 2.3 | Add `cover_cache` if frozen build omits it |

If SteamOS `archive_utils.py` still has the **2.0** `_remove_extract_junk` that only
calls `_safe_rmtree` on junk dirnames, **the Metaphor bug will reproduce on Deck**.

### 6.1 `archive_utils.py` helpers (2.1)

| Symbol | Role |
|--------|------|
| `_STORE_FOLDER_BRANDING_RE` | Strip `ankergames`, pre-installed markers |
| `_strip_store_branding_folder_name()` | Clean folder name |
| `_unique_sibling_dir()` | Avoid rename collisions |
| `_GAME_FILE_EXTENSIONS` | Detect game payloads (add `.cpk` for Metaphor — see §7.3) |
| `_dir_has_game_content()` | True if game-like files in tree |
| `_safe_rename()` | Rename folder (`long_path` on Windows; `os.rename` on Linux) |

### 6.2 `download_service.py` — extract call chain

```python
extract_archive(
    archive_path,
    install_dir,
    on_progress=on_extract_progress,
    remove_store_junk=self.store.is_anker,   # Server 2 only
)
exes = scan_executables(install_dir)   # after cleanup/rename inside extract_archive
self._post_extract(task_id, game, install_dir, exes)
```

### 6.3 `download_service.py` — launch (2.2)

```python
from exe_scanner import launch_play_target
launch_play_target(entry.exe_path)
```

---

## 7. Linux / SteamOS-specific notes

### 7.1 Paths

- `install_dir = join(download_dir, LibraryManager.safe_folder_name(title))`
- Same two-level layout as Windows: outer title folder → inner repack folder (often branded)
- `long_path()` in `_safe_rename` is Windows-only; Linux uses normal paths

### 7.2 7-Zip

Extraction uses bundled `7zz` via `app_paths.bundled_7z_path()` — unchanged. Junk cleanup
runs **after** 7z finishes, in `_finish()` → `_remove_extract_junk()`.

### 7.3 Metaphor + `.cpk` payloads

Add `".cpk"` to `_GAME_FILE_EXTENSIONS` so cleanup never deletes a folder that only has
CRIware `.cpk` data:

```python
_GAME_FILE_EXTENSIONS = (
    ".exe", ".pak", ".bin", ".dat", ".dll", ".vpk", ".bsa", ".cpk",
)
```

### 7.4 PLAY targets on Linux

| Type | SteamOS behavior |
|------|------------------|
| `.exe` | Proton / Steam shortcut (existing flow) |
| `.bat` | **Do not** use Windows `runas` — use bash/wine or block with clear message |
| Native Linux binary | `subprocess.Popen` with `cwd=install_dir` |

### 7.5 Cover cache offline

Works the same as Windows once `app_paths.cache_dir()` resolves correctly. Test:

1. Online: open My Library → wait for covers to load
2. Check `cache/covers/*.img` exists under app root
3. Disconnect network → reload library → capsules still show via `/api/covers/...`

---

## 8. What NOT to change

- Do **not** remove `remove_store_junk` for Server 2 — promo HTML/BAT cleanup is still wanted
- Do **not** delete nested folders by marker name (only **install root**)
- Do **not** move EXE scan before cleanup
- Do **not** port Windows-only modules over Linux Steam helpers (`steam_shortcuts.py`, etc.)
- Do **not** port Windows `ShellExecuteW runas` for `.bat` to Linux
- Do **not** use `legacy_main_tk.py` cover cache — use `cover_cache.py` only

---

## 9. Test plan (SteamOS agent checklist)

### 9.1 Unit — extract rename (2.1)

```python
from archive_utils import (
    _is_junk_dirname,
    _strip_store_branding_folder_name,
    _remove_extract_junk,
)
import tempfile, os

name = "Metaphor-ReFantazio-Atlus-35th-Digital-Anniversary-Edition-AnkerGames"
assert _is_junk_dirname(name, at_install_root=True)
assert _strip_store_branding_folder_name(name) == \
    "Metaphor-ReFantazio-Atlus-35th-Digital-Anniversary-Edition"

tmpdir = tempfile.mkdtemp()
install = os.path.join(tmpdir, "Metaphor ReFantazio")
game = os.path.join(install, name)
os.makedirs(game)
open(os.path.join(game, "base.cpk"), "wb").write(b"x" * 1024)
_remove_extract_junk(install)
children = os.listdir(install)
assert "AnkerGames" not in children[0]
assert os.path.isfile(os.path.join(install, children[0], "base.cpk"))
```

### 9.2 Integration — Server 2 download (2.1)

1. Set store to **Server 2**
2. Download game with `-AnkerGames` root folder
3. After extract:
   - [ ] Inner folder **renamed** (no `-AnkerGames` in path)
   - [ ] Game files / `base.cpk` **still present**
   - [ ] EXE picker lists paths under **renamed** folder
4. Promo-only junk folder → still **deleted**

### 9.3 BAT launcher (2.2)

- [ ] Picker lists `.bat` files alongside `.exe`
- [ ] PLAY on `.exe` still works via Proton
- [ ] `.bat` behavior documented (bash/wine or user message — not silent failure)

### 9.4 Cover cache (2.3)

- [ ] Online: library covers load and `cache/covers/*.img` files created
- [ ] API `GET /api/covers/{entry_id}` returns image bytes
- [ ] Offline: My Library shows cached capsules (not broken icons)
- [ ] Missing cache shows gradient placeholder, not browser broken-image icon

### 9.5 Regression

- [ ] Server 1 download + extract unchanged
- [ ] Resume interrupted download still works (see AGENTS.md)
- [ ] `APP_VERSION` shows **2.3** in Settings

---

## 10. Quick diff reference (minimum patch)

If SteamOS already has 2.0 dual-store merge from AGENTICSTEAMOSTODOPORT.md:

1. **2.1** — `archive_utils.py` junk rename + version bump
2. **2.2** — `exe_scanner.py` + `launch_play_target` in `download_service.py` (Linux branch)
3. **2.3** — `cover_cache.py`, server route, download_service cache hooks, web fallback CSS/JS
4. Bump all version strings to `2.3`
5. Rebuild AppImage: `SteamOS port/build-appimage.sh`

---

## 11. Related docs

| Doc | Contents |
|-----|----------|
| [AGENTICSTEAMOSTODOPORT.md](AGENTICSTEAMOSTODOPORT.md) | Full 2.0 SteamOS merge |
| [AGENTS.md](AGENTS.md) | Download/resume/library debugging |
| [ANKER.md](ANKER.md) | Server 2 architecture + CSRF worker fix |
| [DUAL_SERVER_BETA.md](DUAL_SERVER_BETA.md) | Server 2 extract flags overview |

---

## 12. Agent prompt snippet (copy-paste)

```
Port QuickPlay 2.3 from plazipanker (Windows, DUAL-SERVER-stable) into
SteamOS port/QuickPlaySteamOS/.

Read STEAMOSPORT.md and AGENTICSTEAMOSTODOPORT.md first.

Must merge:
- 2.1 archive_utils.py — Anker branded root folders RENAMED not DELETED when they
  contain game files (Metaphor fix). EXE scan after extract_archive() returns.
- 2.2 exe_scanner.py — scan .bat + .exe; launch_play_target() in launch_game().
  On Linux: do NOT use Windows ShellExecuteW runas for .bat.
- 2.3 cover_cache.py + GET /api/covers/{entry_id} + download_service cache warm-up
  so My Library artwork works offline after one online session.
- web/app.js + style.css cover fallback when image missing.

Bump APP_VERSION to 2.3. Optionally add .cpk to _GAME_FILE_EXTENSIONS.

Run test plan in STEAMOSPORT.md §9. Do not overwrite steam_shortcuts.py or Linux-only paths.
```
