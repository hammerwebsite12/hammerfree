"""Bounded-concurrency image loading queue for cover art."""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable

from ui_components import load_cover_pil

ProcessFn = Callable[[], tuple[bytes, int, int] | None]  # RGB bytes, w, h
ApplyFn = Callable[[object], None]
LogFn = Callable[[str], None]
ShouldApplyFn = Callable[[], bool]


class ImageLoadQueue:
    """Background PIL work + throttled PhotoImage apply on the UI thread."""

    def __init__(
        self,
        max_workers: int = 2,
        on_error: LogFn | None = None,
        should_apply_ui: ShouldApplyFn | None = None,
    ) -> None:
        self._semaphore = threading.Semaphore(max_workers)
        self._lock = threading.Lock()
        self._pending_ui: deque[tuple[bytes, int, int, ApplyFn]] = deque()
        self._ui_scheduled = False
        self._ui_root = None
        self._on_error = on_error
        self._should_apply_ui = should_apply_ui
        self._active = 0

    def bind_root(self, root) -> None:
        self._ui_root = root

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._active > 0 or len(self._pending_ui) > 0

    @property
    def pending_ui_count(self) -> int:
        with self._lock:
            return len(self._pending_ui)

    def cancel_pending(self) -> None:
        with self._lock:
            self._pending_ui.clear()
            self._ui_scheduled = False

    def submit(self, work: ProcessFn, apply_photo: ApplyFn) -> None:
        def worker() -> None:
            with self._semaphore:
                with self._lock:
                    self._active += 1
                try:
                    result = work()
                except Exception as exc:
                    if self._on_error:
                        self._on_error(f"Image worker failed: {exc}")
                    return
                finally:
                    with self._lock:
                        self._active -= 1
                if result and self._ui_root:
                    rgb_bytes, width, height = result
                    self._enqueue_ui(rgb_bytes, width, height, apply_photo)

        threading.Thread(target=worker, daemon=True).start()

    def _enqueue_ui(
        self,
        rgb_bytes: bytes,
        width: int,
        height: int,
        apply_photo: ApplyFn,
    ) -> None:
        with self._lock:
            if len(self._pending_ui) > 64:
                return
            self._pending_ui.append((rgb_bytes, width, height, apply_photo))
            if not self._ui_scheduled and self._ui_root:
                self._ui_scheduled = True
                self._ui_root.after_idle(self._flush_ui)

    def _can_apply_ui(self) -> bool:
        if self._should_apply_ui and not self._should_apply_ui():
            return False
        return True

    def _flush_ui(self) -> None:
        if not self._can_apply_ui():
            with self._lock:
                self._ui_scheduled = False
            if self._pending_ui and self._ui_root:
                self._ui_root.after(120, self._schedule_flush)
            return

        item: tuple[bytes, int, int, ApplyFn] | None = None
        with self._lock:
            if self._pending_ui:
                item = self._pending_ui.popleft()
            if not self._pending_ui:
                self._ui_scheduled = False

        if item is None:
            return

        rgb_bytes, width, height, apply_photo = item
        try:
            from PIL import Image, ImageTk
        except ImportError:
            ImageTk = None  # type: ignore

        try:
            if ImageTk is not None:
                image = Image.frombytes("RGB", (width, height), rgb_bytes)
                photo = ImageTk.PhotoImage(image)
                apply_photo(photo)
        except Exception as exc:
            if self._on_error:
                self._on_error(f"UI image apply failed: {exc}")

        if self._pending_ui and self._ui_root:
            self._ui_root.after(100, self._schedule_flush)

    def _schedule_flush(self) -> None:
        with self._lock:
            if self._ui_scheduled:
                return
            self._ui_scheduled = True
        self._flush_ui()


def fetch_and_render_cover(
    fetch_bytes: Callable[[], bytes],
    width: int,
    cached_bytes: bytes | None = None,
) -> tuple[bytes, int, int] | None:
    data = cached_bytes if cached_bytes is not None else fetch_bytes()
    if not data:
        return None
    image = load_cover_pil(data, width)
    if image is None:
        return None
    w, h = image.size
    return image.tobytes(), w, h
