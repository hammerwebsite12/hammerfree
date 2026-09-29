# PORT_AGENTIC_INSTRUCTION.md — QuickPlay 2.7.9 Download UX → Android & SteamOS

> **Audience:** an AI agent (or human) porting the **QuickPlay Windows 2.7.9** download/extract
> UX changes into the **Android (GameHub)** and **SteamOS (QuickPlaySteamOS)** builds.
>
> **Read this whole file before editing.** Every section states the *intent* first, then the
> platform-specific implementation, because the platforms differ in ways that make a blind
> copy-paste wrong (see [§2 Platform reality check](#2-platform-reality-check)).

| Field | Value |
|-------|-------|
| Source branch | `quickplay-2.7.5-beta` @ `dvahana2424-web/playzipdl` |
| Source version | **2.7.9** (`APP_VERSION` / `APP_TITLE` in `settings_manager.py`) |
| Android target | `C:\Users\user\Desktop\gamehub` — module `quickplay`, appId `com.quickplay.android`, `versionName 1.0.7` |
| SteamOS target | branch `DUALSERVER-STEAMOS-PORT`, folder `QuickPlaySteamOS/`, `APP_VERSION = "2.6.8"` |
| Files changed on Windows | `idm_downloader.py`, `download_service.py`, `backend/server.py`, `settings_manager.py`, `web/{app.js,i18n.js,index.html,style.css}` |

---

## 1. What shipped in 2.7.8 (the five features to port)

| # | Feature | Why it exists |
|---|---------|---------------|
| **F1** | **Instant `.part` pre-allocation** | Windows `truncate()` zero-filled the whole file: a 68 GB download sat on "Preparing download file…" for 5–10 minutes with a frozen 0 B bar. |
| **F2** | **Live "preparing" indicator** | While allocating/connecting the byte counter is legitimately 0, so a plain 0 % bar looks frozen. Animated green bar + determinate fill once allocation size is known. |
| **F3** | **Human size text + disk usage** | `0 B / 68.3 GB` → `1.2 GB out of 68.3 GB`, plus `Disk: 198 GB free of 931 GB` during **prepare** and **extract**. |
| **F4** | **Interrupted-download recovery prompt** | After a crash/cancel the app auto-resumed silently, and abandoned partials silently ate disk. Now the user explicitly picks **Resume** or **Delete partial files**. |
| **F5** | **Correct partial-byte accounting** | Progress must come from download **metadata**, never from the size of a pre-allocated (mostly empty) `.part` file. |

### 1.1 Measured impact of F1 (Windows, 8 GB file, NTFS)

| Metric | `truncate()` (old) | `seek(total-1)+write` (new) |
|--------|-------------------|------------------------------|
| Allocation time | 12.20 s | **0.00 s** |
| Free space reserved | 8.26 GB | **8.00 GB** (still reserved) |
| First write at far offset | 0.59 s | 4.20 s (deferred NTFS zero-fill) |

**Conclusion:** the zero-fill cost is *moved to the background*, not eliminated. Space is still
reserved, so there is no mid-download "disk full" risk.

---

## 2. Platform reality check

**Do not port F1 blindly.** The three platforms have different filesystem behaviour and
different download engines.

### 2.1 Filesystem behaviour

| Platform | FS | `truncate(total)` behaviour | Is F1 needed? |
|----------|----|------------------------------|---------------|
| Windows | NTFS | **Zero-fills** — writes every byte | **Yes** (the actual bug) |
| SteamOS / Bazzite | ext4 / btrfs | Creates a **sparse** file instantly | **No** — but the space is *not* reserved, so add an explicit free-space check instead |
| Android | ext4 / f2fs | Sparse, instant | **No** |

> **SteamOS/Android agents:** skip the `seek+write` trick. Instead keep the existing
> allocation and make sure the **free-space pre-check** (F3) runs before enqueue, because a
> sparse file will happily let the user run out of disk mid-download.

### 2.2 Download engine differences — this is the important one

| | Windows 2.7.8 | SteamOS 2.6.8 | Android 1.0.7 |
|---|---|---|---|
| Multi-conn layout | **one pre-allocated `.part`**, workers write at byte offsets | `.part.partN` chunk files | `.part.partN` chunk files |
| Final step | `os.replace(.part → dest)` — **no merge** | **`MERGING` pass** copies all chunks | **`mergeChunks()`** copies all chunks |
| Resume metadata | **`.part.progress`** JSON (`done[]` per range) | sum of chunk file sizes | sum of chunk file sizes |
| Queue file | `.quickplay_downloads.json` in download folder | same | `filesDir/downloads.json` |

**Consequence:** SteamOS and Android have **no "Preparing/Allocating" phase at all** — but they
both have a **merge phase** that is exactly the same kind of dead wait (copying 68 GB with a
frozen bar). So on those platforms **F2 applies to the merge phase, not the allocate phase.**

```
Windows :  Connecting → [Allocating]  → Downloading →              → Extract
SteamOS :  Connecting →               → Downloading → [Merging]    → Extract
Android :  Resolving  →               → Downloading → [Merging]    → Extract
                          ^^^^^^^^^^                  ^^^^^^^^^
                          F2 attaches here            F2 attaches here
```

---

## 3. Windows reference implementation (read before porting)

All snippets below are the **actual 2.7.8 code**. Use them as the behavioural spec.

### 3.1 F1 — fast allocation + prepare callback (`idm_downloader.py`)

New callback type near the top of the file:

```python
ProgressCallback = Callable[[int, int, float, str], None]
StatusCallback = Callable[[str], None]
# (allocated_bytes, total_bytes, disk_free_bytes, disk_total_bytes)
PrepareCallback = Callable[[int, int, int, int], None]
```

Constructor gains `on_prepare`:

```python
def __init__(
    self,
    on_progress: ProgressCallback | None = None,
    on_status: StatusCallback | None = None,
    on_prepare: PrepareCallback | None = None,
) -> None:
```

The allocation itself. **Key points:** runs on a worker thread so the caller can emit progress
every 0.4 s; uses `r+b` when the file exists so an existing partial is never truncated to zero;
`seek(total-1) + write` instead of `truncate(total)`.

```python
@staticmethod
def _disk_usage_for(part_path: str) -> tuple[int, int]:
    try:
        usage = shutil.disk_usage(os.path.dirname(part_path) or ".")
        return usage.free, usage.total
    except OSError:
        return 0, 0

def _notify_prepare(self, part_path: str, total: int) -> None:
    if not self.on_prepare:
        return
    try:
        allocated = os.path.getsize(part_path) if os.path.isfile(part_path) else 0
    except OSError:
        allocated = 0
    free_bytes, disk_total = self._disk_usage_for(part_path)
    self.on_prepare(allocated, total, free_bytes, disk_total)

def _ensure_multi_part_file(self, part_path: str, total: int) -> None:
    if os.path.isfile(part_path) and os.path.getsize(part_path) == total:
        self._notify_prepare(part_path, total)
        return
    if total <= 0:
        with open(part_path, "ab"):
            pass
        return

    # `truncate()` zero-fills on Windows: a 60+ GB pre-allocation writes every
    # byte and blocks for minutes. Setting the final size with a single write
    # at the last offset leaves the zero-filling to NTFS.
    errors: list[Exception] = []

    def allocate() -> None:
        try:
            mode = "r+b" if os.path.isfile(part_path) else "wb"
            with open(part_path, mode) as handle:
                handle.seek(total - 1)
                handle.write(b"\0")
                handle.flush()
        except Exception as exc:      # surfaced on the calling thread
            errors.append(exc)

    worker = threading.Thread(target=allocate, name="part-allocate", daemon=True)
    worker.start()
    while worker.is_alive():
        self._notify_prepare(part_path, total)
        worker.join(0.4)
    if errors:
        raise errors[0]
    self._notify_prepare(part_path, total)
```

### 3.2 F5 — never trust the `.part` size

```python
def _prepare_resume(self, part_path: str, connections: int = 1, total: int = 0) -> int:
    """Bytes already saved (never treat a sparse multi-conn shell as fully downloaded)."""
    if connections > 1:
        existing = self._multi_bytes_done(part_path, connections)
        if existing is not None:
            return existing
        if total > 0 and self._looks_uninitialized_part(part_path, total):
            return 0
        return 0
    if os.path.exists(part_path):
        return os.path.getsize(part_path)
    return 0
```

Module-level helpers used by the service layer for the recovery prompt:

```python
def partial_bytes_for_dest(dest_path: str, connections: int = 8) -> tuple[int, int]:
    """Return (bytes_downloaded, total_size_if_known) from progress metadata,
    not sparse .part size."""
    ...

def part_disk_bytes(dest_path: str) -> int:
    """Raw on-disk size of <dest>.part — shown as 'reserved', never as progress."""
    ...
```

### 3.3 F3 — task state fields (`download_service.py`)

```python
@dataclass
class TaskView:
    ...
    extract_current: int = 0
    extract_total: int = 100
    extract_message: str = ""
    prepare_current: int = 0     # bytes reserved so far
    prepare_total: int = 0       # final archive size
    disk_free: int = 0
    disk_total: int = 0
```

Wiring the downloader callback:

```python
def on_prepare(allocated: int, total: int, free_bytes: int, disk_total: int) -> None:
    with self._lock:
        v = self._task_views.get(task_id)
        if not v or v.phase in ("resolving", "rate_limit"):
            return
        if v.state in (DownloadState.CANCELLED.value, DownloadState.PAUSED.value):
            return
        v.prepare_current = allocated
        v.prepare_total = total
        v.disk_free = free_bytes
        v.disk_total = disk_total
        if total > 0 and v.total_size <= 0:
            v.total_size = total
        payload = asdict(v)
    self.emit("task_update", payload)

downloader = IDMDownloader(
    on_progress=on_progress,
    on_status=on_status,
    on_prepare=on_prepare,
)
```

Clear the prepare fields the moment real downloading starts (inside `on_progress`):

```python
if state == DownloadState.DOWNLOADING.value:
    v.prepare_current = 0
    v.prepare_total = 0
```

### 3.4 F3 — disk usage during extraction

```python
view.phase = "extract"
view.extract_message = "Preparing extraction..."
extract_usage = disk_usage_for_path(install_dir)
view.disk_free = int(extract_usage["free_bytes"])
view.disk_total = int(extract_usage["total_bytes"])

# Extraction can eat tens of GB; refresh free space at most once a second
# so the UI shows disk pressure without a stat() call per progress line.
last_disk_poll = [0.0]

def on_extract_progress(current: int, total: int, message: str) -> None:
    self.logger.info(f"[{game.title}] {message}")
    now = time.time()
    usage = None
    if now - last_disk_poll[0] >= 1.0:
        last_disk_poll[0] = now
        try:
            usage = disk_usage_for_path(install_dir)
        except OSError:
            usage = None
    with self._lock:
        v = self._task_views.get(task_id)
        if not v:
            return
        v.extract_current = current
        v.extract_total = total
        v.extract_message = message
        if usage:
            v.disk_free = int(usage["free_bytes"])
            v.disk_total = int(usage["total_bytes"])
        payload = asdict(v)
    self.emit("task_update", payload)
```

> **Throttle rule:** never `stat()` the filesystem on every progress line. 7-Zip emits a line
> per percent per file; on a 200 GB extract that is tens of thousands of syscalls.

### 3.5 F4 — recovery hold instead of silent auto-resume

In `resume_pending_downloads()`, saved downloads **that still have partial fragments** are put
on hold instead of auto-starting:

```python
if has_partial:
    recovery_items.append(item)
else:
    to_resume.append(item)

if recovery_items:
    payloads = [self._recovery_payload(item) for item in recovery_items]
    with self._lock:
        self._recovery_hold = {item.dest_path: item for item in recovery_items}
        self._recovery_payloads = payloads
    self.emit("download_recovery", {"items": payloads})
```

Payload shape consumed by the UI:

```python
{
  "dest_path": ..., "game_id": ..., "title": ..., "image_url": ...,
  "connections": 8,
  "downloaded": 12_884_901_888,   # verified bytes from .part.progress
  "total_size": 73_364_144_128,
  "disk_bytes": 73_364_144_128,   # raw .part size ("reserved")
}
```

Two service methods + two HTTP routes:

```python
def resume_interrupted_download(self, dest_path: str) -> dict[str, Any]: ...
def discard_interrupted_download(self, dest_path: str) -> dict[str, Any]: ...
```

```python
@app.get("/api/downloads/recovery")
async def api_list_recovery_downloads():
    return {"items": get_service().list_recovery_downloads()}

@app.post("/api/downloads/recovery")      # body: {dest_path, action: resume|discard}
async def api_recovery_download_action(payload: RecoveryActionBody): ...
```

**Critical:** held items must be added to the orphan-sweep protection set, or the startup
sweeper will delete the very files the user is being asked about:

```python
protected = self._protected_download_dest_paths()
with self._lock:
    for item in self._recovery_hold.values():
        if item.dest_path:
            protected.add(os.path.normpath(item.dest_path))
```

### 3.6 F2/F3 — web UI (`web/app.js`)

```js
/** Connecting / pre-allocating .part — bytes often stay at 0%; show an active bar. */
function taskIsPreparingDownload(t) {
  if (t.phase !== "download") return false;
  if (t.state === "Connecting" || t.state === "Allocating") return true;
  const msg = (t.status_message || "").toLowerCase();
  return msg.includes("preparing download file") || msg.includes("kumokonekta");
}

/** Allocation progress is known, so the "preparing" bar can fill instead of sliding. */
function taskPrepareIsMeasured(task) {
  return taskIsPreparingDownload(task) && (task.prepare_total || task.total_size || 0) > 0;
}

function taskPreparePct(task) {
  const total = task.prepare_total || task.total_size || 0;
  if (total <= 0) return 0;
  return Math.min(100, ((task.prepare_current || 0) / total) * 100);
}

function formatDiskUsage(task) {
  if (!task.disk_total) return "";
  return t("download.diskUsage", {
    free: formatBytesHuman(task.disk_free || 0),
    total: formatBytesHuman(task.disk_total || 0),
  });
}
```

Extract line gets its own formatter so the download byte summary is no longer mixed with the
extract percentage (that produced the confusing `96.0% | 15.9 GB out of 15.9 GB`):

```js
function formatExtractMeta(task) {
  const pct = task.extract_total > 0
    ? Math.min(100, (task.extract_current / task.extract_total) * 100) : 0;
  const parts = [`${pct.toFixed(1)}%`];
  const msg = displayText(task.extract_message || "");
  if (msg) parts.push(msg);
  const disk = formatDiskUsage(task);
  if (disk) parts.push(disk);
  return parts.join(" | ");
}
```

CSS states (`web/style.css`):

```css
.progress-bar.preparing              { background: #1a2e1a; }             /* indeterminate */
.progress-bar.preparing:not(.measured) .progress-fill { animation: qp-preparing-bar 1.35s ease-in-out infinite; }
.progress-bar.preparing.measured .progress-fill       { animation: none; transition: width .4s ease-out; }
.progress-bar.active .progress-fill.active-download   { animation: qp-download-shimmer 2s ease-in-out infinite; }
```

### 3.7 New i18n keys (add to **all** locales)

| Key | English |
|-----|---------|
| `download.sizeOutOf` | `{done} out of {total}` |
| `download.reservedOutOf` | `{done} out of {total} reserved` |
| `download.diskUsage` | `Disk: {free} free of {total}` |
| `download.recoveryHeading` | `Interrupted downloads — choose what to do` |
| `download.recoveryResume` | `Resume` |
| `download.recoveryDiscard` | `Delete partial files` |
| `download.recoveryDiscardTitle` | `Delete partial files?` |
| `download.recoveryDiscardMsg` | `Delete partial files for "{title}" and free disk space? …` |
| `download.recoveryDiscardConfirm` | `Delete files` |
| `download.recoveryDiskHint` | `{saved} saved · {disk} on disk` |

Tagalog reference: `{done} sa {total}`, `Disk: {free} libre sa {total}`,
`Naantala ang download — piliin ang gawin`, `Ituloy`, `Burahin ang partial files`.

---

## 4. SteamOS port (`DUALSERVER-STEAMOS-PORT` → `QuickPlaySteamOS/`)

The SteamOS tree is a near-mirror of the Windows Python app, so this is mostly a **three-way
merge**, not a rewrite. It is currently on **2.6.8** and still uses the **chunk-file + merge**
downloader.

### 4.1 Decision: engine first, or UI first?

| Option | Do this when |
|--------|--------------|
| **A. UI-only port (recommended first PR)** | Fastest win. Keep the `.part.partN` + merge engine; attach F2 to the **Merging** phase. No resume-format risk. |
| **B. Full engine port** | Port Windows `idm_downloader.py` wholesale (in-place writes + `.part.progress`). Bigger win (kills the merge pass entirely) but invalidates in-flight partials. |

If you choose **B**, the legacy migration already exists in the Windows file
(`_migrate_legacy_chunks`) and converts `.part.partN` → single `.part` on first resume. Port it
verbatim; do **not** write a new migration.

### 4.2 Step-by-step (Option A)

1. **Checkout + worktree**

   ```bash
   git fetch origin DUALSERVER-STEAMOS-PORT quickplay-2.7.5-beta
   git switch -c feature/steamos-2.7.8-download-ux origin/DUALSERVER-STEAMOS-PORT
   git worktree add ../qp-win278 origin/quickplay-2.7.5-beta   # reference tree
   ```

2. **`QuickPlaySteamOS/settings_manager.py`** — bump `APP_VERSION` / `APP_TITLE` to **`2.7.8`**.
   Also update `QuickPlaySteamOS/web/index.html` (`<title>` + `#appVersionLabel`).

3. **`QuickPlaySteamOS/idm_downloader.py`**
   - Add `PrepareCallback` type + `on_prepare` constructor arg + `_disk_usage_for()` +
     `_notify_prepare()` (§3.1) — **skip `_ensure_multi_part_file`**, it does not exist here.
   - Call `_notify_prepare(part_path, total)` from the **`MERGING`** block, inside the merge
     copy loop, roughly every 0.4 s. `allocated` = bytes merged so far, `total` = archive size.
   - Add `partial_bytes_for_dest()` / `part_disk_bytes()`. On this branch there is no
     `.part.progress`, so `partial_bytes_for_dest` must sum `*.part.partN` sizes — exactly what
     `DownloadService` already does inline today; move that logic into the helper.

4. **`QuickPlaySteamOS/download_service.py`**
   - Add the four `TaskView` fields (§3.3).
   - Add `on_prepare` wiring (§3.3).
   - Add extract disk polling (§3.4) — `disk_space.py` already exists on this branch with
     `disk_usage_for_path`, no new module needed.
   - Add `_recovery_hold` / `_recovery_payloads`, `_recovery_payload()`,
     `_begin_resume_queued_item()`, `list_recovery_downloads()`,
     `resume_interrupted_download()`, `discard_interrupted_download()` (§3.5).
   - Add the recovery paths to `_sweep_orphaned_part_files()`'s protected set.

5. **`QuickPlaySteamOS/backend/server.py`** — add the two `/api/downloads/recovery` routes.

6. **`QuickPlaySteamOS/web/`** — port `app.js`, `i18n.js`, `style.css`, `index.html` hunks.
   The SteamOS `app.js` already has free-space helpers (`formatBytes(u.free_bytes)` in the
   settings pane) — **reuse `formatBytesHuman` for the new strings** and leave the existing
   settings/space-check text alone.

7. **Linux-specific adjustments**
   - Replace the `Kumokonekta` / `Preparing download file` string sniffing in
     `taskIsPreparingDownload()` with a `Merging` check:
     ```js
     if (t.state === "Merging") return true;
     ```
   - `shutil.disk_usage()` works unchanged on Linux — no change needed.
   - **Add a free-space guard**: because ext4/btrfs allocate sparsely, check
     `disk_usage_for_path(download_dir).free_bytes >= total` before starting, and surface
     `space_error_message()` (already in `storage_requirements.py`).

8. **Build & verify**

   ```bash
   cd QuickPlaySteamOS
   python -m PyInstaller --noconfirm --clean QuickPlaySteamOS.spec
   ```

   Also check `packaging/AppRun` + `quickplay.desktop` still reference the right version.

### 4.3 SteamOS acceptance checklist

- [ ] Settings shows **Version 2.7.8** on the AppImage.
- [ ] Merging a >20 GB archive shows a moving green bar + `X out of Y merged` + disk usage.
- [ ] Extract pane shows `NN.N% | <file> | Disk: X free of Y`.
- [ ] Kill the app mid-download, relaunch → recovery banner with **Resume** / **Delete partial files**.
- [ ] **Delete partial files** actually reclaims space (`df -h` before/after).
- [ ] Startup orphan sweep does **not** delete files that are on the recovery hold.

---

## 5. Android port (GameHub, module `quickplay`)

Kotlin + Jetpack Compose. There is no HTML/SSE layer, so §3.6 becomes Compose code and §3.5's
REST routes become `DownloadCoordinator` methods called from `QuickPlayViewModel`.

### 5.1 File map

| Concern | Android file |
|---------|--------------|
| Download orchestration | `quickplay/src/main/java/com/quickplay/android/download/DownloadCoordinator.kt` |
| HTTP engine + merge | `.../download/MultiConnectionDownloader.kt` |
| Persisted queue | `.../download/DownloadQueueStore.kt` (`filesDir/downloads.json`) |
| Extraction | `.../extract/ArchiveExtractor.kt` |
| UI (tasks pane) | `.../ui/QuickPlayAppUi.kt` → `DownloadsPane` / `TaskRow` |
| ViewModel | `.../ui/QuickPlayViewModel.kt` |
| Notification | `.../download/DownloadNotifier.kt` |
| Version | `gamenative/build.gradle.kts` → `versionName` |
| Free space helper (exists!) | `gamenative/src/main/java/app/gamenative/utils/StorageUtils.kt` |

### 5.2 Step 1 — extend the task model (F3)

`DownloadCoordinator.kt`:

```kotlin
data class DownloadTaskUi(
    val id: String,
    val title: String,
    val gameId: String,
    val store: String,
    val coverUrl: String,
    val state: TaskState,
    val downloaded: Long = 0,
    val total: Long = 0,
    val speedBps: Double = 0.0,
    val message: String = "",
    val error: String = "",
    val destPath: String = "",
    // --- 2.7.8 port ---
    val prepareCurrent: Long = 0,   // bytes merged/allocated so far
    val prepareTotal: Long = 0,     // 0 = not in a prepare/merge phase
    val diskFree: Long = 0,
    val diskTotal: Long = 0,
)
```

### 5.3 Step 2 — disk usage helper

QuickPlay has **no** `StatFs` helper; GameNative does. Either reuse `StorageUtils` or add a
small local one to keep the `quickplay` module self-contained:

```kotlin
// quickplay/src/main/java/com/quickplay/android/core/DiskSpace.kt
package com.quickplay.android.core

import android.os.StatFs
import java.io.File

data class DiskUsage(val free: Long, val total: Long)

fun diskUsageFor(path: File): DiskUsage = try {
    var dir: File? = path
    while (dir != null && !dir.exists()) dir = dir.parentFile
    val stat = StatFs((dir ?: File("/")).absolutePath)
    DiskUsage(stat.availableBytes, stat.totalBytes)
} catch (_: Throwable) {
    DiskUsage(0, 0)
}
```

### 5.4 Step 3 — F2 on the merge phase

Android's dead wait is `MultiConnectionDownloader.mergeChunks()`. Give it a progress callback:

```kotlin
private fun mergeChunks(
    part: File,
    n: Int,
    dest: File,
    total: Long,
    onMerge: (merged: Long, total: Long) -> Unit,   // NEW
) {
    RandomAccessFile(part, "rw").use { out ->
        out.setLength(0)
        val buf = ByteArray(512 * 1024)
        var merged = 0L
        var lastTick = 0L
        for (i in 0 until n) {
            val chunk = File("${part.path}.part$i")
            FileInputStream(chunk).use { input ->
                while (true) {
                    val read = input.read(buf)
                    if (read <= 0) break
                    out.write(buf, 0, read)
                    merged += read
                    val now = System.currentTimeMillis()
                    if (now - lastTick >= 400) {          // throttle like Windows' 0.4s
                        lastTick = now
                        onMerge(merged, total)
                    }
                }
            }
            chunk.delete()
        }
        onMerge(merged, total)
    }
    finalizePart(part, dest)
}
```

In `DownloadCoordinator`, map it onto the new fields:

```kotlin
onMerge = { merged, total ->
    val usage = diskUsageFor(File(destPath).parentFile ?: File("/"))
    mutate(id) {
        it.copy(
            state = TaskState.MERGING,
            prepareCurrent = merged,
            prepareTotal = total,
            diskFree = usage.free,
            diskTotal = usage.total,
            message = "Preparing download file…",
        )
    }
}
```

> Add `MERGING` to the `TaskState` enum if it is not there yet, and make sure
> `prepareTotal` is reset to `0` when the state leaves merging — the UI keys off it.

### 5.5 Step 4 — F3 on extraction

`DownloadCoordinator`'s extract lambda already receives `(cur, total, name)`. Add throttled
disk polling:

```kotlin
var lastDiskPoll = 0L
ArchiveExtractor.extract(archive, ArchiveExtractor.Dest(installDir), removeStoreJunk) { cur, total, name ->
    val safeTotal = total.coerceAtLeast(1)
    val pct = ((cur * 100f) / safeTotal).toInt().coerceIn(0, 100)
    val now = System.currentTimeMillis()
    val usage = if (now - lastDiskPoll >= 1000) { lastDiskPoll = now; diskUsageFor(installDir) } else null
    mutate(id) {
        it.copy(
            downloaded = cur.toLong(),
            total = safeTotal.toLong(),
            message = if (pct >= 100) "Extracting 100%" else "Extracting $pct%  $name",
            diskFree = usage?.free ?: it.diskFree,
            diskTotal = usage?.total ?: it.diskTotal,
        )
    }
}
```

### 5.6 Step 5 — Compose UI (F2 + F3)

`QuickPlayAppUi.kt`, inside `TaskRow`. Replace the single `LinearProgressIndicator` with a
phase-aware one:

```kotlin
val preparing = task.prepareTotal > 0L
val prepareFraction = if (preparing) (task.prepareCurrent.toFloat() / task.prepareTotal).coerceIn(0f, 1f) else 0f

when {
    preparing -> {
        LinearProgressIndicator(
            progress = { prepareFraction },
            color = Color(0xFF6ECF7A),                       // green = preparing
            trackColor = Color(0xFF1A2E1A),
            modifier = Modifier.fillMaxWidth(),
        )
        Text(
            "${(prepareFraction * 100).format(1)}% | " +
                "${formatBytes(task.prepareCurrent)} out of ${formatBytes(task.prepareTotal)} reserved | " +
                diskLine(task),
            color = Muted, fontSize = 12.sp,
        )
    }
    task.state == TaskState.EXTRACTING -> {
        LinearProgressIndicator(progress = { extractFraction }, modifier = Modifier.fillMaxWidth())
        Text("$pct% | ${task.message} | ${diskLine(task)}", color = Muted, fontSize = 12.sp)
    }
    else -> {
        LinearProgressIndicator(progress = { downloadFraction }, modifier = Modifier.fillMaxWidth())
        Text(
            "${formatBytes(task.downloaded)} out of ${formatBytes(task.total)}   ${formatSpeed(task.speedBps)}",
            color = Muted, fontSize = 12.sp,
        )
    }
}
```

Helper:

```kotlin
private fun diskLine(task: DownloadTaskUi): String =
    if (task.diskTotal <= 0) "" else "Disk: ${formatBytes(task.diskFree)} free of ${formatBytes(task.diskTotal)}"
```

**Indeterminate fallback:** when `prepareTotal == 0` but the state is merging/connecting, use
`LinearProgressIndicator(modifier = ...)` with no `progress` argument — Compose renders the
animated indeterminate bar, which is the Android equivalent of the sliding green CSS bar.

**Notification:** mirror the same numbers in `DownloadNotifier` so the shade does not show a
stalled 0 %.

### 5.7 Step 6 — F4 recovery prompt

`DownloadCoordinator.restorePending()` currently resumes silently. Split it:

```kotlin
data class RecoveryItem(
    val destPath: String,
    val gameId: String,
    val title: String,
    val coverUrl: String,
    val connections: Int,
    val downloaded: Long,     // from partialBytes()
    val totalSize: Long,
    val diskBytes: Long,      // raw .part size on disk
)

private val _recovery = MutableStateFlow<List<RecoveryItem>>(emptyList())
val recovery: StateFlow<List<RecoveryItem>> = _recovery

fun resumeInterrupted(destPath: String) { /* pop from _recovery → normal enqueue/resume */ }
fun discardInterrupted(destPath: String) {
    /* pop from _recovery, queueStore.remove(destPath), deletePartFiles(destPath) */
}
```

In `restorePending()`: if `partialBytes(dest) > 0` **or** any `.part*` file exists, push to
`_recovery` instead of starting the download.

Expose `recovery` through `QuickPlayViewModel` and render a banner above the task list in
`DownloadsPane` with **Resume** and **Delete partial files** buttons (confirm dialog on delete).

> **Android-specific hazard:** `clearStaleArchives()` runs on startup and deletes abandoned
> archives. It **must** skip anything currently in `_recovery`, mirroring the Windows
> protected-path fix in §3.5.

### 5.8 Step 7 — version + strings

- `gamenative/build.gradle.kts`: bump `versionName` (e.g. `1.0.8`) and `versionCode` → `10`.
- QuickPlay UI strings are hardcoded English today. Either keep them inline (fastest) or move
  the ten new strings into `quickplay/src/main/res/values/strings.xml`. If you add localized
  variants, remember release builds set `resourceConfigurations += listOf("en")` in
  `gamenative/build.gradle.kts` — non-English resources are stripped unless that line changes.

### 5.9 Android acceptance checklist

- [ ] `versionName` bumped; About/Settings shows it.
- [ ] Merging a large archive shows a moving bar + `X out of Y reserved` + disk line (not a frozen 0 %).
- [ ] Extract row shows percent + current file + `Disk: X free of Y`.
- [ ] Force-stop mid-download, relaunch → recovery banner, **not** auto-resume.
- [ ] **Delete partial files** removes `.part` + `.part.partN` and frees space.
- [ ] `clearStaleArchives()` does not delete files awaiting a recovery decision.
- [ ] Notification progress matches the in-app numbers.

---

## 6. Shared pitfalls (all platforms)

1. **Never derive progress from the `.part` file size** when the engine pre-allocates. Use
   metadata (`.part.progress` / chunk sums). This was the original bug: a 55 GB reserved file
   with 0 downloaded bytes.
2. **Throttle disk queries.** 0.4 s during prepare/merge, 1.0 s during extract.
3. **Protect recovery files from the orphan sweeper.** Both platforms delete stale partials on
   startup; a held item must be exempt or the Resume button deletes the user's 50 GB.
4. **Reset the prepare fields** when the real download starts, or the UI stays green forever.
5. **Open existing partials with `r+b` / append**, never a truncating mode, when extending an
   allocation. Windows 2.7.8 fixed a latent bug here that silently wiped partial data.
6. **Reserve vs. download wording.** "Reserved on disk" and "downloaded" are different numbers;
   never show reserved bytes as progress.

---

## 7. Suggested commit messages

```
feat(steamos): port 2.7.8 download UX — merge progress, disk usage, recovery prompt
feat(android): merge/extract progress with disk usage + interrupted-download recovery
chore(steamos): bump APP_VERSION to 2.7.8
chore(android): bump versionName to 1.0.8 (versionCode 10)
```

---

## 8. Agent kickoff prompt (copy-paste)

```
You are porting QuickPlay Windows 2.7.8 download UX to <android|steamos>.

Read PORT_AGENTIC_INSTRUCTION.md in the Windows tree (branch quickplay-2.7.5-beta) FIRST,
then AGENTS.md for the download/resume invariants.

Source of truth for 2.7.8 behaviour: idm_downloader.py (_ensure_multi_part_file,
_notify_prepare, _prepare_resume, partial_bytes_for_dest), download_service.py
(TaskView prepare_*/disk_* fields, _recovery_hold, extract disk polling),
backend/server.py (/api/downloads/recovery), web/app.js (taskIsPreparingDownload,
taskPrepareIsMeasured, formatExtractMeta, formatDiskUsage).

CRITICAL: do NOT port the seek(total-1)+write allocation trick — that fixes a Windows/NTFS
zero-fill problem that does not exist on ext4/f2fs/btrfs. On your platform the equivalent
dead-wait is the MERGE phase; attach the progress indicator there instead (see §2.2).

Deliver: code changes + the acceptance checklist in §4.3 (steamos) or §5.9 (android) verified.
```

---

*Authored from the Windows `quickplay-2.7.5-beta` workspace at version 2.7.8. Android facts
verified against `C:\Users\user\Desktop\gamehub` (`versionName 1.0.7`); SteamOS facts verified
against `origin/DUALSERVER-STEAMOS-PORT` (`APP_VERSION 2.6.8`).*
