"""PlayZip Downloader - Browse, Library Manager, IDM-style downloads."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from collections import deque
from tkinter import messagebox, ttk

from archive_utils import extract_archive
from download_logger import DownloadLogger
from exe_scanner import pick_best_exe, scan_executables
from idm_downloader import DownloadState, DownloadTask, IDMDownloader
from image_loader import ImageLoadQueue, fetch_and_render_cover
from library_manager import LibraryEntry, LibraryManager
from playzip_api import GameResult, PlayZipClient, normalize_image_url
from ui_components import (
    ExePickerDialog,
    GameCoverCard,
    LibraryCard,
    capsule_size,
    compute_grid_layout,
)


def app_root_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def format_bytes(num: int) -> str:
    if num <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(num)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.2f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{num} B"


def format_speed(speed: float) -> str:
    return f"{format_bytes(int(speed))}/s"


def format_eta(downloaded: int, total: int, speed: float) -> str:
    if total <= 0 or speed <= 0 or downloaded >= total:
        return "--:--"
    remaining = total - downloaded
    seconds = int(remaining / speed)
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:d}:{secs:02d}"


class PlayZipDownloaderApp(tk.Tk):
    BG = "#0b0a0a"
    FG = "#e0e0e0"
    ACCENT = "#ffb347"
    PANEL = "#161414"
    BORDER = "#333333"
    CARD_W = 168
    CARD_H = int(round(CARD_W * 1.45))
    LOG_COLORS = {
        "INFO": "#c8c8c8",
        "WARN": "#ffb347",
        "ERROR": "#ff8080",
        "DEBUG": "#7aa2d6",
    }

    def __init__(self) -> None:
        super().__init__()
        self.title("PlayZip")
        self.geometry("1180x760")
        self.minsize(980, 640)
        self.configure(bg=self.BG)

        self.root_dir = app_root_dir()
        self.cache_dir = os.path.join(self.root_dir, "cache", "covers")
        self.rendered_cache_dir = os.path.join(self.cache_dir, "rendered")
        os.makedirs(self.cache_dir, exist_ok=True)
        os.makedirs(self.rendered_cache_dir, exist_ok=True)
        self.log_file = os.path.join(self.root_dir, "playzip.log")

        self.logger = DownloadLogger(on_log=self._append_log, log_file=self.log_file)
        self.logger.info("=== PlayZip starting ===")
        self.logger.info(f"Root: {self.root_dir}")
        self.client = PlayZipClient(logger=self.logger)
        self.library = LibraryManager(os.path.join(self.root_dir, "library.json"))
        self.CARD_H = capsule_size(self.CARD_W)[1]
        self.tasks: dict[str, DownloadTask] = {}
        self.task_rows: dict[str, dict[str, tk.Widget]] = {}
        self.task_meta: dict[str, GameResult] = {}
        self.search_results: list[GameResult] = []
        self.browse_games: list[GameResult] = []
        self.browse_page = 1
        self.browse_sort = "views"
        self.browse_view = "browse"
        self._photo_refs: dict[str, tk.PhotoImage] = {}
        self._image_bytes: dict[str, bytes] = {}
        self._rendered_cache: dict[tuple[str, int], tuple[bytes, int, int]] = {}
        self._cover_url_locks: dict[str, threading.Lock] = {}
        self._cover_lock_guard = threading.Lock()
        self._browse_card_w = self.CARD_W
        self._browse_grid_cols = 3
        self._library_card_w = self.CARD_W
        self._browse_cards: list[GameCoverCard] = []
        self._library_cards: list[LibraryCard] = []
        self._browse_card_games: list[GameResult] = []
        self._loaded_cover_keys: set[str] = set()
        self._browse_scroll_job: str | None = None
        self._library_scroll_job: str | None = None
        self._browse_nav_job: str | None = None
        self._browse_fetch_seq = 0
        self._cover_layout_session = 0
        self._pending_browse_relayout = False
        self._pending_library_relayout = False
        self._covers_loading = False
        self._browse_resize_ready = False
        self._library_resize_ready = False
        self._browse_relayout_job: str | None = None
        self._library_relayout_job: str | None = None
        self._scrollregion_job: str | None = None
        self._resizing = False
        self._resize_settle_job: str | None = None
        self._pending_resize_target = "browse"
        self._ui_ready = False
        self._library_loaded = False
        self._grid_render_token = 0
        self._library_grid_cols = 3
        self._library_render_token = 0
        self._rendering_browse_grid = False
        self._rendering_library_grid = False
        self._image_load_jobs: deque[tuple[str, str, callable, int]] = deque()
        self._image_drain_job: str | None = None
        self.image_queue = ImageLoadQueue(
            max_workers=2,
            on_error=lambda msg: self.logger.error(msg),
            should_apply_ui=lambda: not self._resizing,
        )

        self._build_styles()
        self.logger.info("Building UI...")
        self._build_ui()
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)
        self.logger.info("UI ready — scheduling startup")
        self.after(120, self._startup)

    def _startup(self) -> None:
        self.logger.info("Startup: binding image queue")
        self.image_queue.bind_root(self)
        self.update_idletasks()
        self.logger.info("Startup: loading browse games")
        self.load_browse_games()

    def _on_tab_changed(self, _event: tk.Event | None = None) -> None:
        if self.notebook.index(self.notebook.select()) != 1:
            return
        if self._library_loaded:
            return
        self._library_loaded = True
        self.after(50, self.refresh_library_grid)

    def _build_styles(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background=self.BG)
        style.configure("Card.TFrame", background=self.PANEL)
        style.configure("TLabel", background=self.BG, foreground=self.FG, font=("Segoe UI", 10))
        style.configure("Title.TLabel", background=self.BG, foreground=self.ACCENT, font=("Segoe UI", 22, "bold"))
        style.configure("Muted.TLabel", background=self.BG, foreground="#9a9a9a", font=("Segoe UI", 9))
        style.configure("Tab.TNotebook", background=self.BG, borderwidth=0)
        style.configure("Tab.TNotebook.Tab", background=self.PANEL, foreground=self.FG, padding=(16, 8))
        style.map("Tab.TNotebook.Tab", background=[("selected", "#2a2828")], foreground=[("selected", self.ACCENT)])
        style.configure("TProgressbar", troughcolor=self.PANEL, background=self.ACCENT)

    def _build_ui(self) -> None:
        header = ttk.Frame(self, style="TFrame")
        header.pack(fill="x", padx=20, pady=(16, 8))
        ttk.Label(header, text="PLAY", style="Title.TLabel").pack(side="left")
        zip_label = tk.Label(header, text="ZIP", bg=self.BG, fg=self.ACCENT, font=("Segoe UI", 22, "bold"))
        zip_label.pack(side="left")

        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(header, textvariable=self.status_var, style="Muted.TLabel").pack(side="right")

        self.notebook = ttk.Notebook(self, style="Tab.TNotebook")
        self.notebook.pack(fill="both", expand=True, padx=16, pady=(0, 16))

        self.browse_tab = ttk.Frame(self.notebook, style="TFrame")
        self.library_tab = ttk.Frame(self.notebook, style="TFrame")
        self.downloads_tab = ttk.Frame(self.notebook, style="TFrame")

        self.notebook.add(self.browse_tab, text="  Browse Games  ")
        self.notebook.add(self.library_tab, text="  My Library  ")
        self.notebook.add(self.downloads_tab, text="  Downloads  ")

        self._build_browse_tab()
        self._build_library_tab()
        self._build_downloads_tab()
        self._show_library_empty_hint()

    def _build_browse_tab(self) -> None:
        toolbar = ttk.Frame(self.browse_tab, style="TFrame")
        toolbar.pack(fill="x", padx=12, pady=(12, 8))

        self.search_var = tk.StringVar()
        entry = tk.Entry(
            toolbar,
            textvariable=self.search_var,
            font=("Segoe UI", 11),
            bg="#1f1d1d",
            fg=self.FG,
            insertbackground=self.FG,
            relief="flat",
            highlightthickness=1,
            highlightbackground=self.BORDER,
            highlightcolor=self.ACCENT,
        )
        entry.pack(side="left", fill="x", expand=True, ipady=7, padx=(0, 8))
        entry.bind("<Return>", lambda _e: self.start_search())

        self.search_btn = tk.Button(
            toolbar,
            text="Search",
            command=self.start_search,
            bg=self.ACCENT,
            fg="#0b0a0a",
            relief="flat",
            font=("Segoe UI", 10, "bold"),
            padx=14,
            pady=7,
            cursor="hand2",
        )
        self.search_btn.pack(side="left", padx=(0, 8))

        tk.Button(
            toolbar,
            text="Latest",
            command=lambda: self.load_browse_games(sort="latest"),
            bg="#2a2828",
            fg=self.FG,
            relief="flat",
            font=("Segoe UI", 9),
            padx=10,
            pady=7,
            cursor="hand2",
        ).pack(side="left", padx=(0, 4))

        tk.Button(
            toolbar,
            text="Popular",
            command=lambda: self.load_browse_games(sort="views"),
            bg="#2a2828",
            fg=self.FG,
            relief="flat",
            font=("Segoe UI", 9),
            padx=10,
            pady=7,
            cursor="hand2",
        ).pack(side="left")

        pager = ttk.Frame(self.browse_tab, style="TFrame")
        pager.pack(fill="x", padx=12, pady=(0, 6))
        tk.Button(
            pager,
            text="◀ Prev",
            command=self.prev_browse_page,
            bg="#2a2828",
            fg=self.FG,
            relief="flat",
            padx=10,
            pady=4,
            cursor="hand2",
        ).pack(side="left")
        self.page_label = tk.Label(pager, text="Page 1", bg=self.BG, fg="#9a9a9a", font=("Segoe UI", 9))
        self.page_label.pack(side="left", padx=10)
        tk.Button(
            pager,
            text="Next ▶",
            command=self.next_browse_page,
            bg="#2a2828",
            fg=self.FG,
            relief="flat",
            padx=10,
            pady=4,
            cursor="hand2",
        ).pack(side="left")

        canvas_frame = ttk.Frame(self.browse_tab, style="TFrame")
        canvas_frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        self.browse_canvas = tk.Canvas(canvas_frame, bg=self.BG, highlightthickness=0)
        browse_scroll = ttk.Scrollbar(
            canvas_frame, orient="vertical", command=self._on_browse_scrollbar
        )
        self.browse_inner = tk.Frame(self.browse_canvas, bg=self.BG)
        self.browse_inner.bind("<Configure>", self._on_browse_inner_configure)
        self.browse_canvas.create_window((0, 0), window=self.browse_inner, anchor="nw")
        self.browse_canvas.configure(yscrollcommand=browse_scroll.set)
        self.browse_canvas.pack(side="left", fill="both", expand=True)
        browse_scroll.pack(side="right", fill="y")

        self.browse_canvas.bind("<Configure>", self._on_browse_canvas_resize)
        self.browse_canvas.bind_all("<MouseWheel>", self._on_mousewheel)

    def _build_library_tab(self) -> None:
        top = ttk.Frame(self.library_tab, style="TFrame")
        top.pack(fill="x", padx=12, pady=(12, 6))
        ttk.Label(top, text="Installed Games", style="TLabel", font=("Segoe UI", 12, "bold")).pack(side="left")
        tk.Label(
            top,
            text=f"Games folder: {self.root_dir}",
            bg=self.BG,
            fg="#9a9a9a",
            font=("Segoe UI", 9),
        ).pack(side="right")

        lib_frame = ttk.Frame(self.library_tab, style="TFrame")
        lib_frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        self.library_canvas = tk.Canvas(lib_frame, bg=self.BG, highlightthickness=0)
        lib_scroll = ttk.Scrollbar(
            lib_frame, orient="vertical", command=self._on_library_scrollbar
        )
        self.library_inner = tk.Frame(self.library_canvas, bg=self.BG)
        self.library_inner.bind(
            "<Configure>",
            lambda _e: self.library_canvas.configure(scrollregion=self.library_canvas.bbox("all")),
        )
        self.library_canvas.create_window((0, 0), window=self.library_inner, anchor="nw")
        self.library_canvas.configure(yscrollcommand=lib_scroll.set)
        self.library_canvas.pack(side="left", fill="both", expand=True)
        lib_scroll.pack(side="right", fill="y")
        self.library_canvas.bind("<Configure>", self._on_library_canvas_resize)

    def _build_downloads_tab(self) -> None:
        top = ttk.Frame(self.downloads_tab, style="TFrame")
        top.pack(fill="both", expand=True)

        header = ttk.Frame(top, style="TFrame")
        header.pack(fill="x", padx=16, pady=(14, 6))
        ttk.Label(
            header,
            text="Active Downloads (IDM Style)",
            style="TLabel",
            font=("Segoe UI", 12, "bold"),
        ).pack(side="left")
        tk.Button(
            header,
            text="Clear Logs",
            command=self._clear_logs,
            bg="#2a2828",
            fg=self.FG,
            relief="flat",
            font=("Segoe UI", 8),
            padx=8,
            pady=4,
            cursor="hand2",
        ).pack(side="right")

        canvas_frame = ttk.Frame(top, style="TFrame")
        canvas_frame.pack(fill="both", expand=True, padx=12, pady=(0, 8))

        self.downloads_canvas = tk.Canvas(canvas_frame, bg=self.BG, highlightthickness=0, height=220)
        self.downloads_scroll = ttk.Scrollbar(canvas_frame, orient="vertical", command=self.downloads_canvas.yview)
        self.downloads_inner = ttk.Frame(self.downloads_canvas, style="TFrame")
        self.downloads_inner.bind(
            "<Configure>",
            lambda _e: self.downloads_canvas.configure(scrollregion=self.downloads_canvas.bbox("all")),
        )
        self.downloads_canvas.create_window((0, 0), window=self.downloads_inner, anchor="nw")
        self.downloads_canvas.configure(yscrollcommand=self.downloads_scroll.set)
        self.downloads_canvas.pack(side="left", fill="both", expand=True)
        self.downloads_scroll.pack(side="right", fill="y")

        log_frame = tk.Frame(self.downloads_tab, bg=self.PANEL)
        log_frame.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        tk.Label(
            log_frame,
            text="Download Logs",
            bg=self.PANEL,
            fg=self.ACCENT,
            font=("Segoe UI", 10, "bold"),
            anchor="w",
        ).pack(fill="x", padx=10, pady=(8, 4))

        log_body = tk.Frame(log_frame, bg=self.PANEL)
        log_body.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        self.log_text = tk.Text(
            log_body,
            bg="#111111",
            fg="#c8c8c8",
            insertbackground=self.FG,
            relief="flat",
            font=("Consolas", 9),
            wrap="word",
            height=10,
            state="disabled",
        )
        log_scroll = ttk.Scrollbar(log_body, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")

        for level, color in self.LOG_COLORS.items():
            self.log_text.tag_configure(level, foreground=color)

        self.logger.info(f"PlayZip ready — download folder: {self.root_dir}")

    def _on_browse_inner_configure(self, _event: tk.Event | None = None) -> None:
        if self._scrollregion_job:
            self.after_cancel(self._scrollregion_job)
        self._scrollregion_job = self.after(120, self._update_browse_scrollregion)

    def _update_browse_scrollregion(self) -> None:
        self._scrollregion_job = None
        try:
            bbox = self.browse_canvas.bbox("all")
            if bbox:
                self.browse_canvas.configure(scrollregion=bbox)
        except tk.TclError:
            pass

    def _on_mousewheel(self, event: tk.Event) -> None:
        tab = self.notebook.index(self.notebook.select())
        if tab == 0:
            self.browse_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            self._schedule_visible_scan("browse")
        elif tab == 1:
            self.library_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            self._schedule_visible_scan("library")

    def _on_browse_scrollbar(self, *args) -> None:
        self.browse_canvas.yview(*args)
        self._schedule_visible_scan("browse")

    def _on_library_scrollbar(self, *args) -> None:
        self.library_canvas.yview(*args)
        self._schedule_visible_scan("library")

    def _schedule_visible_scan(self, kind: str) -> None:
        if kind == "browse":
            if self._browse_scroll_job:
                self.after_cancel(self._browse_scroll_job)
            self._browse_scroll_job = self.after(
                90, lambda: self._load_visible_covers("browse")
            )
        else:
            if self._library_scroll_job:
                self.after_cancel(self._library_scroll_job)
            self._library_scroll_job = self.after(
                90, lambda: self._load_visible_covers("library")
            )

    def _load_visible_covers(self, kind: str) -> None:
        """Queue covers only for cards currently in (or near) the viewport."""
        if kind == "browse":
            self._browse_scroll_job = None
            canvas = self.browse_canvas
            cards = self._browse_cards
            games = self._browse_card_games
            mode = self.browse_view
            card_w = self._browse_card_w
        else:
            self._library_scroll_job = None
            canvas = self.library_canvas
            cards = self._library_cards
            games = self.library.entries
            card_w = self._library_card_w

        if not cards:
            return

        try:
            view_h = canvas.winfo_height()
            top = canvas.canvasy(0)
        except tk.TclError:
            return
        if view_h <= 1:
            view_h = 600
        buffer = view_h // 2  # half a screen of pre-load, like browser lazy-load
        view_top = top - buffer
        view_bottom = top + view_h + buffer

        queued = 0
        for index, card in enumerate(cards):
            if index >= len(games):
                break
            try:
                cy = card.winfo_y()
                ch = card.winfo_height() or self.CARD_H
            except tk.TclError:
                continue
            if cy + ch < view_top or cy > view_bottom:
                continue

            game = games[index]
            if kind == "browse":
                key = f"{mode}-{game.game_id}"
                url = game.image_url
            else:
                key = f"library-{game.entry_id}"
                url = game.image_url
            if not url:
                continue
            photo_key = f"{key}@{card_w}"
            if photo_key in self._loaded_cover_keys:
                continue
            self._loaded_cover_keys.add(photo_key)
            self._queue_image_load(url, key, card.set_image, card_w)
            queued += 1

        if queued:
            self.logger.debug(f"Viewport: queued {queued} {kind} cover(s)")
            self._schedule_image_drain()

    def _on_browse_canvas_resize(self, event: tk.Event) -> None:
        if event.width <= 1:
            return
        self.browse_canvas.itemconfig(self.browse_canvas.find_all()[0], width=event.width)
        if not self._ui_ready:
            return
        self._mark_resizing("browse")

    def _on_library_canvas_resize(self, event: tk.Event) -> None:
        if event.width <= 1:
            return
        self.library_canvas.itemconfig(self.library_canvas.find_all()[0], width=event.width)
        if not self._ui_ready or not self._library_loaded:
            return
        self._mark_resizing("library")

    def _mark_resizing(self, which: str) -> None:
        self._resizing = True
        self._pending_resize_target = which
        if self._resize_settle_job:
            self.after_cancel(self._resize_settle_job)
        self._resize_settle_job = self.after(500, self._finish_resize)

    def _finish_resize(self) -> None:
        self._resize_settle_job = None
        self._resizing = False
        self.logger.debug("Resize finished")
        target = self._pending_resize_target
        if self._covers_loading:
            if target == "library":
                self._pending_library_relayout = True
            else:
                self._pending_browse_relayout = True
            return
        if target == "library" and not self._library_resize_ready:
            self._pending_library_relayout = True
            return
        if target == "browse" and not self._browse_resize_ready:
            self._pending_browse_relayout = True
            return
        if target == "library":
            self._schedule_library_relayout()
        else:
            self._schedule_browse_relayout()

    def _schedule_browse_relayout(self) -> None:
        if self._browse_relayout_job:
            self.after_cancel(self._browse_relayout_job)
        self._browse_relayout_job = self.after(200, self._relayout_browse_grid)

    def _schedule_library_relayout(self) -> None:
        if self._library_relayout_job:
            self.after_cancel(self._library_relayout_job)
        self._library_relayout_job = self.after(200, self._relayout_library_grid)

    def _cancel_cover_loading(self) -> None:
        self._image_load_jobs.clear()
        if self._image_drain_job:
            self.after_cancel(self._image_drain_job)
            self._image_drain_job = None
        self.image_queue.cancel_pending()

    def _relayout_browse_grid(self) -> None:
        if self._rendering_browse_grid or not self._ui_ready:
            return
        if self._covers_loading or self.image_queue.busy:
            self._pending_browse_relayout = True
            return
        self._browse_relayout_job = None
        canvas_width = max(self.browse_canvas.winfo_width(), 400)
        new_cols, new_card_w = compute_grid_layout(canvas_width, min_cols=3, max_cols=7)
        if (
            new_cols == self._browse_grid_cols
            and abs(new_card_w - self._browse_card_w) < 6
        ):
            return

        games = (
            self.search_results
            if self.browse_view == "search"
            else self.browse_games
        )
        if not games or not self._browse_cards:
            return

        self.logger.info(
            f"Browse relayout: {self._browse_grid_cols}->{new_cols} cols, "
            f"card {self._browse_card_w}->{new_card_w}px (in-place)"
        )
        self._cover_layout_session += 1
        self._cancel_cover_loading()
        self._loaded_cover_keys.clear()

        parent = self.browse_inner
        self._browse_card_w = new_card_w
        self._browse_grid_cols = new_cols
        for col in range(new_cols):
            parent.grid_columnconfigure(col, weight=1, uniform="browse")

        self._browse_relayout_state = {
            "new_cols": new_cols,
            "new_card_w": new_card_w,
            "index": 0,
        }
        self._relayout_browse_batch()

    def _relayout_browse_batch(self) -> None:
        state = self._browse_relayout_state
        new_cols = state["new_cols"]
        new_card_w = state["new_card_w"]
        start = state["index"]
        chunk = 6
        end = min(start + chunk, len(self._browse_cards))

        for index in range(start, end):
            card = self._browse_cards[index]
            row, col = divmod(index, new_cols)
            card.grid(row=row, column=col, padx=10, pady=10, sticky="n")
            card.resize(new_card_w)

        state["index"] = end
        if end < len(self._browse_cards):
            self.after(12, self._relayout_browse_batch)
        else:
            self._load_visible_covers("browse")

    def _relayout_library_grid(self) -> None:
        if self._rendering_library_grid or not self._library_loaded or not self._ui_ready:
            return
        if self._covers_loading or self.image_queue.busy:
            self._pending_library_relayout = True
            return
        self._library_relayout_job = None
        if not self.library.entries:
            return
        canvas_width = max(self.library_canvas.winfo_width(), 400)
        new_cols, new_card_w = compute_grid_layout(canvas_width, min_cols=2, max_cols=6)
        if (
            new_cols == self._library_grid_cols
            and abs(new_card_w - self._library_card_w) < 6
        ):
            return
        if not self._library_cards:
            return

        self.logger.info(
            f"Library relayout: {self._library_grid_cols}->{new_cols} cols, "
            f"card {self._library_card_w}->{new_card_w}px (in-place)"
        )
        self._cover_layout_session += 1
        self._cancel_cover_loading()
        self._loaded_cover_keys.clear()

        entries = self.library.entries
        parent = self.library_inner
        self._library_card_w = new_card_w
        self._library_grid_cols = new_cols
        for col in range(new_cols):
            parent.grid_columnconfigure(col, weight=1, uniform="library")

        for index, card in enumerate(self._library_cards):
            if index >= len(entries):
                break
            row, col = divmod(index, new_cols)
            card.grid(row=row, column=col, padx=10, pady=10, sticky="n")
            card.resize(new_card_w)

        self._load_visible_covers("library")

    def set_status(self, text: str) -> None:
        self.after(0, lambda: self.status_var.set(text))

    def _append_log(self, level: str, message: str, timestamp: str) -> None:
        def write() -> None:
            self.log_text.configure(state="normal")
            tag = level if level in self.LOG_COLORS else "INFO"
            self.log_text.insert("end", f"[{timestamp}] ", "INFO")
            self.log_text.insert("end", f"[{level}] ", tag)
            self.log_text.insert("end", f"{message}\n", "INFO")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")

        self.after(0, write)

    def _clear_logs(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")
        self.logger.info("Logs cleared.")

    def load_browse_games(self, sort: str | None = None, page: int | None = None) -> None:
        if sort is not None:
            self.browse_sort = sort
            self.browse_page = 1
        if page is not None:
            self.browse_page = page

        self.set_status("Loading games from PlayZip...")
        self.page_label.configure(text=f"Page {self.browse_page} ({self.browse_sort})")

        # Debounce rapid navigation: only the last requested page actually fetches.
        if self._browse_nav_job:
            self.after_cancel(self._browse_nav_job)
        self._browse_nav_job = self.after(180, self._dispatch_browse_fetch)

    def _dispatch_browse_fetch(self) -> None:
        self._browse_nav_job = None
        self._browse_fetch_seq += 1
        seq = self._browse_fetch_seq
        page = self.browse_page
        sort = self.browse_sort

        def worker() -> None:
            try:
                self.logger.info(f"Browse fetch: page={page} sort={sort}")
                games = self.client.browse(page=page, sort=sort)
                self.logger.info(f"Browse fetch OK: {len(games)} games")
                self.after(0, lambda: self._apply_browse_fetch(seq, games))
            except Exception as exc:
                self.logger.error(f"Browse fetch failed: {exc}")
                self.after(0, lambda: messagebox.showerror("PlayZip", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_browse_fetch(self, seq: int, games: list[GameResult]) -> None:
        if seq != self._browse_fetch_seq:
            self.logger.debug(f"Discarding stale browse fetch (seq {seq})")
            return
        self._show_browse_games(games)

    def prev_browse_page(self) -> None:
        if self.browse_page > 1:
            self.load_browse_games(page=self.browse_page - 1)

    def next_browse_page(self) -> None:
        self.load_browse_games(page=self.browse_page + 1)

    def _show_browse_games(self, games: list[GameResult]) -> None:
        self.browse_view = "browse"
        self.search_results = []
        self.browse_games = games
        self._pending_browse_relayout = False
        self._render_game_grid(
            parent=self.browse_inner,
            games=games,
            mode="browse",
            on_click=self.begin_download,
        )
        self.set_status(f"{len(games)} games loaded.")

    def _show_library_empty_hint(self) -> None:
        for widget in self.library_inner.winfo_children():
            widget.destroy()
        if self.library.entries and not self._library_loaded:
            tk.Label(
                self.library_inner,
                text=(
                    f"{len(self.library.entries)} game(s) sa library.\n"
                    "Naka-focus dito ang tab para i-load ang covers."
                ),
                bg=self.BG,
                fg="#9a9a9a",
                font=("Segoe UI", 11),
                justify="center",
            ).pack(pady=80)
            return
        if not self.library.entries:
            tk.Label(
                self.library_inner,
                text="Walang naka-install na games.\nMag-download mula sa Browse tab.",
                bg=self.BG,
                fg="#9a9a9a",
                font=("Segoe UI", 11),
                justify="center",
            ).pack(pady=80)

    def refresh_library_grid(self) -> None:
        for widget in self.library_inner.winfo_children():
            widget.destroy()

        if not self.library.entries:
            self._show_library_empty_hint()
            return

        self._ui_ready = False
        self._rendering_library_grid = True
        self._render_library_cards_batched()

    def _render_library_cards(self) -> None:
        self._ui_ready = False
        self._rendering_library_grid = True
        self._render_library_cards_batched()

    def _render_library_cards_batched(self, start: int = 0) -> None:
        entries = self.library.entries
        if start == 0:
            for widget in self.library_inner.winfo_children():
                widget.destroy()
            self._library_cards = []
            self._loaded_cover_keys.clear()
            self._cover_layout_session += 1
            self._cancel_cover_loading()
            canvas_width = max(self.library_canvas.winfo_width(), 400)
            cols, card_w = compute_grid_layout(canvas_width, min_cols=2, max_cols=6)
            self._library_card_w = card_w
            self._library_grid_cols = cols
            self._library_resize_ready = False
            for col in range(cols):
                self.library_inner.grid_columnconfigure(col, weight=1, uniform="library")
            self._library_render_token = self._grid_render_token = (
                self._grid_render_token + 1
            )

        token = self._library_render_token
        cols = self._library_grid_cols
        card_w = self._library_card_w
        chunk = 3
        end = min(start + chunk, len(entries))

        for index in range(start, end):
            entry = entries[index]
            row, col = divmod(index, cols)
            card = LibraryCard(
                self.library_inner,
                title=entry.title,
                exe_name=os.path.basename(entry.exe_path) if entry.exe_path else "",
                width=card_w,
                on_play=lambda e=entry: self.launch_game(e),
                on_change_exe=lambda e=entry: self.change_exe(e),
                on_delete=lambda e=entry: self.delete_game(e),
            )
            card.grid(row=row, column=col, padx=10, pady=10, sticky="n")
            self._library_cards.append(card)

        self.update_idletasks()
        if end < len(entries):
            self.after(8, lambda: self._render_library_cards_batched(end))
        else:
            self._rendering_library_grid = False
            self._ui_ready = True
            self.logger.info(f"Library grid rendered ({len(entries)} cards)")
            self.library_canvas.yview_moveto(0.0)
            self.after(30, lambda: self._load_visible_covers("library"))

    def _render_game_grid(
        self,
        parent: tk.Misc,
        games: list[GameResult],
        mode: str,
        on_click: callable,
    ) -> None:
        self._cover_layout_session += 1
        self._cancel_cover_loading()
        self._browse_card_games = list(games)
        self._loaded_cover_keys.clear()
        self._browse_resize_ready = False

        self._ui_ready = False
        self._rendering_browse_grid = True
        self._grid_render_token += 1
        token = self._grid_render_token

        canvas_width = max(self.browse_canvas.winfo_width(), 400)
        cols, card_w = compute_grid_layout(canvas_width, min_cols=3, max_cols=7)
        self._browse_card_w = card_w
        self._browse_grid_cols = cols
        for col in range(cols):
            parent.grid_columnconfigure(col, weight=1, uniform="browse")

        # Reuse existing cards (like browser DOM reuse) to avoid widget churn.
        surplus = self._browse_cards[len(games):]
        for card in surplus:
            card.destroy()
        del self._browse_cards[len(games):]

        self._game_grid_state = {
            "token": token,
            "parent": parent,
            "games": games,
            "mode": mode,
            "on_click": on_click,
            "index": 0,
            "cols": cols,
            "card_w": card_w,
        }
        self.logger.info(f"Rendering browse grid: {len(games)} games, {cols} cols")
        self._render_game_grid_batch()

    def _render_game_grid_batch(self) -> None:
        state = self._game_grid_state
        token = state["token"]
        if token != self._grid_render_token:
            return

        games: list[GameResult] = state["games"]
        start: int = state["index"]
        chunk = 8
        end = min(start + chunk, len(games))
        parent = state["parent"]
        mode = state["mode"]
        on_click = state["on_click"]
        cols = state["cols"]
        card_w = state["card_w"]

        for index in range(start, end):
            game = games[index]
            row, col = divmod(index, cols)
            if index < len(self._browse_cards):
                card = self._browse_cards[index]
                card.set_title(game.title)
                card.set_on_click(lambda g=game: on_click(g))
                card.resize(card_w)
                card.clear_image()
            else:
                card = GameCoverCard(
                    parent,
                    title=game.title,
                    width=card_w,
                    on_click=lambda g=game: on_click(g),
                    show_download_hint=True,
                )
                self._browse_cards.append(card)
            card.grid(row=row, column=col, padx=10, pady=10, sticky="n")

        state["index"] = end
        self.update_idletasks()
        if end < len(games):
            self.after(8, self._render_game_grid_batch)
        else:
            self._rendering_browse_grid = False
            self._ui_ready = True
            self.logger.info(f"Browse grid rendered ({len(games)} cards)")
            self.browse_canvas.yview_moveto(0.0)
            self.after(30, lambda: self._load_visible_covers("browse"))

    def _queue_image_load(
        self,
        url: str,
        key: str,
        apply_photo: callable,
        card_w: int,
    ) -> None:
        if not url:
            return
        self._image_load_jobs.append((url, key, apply_photo, card_w))

    def _schedule_image_drain(self) -> None:
        if self._image_drain_job:
            return
        pending = len(self._image_load_jobs)
        if pending:
            self.logger.info(f"Queueing cover downloads: {pending}")
        self._covers_loading = True
        if pending:
            self._image_drain_job = self.after(1, self._drain_image_jobs)
        else:
            self.after(50, self._check_covers_idle)

    def _drain_image_jobs(self) -> None:
        self._image_drain_job = None
        batch = 1
        for _ in range(min(batch, len(self._image_load_jobs))):
            url, key, apply_photo, card_w = self._image_load_jobs.popleft()
            self._load_image_async(url, key, apply_photo, card_w=card_w)
        if self._image_load_jobs:
            self._image_drain_job = self.after(80, self._drain_image_jobs)
        else:
            self.after(50, self._check_covers_idle)

    def _check_covers_idle(self) -> None:
        pending_jobs = len(self._image_load_jobs)
        if pending_jobs:
            if not self._image_drain_job:
                self._image_drain_job = self.after(80, self._drain_image_jobs)
            return
        if self.image_queue.busy:
            self.after(100, self._check_covers_idle)
            return
        self._covers_loading = False
        self.logger.info("Cover loading finished")
        self._browse_resize_ready = True
        self._library_resize_ready = True
        if self._pending_browse_relayout:
            self._pending_browse_relayout = False
            self.logger.info("Running deferred browse relayout")
            self._schedule_browse_relayout()
        if self._pending_library_relayout:
            self._pending_library_relayout = False
            self.logger.info("Running deferred library relayout")
            self._schedule_library_relayout()

    def _url_cache_path(self, url: str) -> str:
        norm = normalize_image_url(url)
        digest = hashlib.md5(norm.encode(), usedforsecurity=False).hexdigest()
        return os.path.join(self.cache_dir, f"{digest}.jpg")

    def _rendered_cache_path(self, url: str, width: int) -> str:
        norm = normalize_image_url(url)
        digest = hashlib.md5(norm.encode(), usedforsecurity=False).hexdigest()
        card_w, _card_h = capsule_size(width)
        return os.path.join(self.rendered_cache_dir, f"{digest}_{card_w}.jpg")

    def _cover_url_lock(self, url: str) -> threading.Lock:
        norm = normalize_image_url(url)
        with self._cover_lock_guard:
            lock = self._cover_url_locks.get(norm)
            if lock is None:
                lock = threading.Lock()
                self._cover_url_locks[norm] = lock
            return lock

    def _load_rendered_from_disk(
        self, url: str, width: int
    ) -> tuple[bytes, int, int] | None:
        path = self._rendered_cache_path(url, width)
        if not os.path.exists(path):
            return None
        try:
            from PIL import Image

            with Image.open(path) as image:
                rgb = image.convert("RGB")
                w, h = rgb.size
                return rgb.tobytes(), w, h
        except OSError:
            return None

    def _save_rendered_to_disk(
        self, url: str, width: int, rgb_bytes: bytes, img_w: int, img_h: int
    ) -> None:
        path = self._rendered_cache_path(url, width)
        try:
            from PIL import Image

            image = Image.frombytes("RGB", (img_w, img_h), rgb_bytes)
            image.save(path, "JPEG", quality=85)
        except OSError:
            pass

    def _load_raw_cover_bytes(self, url: str) -> bytes:
        norm = normalize_image_url(url)
        if norm in self._image_bytes:
            return self._image_bytes[norm]

        cache_path = self._url_cache_path(norm)
        if os.path.exists(cache_path):
            with open(cache_path, "rb") as handle:
                data = handle.read()
            self._image_bytes[norm] = data
            self.logger.debug(f"Cover from disk cache: {norm[:80]}...")
            return data

        with self._cover_url_lock(norm):
            if norm in self._image_bytes:
                return self._image_bytes[norm]
            if os.path.exists(cache_path):
                with open(cache_path, "rb") as handle:
                    data = handle.read()
                self._image_bytes[norm] = data
                self.logger.debug(f"Cover from disk cache: {norm[:80]}...")
                return data

            self.logger.debug(f"Downloading cover: {norm[:80]}...")
            data = self.client.fetch_image_bytes(norm)
            self._image_bytes[norm] = data
            with open(cache_path, "wb") as handle:
                handle.write(data)
            return data

    def _load_image_async(
        self,
        url: str,
        key: str,
        apply_photo: callable,
        card_w: int | None = None,
    ) -> None:
        width = card_w or self._browse_card_w
        photo_key = f"{key}@{width}"
        session = self._cover_layout_session

        def store_and_apply(photo) -> None:
            if session != self._cover_layout_session:
                return
            self._photo_refs[photo_key] = photo
            try:
                apply_photo(photo)
            except tk.TclError as exc:
                self.logger.debug(f"Skipped stale cover apply ({key}): {exc}")

        if not url:
            return

        def work():
            norm = normalize_image_url(url)
            cache_key = (norm, width)
            if cache_key in self._rendered_cache:
                self.logger.debug(f"Cover from memory cache: {norm[:80]}...")
                return self._rendered_cache[cache_key]

            rendered = self._load_rendered_from_disk(norm, width)
            if rendered:
                self.logger.debug(f"Cover from render cache: {norm[:80]}...")
                self._rendered_cache[cache_key] = rendered
                return rendered

            data = self._load_raw_cover_bytes(norm)
            rendered = fetch_and_render_cover(lambda: data, width, cached_bytes=data)
            if rendered:
                rgb_bytes, img_w, img_h = rendered
                self._rendered_cache[cache_key] = rendered
                self._save_rendered_to_disk(norm, width, rgb_bytes, img_w, img_h)
            return rendered

        self.image_queue.submit(work, store_and_apply)

    def start_search(self) -> None:
        query = self.search_var.get().strip()
        if not query:
            self.load_browse_games()
            return

        self.search_btn.configure(state="disabled")
        self.set_status(f"Searching '{query}'...")

        def worker() -> None:
            try:
                results = self.client.search(query)
                self.after(0, lambda: self._handle_search_results(query, results))
            except Exception as exc:
                self.after(0, lambda: self._handle_search_error(exc))

        threading.Thread(target=worker, daemon=True).start()

    def _handle_search_error(self, exc: Exception) -> None:
        self.search_btn.configure(state="normal")
        self.set_status(f"Search failed: {exc}")
        messagebox.showerror("PlayZip", str(exc))

    def _handle_search_results(self, query: str, results: list[GameResult]) -> None:
        self.search_btn.configure(state="normal")
        self.search_results = results
        self.browse_view = "search"

        if not results:
            self.set_status(f"Walang nahanap para sa '{query}'.")
            messagebox.showinfo("PlayZip", f"Walang resulta para sa '{query}'.")
            return

        self._pending_browse_relayout = False
        self._render_game_grid(
            parent=self.browse_inner,
            games=results,
            mode="search",
            on_click=self.begin_download,
        )
        self.set_status(f"{len(results)} result(s) for '{query}'.")

    def begin_download(self, game: GameResult) -> None:
        self.notebook.select(self.downloads_tab)
        self.set_status(f"Getting download link for {game.title}...")
        self.logger.info(f"Download requested: {game.title} (id={game.game_id})")

        def worker() -> None:
            try:
                url = self.client.get_download_url(game.game_id, title=game.title)
                filename = self.client.filename_from_url(url, fallback=f"{game.title}.bin")
                dest = os.path.join(self.root_dir, filename)
                self.logger.info(f"[{game.title}] Saving as {filename}")
                self.after(0, lambda: self._queue_download(game, url, dest))
            except Exception as exc:
                self.logger.error(f"[{game.title}] Failed to get download link: {exc}")
                self.after(0, lambda: self._handle_download_link_error(game.title, exc))

        threading.Thread(target=worker, daemon=True).start()

    def _handle_download_link_error(self, title: str, exc: Exception) -> None:
        self.set_status(f"Failed: {title} — {exc}")
        messagebox.showerror("PlayZip", str(exc))

    def _queue_download(self, game: GameResult, url: str, dest_path: str) -> None:
        task_id = dest_path
        active = {
            DownloadState.QUEUED,
            DownloadState.CONNECTING,
            DownloadState.DOWNLOADING,
            DownloadState.PAUSED,
        }
        if task_id in self.tasks and self.tasks[task_id].state in active:
            self.set_status("May active download na para sa file na ito.")
            return

        task = DownloadTask(url=url, dest_path=dest_path, connections=8)
        self.tasks[task_id] = task
        self.task_meta[task_id] = game
        self._create_task_row(task_id, game.title)
        self.logger.info(f"[{game.title}] Download started → {dest_path}")
        self.set_status(f"Downloading: {game.title}")

        def on_progress(downloaded: int, total: int, speed: float, state: str) -> None:
            self.after(0, lambda: self._update_task_row(task_id, downloaded, total, speed, state))
            if state == DownloadState.COMPLETED.value:
                self.logger.info(f"[{game.title}] Download finished")
                self.after(0, lambda: self._on_download_complete(task_id))

        def on_status(message: str) -> None:
            self.logger.info(f"[{game.title}] {message}")
            self.after(0, lambda: self.set_status(message))

        downloader = IDMDownloader(on_progress=on_progress, on_status=on_status)
        downloader.start(task)
        task._downloader = downloader

    def _on_download_complete(self, task_id: str) -> None:
        task = self.tasks.get(task_id)
        game = self.task_meta.get(task_id)
        if not task or not game:
            return

        archive_path = task.dest_path
        install_dir = os.path.join(
            self.root_dir,
            LibraryManager.safe_folder_name(game.title),
        )

        self.set_status(f"Extracting {game.title}...")
        self.logger.info(f"[{game.title}] Starting extraction → {install_dir}")
        self._set_task_phase(task_id, "extract")
        self._update_extract_row(task_id, 0, 100, "Preparing extraction...")

        def on_extract_progress(current: int, total: int, message: str) -> None:
            self.logger.info(f"[{game.title}] {message}")
            self.after(0, lambda: self._update_extract_row(task_id, current, total, message))

        def worker() -> None:
            try:
                extract_archive(archive_path, install_dir, on_progress=on_extract_progress)
                if os.path.exists(archive_path):
                    os.remove(archive_path)
                    self.logger.info(f"[{game.title}] Removed archive {os.path.basename(archive_path)}")
                exes = scan_executables(install_dir)
                self.logger.info(f"[{game.title}] Found {len(exes)} EXE file(s)")
                self.after(0, lambda: self._post_extract(task_id, game, install_dir, exes))
            except Exception as exc:
                self.logger.error(f"[{game.title}] Extract failed: {exc}")
                self.after(0, lambda: self._handle_extract_error(task_id, game.title, exc))

        threading.Thread(target=worker, daemon=True).start()

    def _handle_extract_error(self, task_id: str, title: str, exc: Exception) -> None:
        self._update_extract_row(task_id, 0, 100, f"Error: {exc}")
        self.set_status(f"Extract failed: {title}")
        messagebox.showerror("Extract Error", str(exc))

    def _post_extract(
        self,
        task_id: str,
        game: GameResult,
        install_dir: str,
        exes: list[str],
    ) -> None:
        self._update_extract_row(task_id, 100, 100, "Extraction complete")
        self._set_task_phase(task_id, "done")
        if not exes:
            messagebox.showwarning(
                "PlayZip",
                f"Na-extract ang {game.title} pero walang nahanap na EXE.",
            )
            entry = LibraryEntry.create(
                title=game.title,
                game_id=game.game_id,
                install_dir=install_dir,
                image_url=game.image_url,
            )
            self.library.add(entry)
            self.refresh_library_grid()
            self.notebook.select(self.library_tab)
            return

        default_exe = pick_best_exe(exes)

        def on_pick(exe_path: str) -> None:
            entry = LibraryEntry.create(
                title=game.title,
                game_id=game.game_id,
                install_dir=install_dir,
                image_url=game.image_url,
                exe_path=exe_path,
            )
            self.library.add(entry)
            self.refresh_library_grid()
            self.notebook.select(self.library_tab)
            self.set_status(f"{game.title} added to library.")

        ExePickerDialog(
            self,
            title=game.title,
            install_dir=install_dir,
            exe_paths=exes,
            current_exe=default_exe,
            on_confirm=on_pick,
            rescan_callback=lambda: scan_executables(install_dir),
        )

    def change_exe(self, entry: LibraryEntry) -> None:
        if not os.path.isdir(entry.install_dir):
            messagebox.showerror("PlayZip", "Nawawala ang install folder.")
            return
        exes = scan_executables(entry.install_dir)
        if not exes:
            messagebox.showwarning("PlayZip", "Walang EXE na nahanap sa folder.")
            return

        def on_pick(exe_path: str) -> None:
            self.library.update_exe(entry.entry_id, exe_path)
            self.refresh_library_grid()
            self.set_status(f"PLAY button updated: {os.path.basename(exe_path)}")

        ExePickerDialog(
            self,
            title=entry.title,
            install_dir=entry.install_dir,
            exe_paths=exes,
            current_exe=entry.exe_path,
            on_confirm=on_pick,
            rescan_callback=lambda: scan_executables(entry.install_dir),
        )

    def launch_game(self, entry: LibraryEntry) -> None:
        if not entry.exe_path or not os.path.isfile(entry.exe_path):
            if messagebox.askyesno(
                "PlayZip",
                "Walang naka-set na EXE. Pumili ngayon?",
            ):
                self.change_exe(entry)
            return

        workdir = os.path.dirname(entry.exe_path)
        try:
            subprocess.Popen(
                [entry.exe_path],
                cwd=workdir,
                shell=False,
            )
            self.set_status(f"Launching {entry.title}...")
        except Exception as exc:
            messagebox.showerror("PlayZip", f"Hindi ma-launch: {exc}")

    def delete_game(self, entry: LibraryEntry) -> None:
        if not messagebox.askyesno(
            "Delete Game",
            f"Tanggalin ang '{entry.title}'?\n\nMade-delete ang buong folder:\n{entry.install_dir}",
        ):
            return

        removed = self.library.remove(entry.entry_id)
        if removed and os.path.isdir(removed.install_dir):
            try:
                shutil.rmtree(removed.install_dir)
            except Exception as exc:
                messagebox.showwarning("PlayZip", f"Na-remove sa library pero hindi nabura ang folder: {exc}")

        self.refresh_library_grid()
        self.set_status(f"Deleted: {entry.title}")

    def _create_task_row(self, task_id: str, title: str) -> None:
        frame = tk.Frame(self.downloads_inner, bg=self.PANEL, padx=12, pady=10)
        frame.pack(fill="x", pady=6)

        top = tk.Frame(frame, bg=self.PANEL)
        top.pack(fill="x")
        tk.Label(top, text=title, bg=self.PANEL, fg=self.FG, font=("Segoe UI", 10, "bold"), anchor="w").pack(
            side="left", fill="x", expand=True
        )

        btn_frame = tk.Frame(top, bg=self.PANEL)
        btn_frame.pack(side="right")

        pause_btn = tk.Button(
            btn_frame,
            text="Pause",
            command=lambda: self._toggle_pause(task_id),
            bg="#2a2828",
            fg=self.FG,
            relief="flat",
            font=("Segoe UI", 8),
            padx=8,
            pady=4,
            cursor="hand2",
        )
        pause_btn.pack(side="left", padx=(0, 4))

        cancel_btn = tk.Button(
            btn_frame,
            text="Cancel",
            command=lambda: self._cancel_task(task_id),
            bg="#402020",
            fg="#ffb3b3",
            relief="flat",
            font=("Segoe UI", 8),
            padx=8,
            pady=4,
            cursor="hand2",
        )
        cancel_btn.pack(side="left")

        progress = ttk.Progressbar(frame, mode="determinate", maximum=100)
        progress.pack(fill="x", pady=(8, 4))

        info = tk.Label(
            frame,
            text="0% | 0 B / 0 B | 0 B/s | ETA --:-- | Queued",
            bg=self.PANEL,
            fg="#9a9a9a",
            font=("Segoe UI", 9),
            anchor="w",
        )
        info.pack(fill="x")

        extract_label = tk.Label(
            frame,
            text="Extract: waiting...",
            bg=self.PANEL,
            fg="#7aa2d6",
            font=("Segoe UI", 8, "bold"),
            anchor="w",
        )
        extract_label.pack(fill="x", pady=(6, 2))

        extract_progress = ttk.Progressbar(frame, mode="determinate", maximum=100)
        extract_progress.pack(fill="x", pady=(0, 4))

        extract_info = tk.Label(
            frame,
            text="",
            bg=self.PANEL,
            fg="#9a9a9a",
            font=("Segoe UI", 8),
            anchor="w",
        )
        extract_info.pack(fill="x")

        self.task_rows[task_id] = {
            "frame": frame,
            "progress": progress,
            "info": info,
            "pause_btn": pause_btn,
            "extract_label": extract_label,
            "extract_progress": extract_progress,
            "extract_info": extract_info,
            "phase": "download",
        }

    def _set_task_phase(self, task_id: str, phase: str) -> None:
        row = self.task_rows.get(task_id)
        if row:
            row["phase"] = phase

    def _update_extract_row(
        self,
        task_id: str,
        current: int,
        total: int,
        message: str,
    ) -> None:
        row = self.task_rows.get(task_id)
        if not row:
            return

        percent = min(100.0, (current / total) * 100) if total > 0 else 0.0
        row["extract_label"].configure(text="Extract: in progress...")
        row["extract_progress"]["value"] = percent
        row["extract_info"].configure(text=f"{percent:5.1f}% | {message}")
        if percent >= 100:
            row["extract_label"].configure(text="Extract: complete")

    def _update_task_row(
        self,
        task_id: str,
        downloaded: int,
        total: int,
        speed: float,
        state: str,
    ) -> None:
        row = self.task_rows.get(task_id)
        if not row:
            return

        percent = min(100.0, (downloaded / total) * 100) if total > 0 else 0.0
        row["progress"]["value"] = percent
        row["info"].configure(
            text=(
                f"{percent:5.1f}% | {format_bytes(downloaded)} / {format_bytes(total)} | "
                f"{format_speed(speed)} | ETA {format_eta(downloaded, total, speed)} | {state}"
            )
        )

        if state in {DownloadState.COMPLETED.value, DownloadState.ERROR.value, DownloadState.CANCELLED.value}:
            row["pause_btn"].configure(state="disabled")

    def _toggle_pause(self, task_id: str) -> None:
        task = self.tasks.get(task_id)
        row = self.task_rows.get(task_id)
        if not task or not row:
            return
        downloader: IDMDownloader | None = getattr(task, "_downloader", None)
        if not downloader:
            return

        if task.state == DownloadState.PAUSED:
            downloader.resume(task)
            row["pause_btn"].configure(text="Pause")
        elif task.state == DownloadState.DOWNLOADING:
            downloader.pause(task)
            row["pause_btn"].configure(text="Resume")

    def _cancel_task(self, task_id: str) -> None:
        task = self.tasks.get(task_id)
        if not task:
            return
        downloader: IDMDownloader | None = getattr(task, "_downloader", None)
        if downloader:
            downloader.cancel(task)


def main() -> None:
    app = PlayZipDownloaderApp()
    app.mainloop()


if __name__ == "__main__":
    main()
