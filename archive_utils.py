"""Extract downloaded archives into install folders with progress reporting."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import zipfile
from typing import Callable

from app_paths import bundled_7z_path
from path_utils import ensure_dir, is_windows, long_path, open_binary

ExtractProgressCallback = Callable[[int, int, str], None]

# Store-packaged junk removed silently after extraction (hide repack source).
_JUNK_EXACT_NAMES = frozenset(
    {
        "read me.txt",
        "readme.txt",
        "read_me.txt",
        "launcher.log",
    }
)
_JUNK_NAME_MARKERS = (
    "ankergames",
    "pre-installed pc games",
    "free pre-installed",
)
_JUNK_EXTENSIONS = (".html", ".htm", ".url", ".lnk", ".website")
# Folder-name branding stripped instead of deleting game payloads (Server 2 repacks).
_STORE_FOLDER_BRANDING_RE = re.compile(
    r"[-_.]?(?:ankergames(?:\.net)?|pre[-_ ]?installed[-_ ]?(?:pc[-_ ]?)?games?"
    r"|free[-_ ]?pre[-_ ]?installed)[-_.]*",
    re.I,
)


def extract_archive(
    archive_path: str,
    dest_dir: str,
    on_progress: ExtractProgressCallback | None = None,
    *,
    remove_store_junk: bool = False,
) -> None:
    ensure_dir(dest_dir)
    ext = os.path.splitext(archive_path)[1].lower()

    def _finish() -> None:
        if remove_store_junk:
            _remove_extract_junk(dest_dir)

    if ext in (".zip", ".rar", ".7z") and _resolve_7z():
        _extract_with_7z_cli(archive_path, dest_dir, on_progress)
        _finish()
        return
    if ext == ".zip":
        _extract_zip(archive_path, dest_dir, on_progress)
        _finish()
        return
    if ext == ".7z":
        _extract_7z(archive_path, dest_dir, on_progress)
        _finish()
        return

    raise RuntimeError(f"Hindi supported ang archive type: {ext}")


def _report(
    on_progress: ExtractProgressCallback | None,
    current: int,
    total: int,
    message: str,
) -> None:
    if on_progress:
        on_progress(current, total, message)


def _extract_zip(
    archive_path: str,
    dest_dir: str,
    on_progress: ExtractProgressCallback | None,
) -> None:
    try:
        _extract_zip_stdlib(archive_path, dest_dir, on_progress)
    except (NotImplementedError, RuntimeError, zipfile.BadZipFile) as exc:
        message = str(exc).lower()
        if _resolve_7z() and (
            isinstance(exc, NotImplementedError)
            or "compression method" in message
            or "bad zip" in message
        ):
            _report(
                on_progress,
                0,
                100,
                "ZIP uses unsupported compression — retrying with 7-Zip...",
            )
            _extract_with_7z_cli(archive_path, dest_dir, on_progress)
            return
        raise


def _extract_zip_stdlib(
    archive_path: str,
    dest_dir: str,
    on_progress: ExtractProgressCallback | None,
) -> None:
    with zipfile.ZipFile(archive_path, "r") as zf:
        members = [m for m in zf.namelist() if not m.endswith("/")]
        total = len(members) or 1
        _report(on_progress, 0, total, f"Extracting ZIP ({total} files)...")

        for index, member in enumerate(members, start=1):
            safe_name = member.replace("\\", "/")
            target = os.path.join(dest_dir, *safe_name.split("/"))
            parent = os.path.dirname(target)
            if parent:
                ensure_dir(parent)
            with zf.open(member) as src, open_binary(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            name = safe_name.rsplit("/", 1)[-1]
            _report(on_progress, index, total, f"Extracting: {name}")

    _report(on_progress, total, total, "ZIP extraction complete")


def _extract_7z(
    archive_path: str,
    dest_dir: str,
    on_progress: ExtractProgressCallback | None,
) -> None:
    if _resolve_7z():
        _extract_with_7z_cli(archive_path, dest_dir, on_progress)
        return

    try:
        import py7zr
        from py7zr.callbacks import ExtractCallback
    except ImportError as exc:
        raise RuntimeError(
            "Kailangan ng 7-Zip o py7zr para sa .7z files."
        ) from exc

    _report(on_progress, 0, 100, "Opening 7z archive...")

    class ProgressHandler(ExtractCallback):
        def __init__(self) -> None:
            self.file_count = 0
            self.bytes_done = 0

        def report_start_preparation(self) -> None:
            _report(on_progress, 0, 100, "Preparing 7z extraction...")

        def report_start(self, processing_file_path: str, processing_bytes: str) -> None:
            name = processing_file_path.replace("\\", "/").rsplit("/", 1)[-1]
            _report(
                on_progress,
                self.file_count,
                max(self.file_count + 1, 1),
                f"Extracting: {name}",
            )

        def report_update(self, decompressed_bytes: str) -> None:
            try:
                self.bytes_done += int(decompressed_bytes)
            except (TypeError, ValueError):
                pass
            _report(
                on_progress,
                min(99, self.file_count),
                100,
                f"Extracting 7z... {format_bytes(self.bytes_done)}",
            )

        def report_end(self, processing_file_path: str, wrote_bytes: str) -> None:
            self.file_count += 1
            name = processing_file_path.replace("\\", "/").rsplit("/", 1)[-1]
            _report(on_progress, self.file_count, max(self.file_count, 1), f"Done: {name}")

        def report_warning(self, message: str) -> None:
            _report(on_progress, self.file_count, 100, f"Warning: {message[:80]}")

        def report_postprocess(self) -> None:
            _report(on_progress, 100, 100, "Finalizing 7z extraction...")

    handler = ProgressHandler()
    with py7zr.SevenZipFile(archive_path, mode="r") as archive:
        archive.extract(path=dest_dir, callback=handler)

    _report(on_progress, 100, 100, "7z extraction complete")


def _extract_with_7z_cli(
    archive_path: str,
    dest_dir: str,
    on_progress: ExtractProgressCallback | None,
) -> None:
    seven_zip = _resolve_7z()
    if not seven_zip:
        raise RuntimeError(
            "Hindi mahanap ang bundled 7-Zip. Sirang install? "
            "Bilang workaround, i-install ang 7-Zip: https://www.7-zip.org/"
        )

    ensure_dir(dest_dir)
    archive_arg = long_path(archive_path) if sys.platform == "win32" else archive_path
    out_arg = f"-o{long_path(dest_dir)}" if sys.platform == "win32" else f"-o{dest_dir}"
    _report(on_progress, 0, 100, "Running 7-Zip (long-path safe)...")
    popen_kwargs: dict = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "bufsize": 1,
    }
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    process = subprocess.Popen(
        [seven_zip, "x", archive_arg, out_arg, "-y", "-bsp1"],
        **popen_kwargs,
    )

    last_percent = 0
    assert process.stdout is not None
    for line in process.stdout:
        stripped = line.strip()
        if "%" in stripped:
            for token in stripped.split():
                if token.endswith("%"):
                    try:
                        last_percent = int(token.rstrip("%"))
                    except ValueError:
                        pass
                    break
            _report(on_progress, last_percent, 100, stripped[:80])
        elif stripped:
            _report(on_progress, last_percent, 100, stripped[:80])

    code = process.wait()
    if code != 0:
        raise RuntimeError("Archive extraction failed (7-Zip CLI).")

    _report(on_progress, 100, 100, "Extraction complete")


def _remove_extract_junk(dest_dir: str) -> None:
    """Delete store promo/launcher files bundled inside game archives."""
    if not dest_dir or not os.path.isdir(dest_dir):
        return

    dest_norm = os.path.normpath(dest_dir)
    for root, dirs, files in os.walk(dest_dir, topdown=True):
        root_norm = os.path.normpath(root)
        at_install_root = root_norm == dest_norm

        for dirname in list(dirs):
            if not _is_junk_dirname(dirname, at_install_root=at_install_root):
                continue
            full = os.path.join(root, dirname)
            if _dir_has_game_content(full):
                new_name = _strip_store_branding_folder_name(dirname)
                dest = _unique_sibling_dir(root, new_name)
                if os.path.normcase(full) != os.path.normcase(dest):
                    _safe_rename(full, dest)
                    if dirname in dirs:
                        dirs[dirs.index(dirname)] = os.path.basename(dest)
            else:
                _safe_rmtree(full)
        dirs[:] = [
            d
            for d in dirs
            if os.path.isdir(os.path.join(root, d))
            and not (
                at_install_root
                and _is_junk_dirname(d, at_install_root=True)
                and not _dir_has_game_content(os.path.join(root, d))
            )
        ]

        for name in files:
            if not _is_junk_file(name, at_install_root=at_install_root):
                continue
            _safe_unlink(os.path.join(root, name))


def _strip_store_branding_folder_name(name: str) -> str:
    cleaned = _STORE_FOLDER_BRANDING_RE.sub("-", name)
    cleaned = re.sub(r"[-_.\s]+", "-", cleaned).strip("-_. ")
    cleaned = re.sub(r"-+", "-", cleaned)
    return cleaned or "Game"


def _unique_sibling_dir(parent: str, name: str) -> str:
    candidate = os.path.join(parent, name)
    if not os.path.exists(candidate):
        return candidate
    stem, ext = os.path.splitext(name)
    if not ext:
        stem, ext = name, ""
    index = 2
    while True:
        suffix = f"-{index}"
        trimmed = stem[: max(1, 180 - len(ext) - len(suffix))] + suffix + ext
        candidate = os.path.join(parent, trimmed)
        if not os.path.exists(candidate):
            return candidate
        index += 1


_GAME_FILE_EXTENSIONS = (
    ".exe",
    ".pak",
    ".bin",
    ".dat",
    ".dll",
    ".vpk",
    ".bsa",
)


def _dir_has_game_content(dir_path: str) -> bool:
    for _walk_root, _subdirs, files in os.walk(dir_path):
        for name in files:
            if name.lower().endswith(_GAME_FILE_EXTENSIONS):
                return True
    return False


def _is_junk_dirname(name: str, *, at_install_root: bool) -> bool:
    if not at_install_root:
        return False
    lower = name.lower()
    return any(marker in lower for marker in _JUNK_NAME_MARKERS)


def _is_junk_file(name: str, *, at_install_root: bool) -> bool:
    lower = name.lower()
    if lower in _JUNK_EXACT_NAMES:
        return True
    if any(marker in lower for marker in _JUNK_NAME_MARKERS):
        return True
    ext = os.path.splitext(lower)[1]
    if ext in _JUNK_EXTENSIONS and any(marker in lower for marker in _JUNK_NAME_MARKERS):
        return True
    if at_install_root and _is_store_launcher_bat(lower):
        return True
    return False


def _is_store_launcher_bat(lower_name: str) -> bool:
    if not lower_name.endswith(".bat"):
        return False
    stem = os.path.splitext(lower_name)[0]
    if any(marker in stem for marker in _JUNK_NAME_MARKERS):
        return True
    normalized = re.sub(r"[^a-z0-9]", "", stem)
    return normalized == "runme"


def _safe_rename(src: str, dest: str) -> None:
    try:
        parent = os.path.dirname(dest)
        if parent:
            ensure_dir(parent)
        if is_windows():
            os.rename(long_path(src), long_path(dest))
        else:
            os.rename(src, dest)
    except OSError:
        pass


def _safe_unlink(path: str) -> None:
    try:
        if is_windows():
            os.remove(long_path(path))
        else:
            os.remove(path)
    except OSError:
        pass


def _safe_rmtree(path: str) -> None:
    try:
        from path_utils import remove_tree

        remove_tree(path)
    except OSError:
        pass


def _resolve_7z() -> str | None:
    """Locate a usable 7-Zip binary.

    Priority: bundled copy shipped with the app → system PATH → common
    Windows install locations. The bundled copy means the user does not
    need 7-Zip installed at all.
    """
    return bundled_7z_path() or shutil.which("7z") or _find_7z_windows()


def _find_7z_windows() -> str | None:
    candidates = [
        r"C:\Program Files\7-Zip\7z.exe",
        r"C:\Program Files (x86)\7-Zip\7z.exe",
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def format_bytes(num: int) -> str:
    if num <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(num)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{num} B"
