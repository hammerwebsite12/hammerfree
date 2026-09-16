"""Reusable UI widgets for PlayZip app."""

from __future__ import annotations

import io
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable

try:
    from PIL import Image, ImageDraw, ImageFont, ImageTk
except ImportError:
    Image = None  # type: ignore
    ImageDraw = None  # type: ignore
    ImageFont = None  # type: ignore
    ImageTk = None  # type: ignore

# PlayZip portrait capsule: padding-top 145% on site => height = width * 1.45
CAPSULE_RATIO = 1.45
CAPSULE_RADIUS = 6
MIN_CARD_W = 132
MAX_CARD_W = 220
GRID_GAP = 28
GRID_PAD = 16


def capsule_size(width: int) -> tuple[int, int]:
    width = max(MIN_CARD_W, min(MAX_CARD_W, width))
    return width, max(1, int(round(width * CAPSULE_RATIO)))


def compute_grid_layout(
    canvas_width: int,
    *,
    min_cols: int = 3,
    max_cols: int = 8,
) -> tuple[int, int]:
    """Return (columns, card_width) that fits the available canvas width."""
    usable = max(canvas_width - GRID_PAD * 2, MIN_CARD_W + GRID_GAP)
    for cols in range(max_cols, min_cols - 1, -1):
        card_w = (usable - GRID_GAP * (cols - 1)) // cols
        if card_w >= MIN_CARD_W:
            return cols, min(card_w, MAX_CARD_W)
    return min_cols, MIN_CARD_W


def title_font_size(card_w: int) -> int:
    return max(8, min(11, int(card_w * 0.054)))


class ExePickerDialog(tk.Toplevel):
    BG = "#161414"
    FG = "#e0e0e0"
    ACCENT = "#ffb347"

    def __init__(
        self,
        master: tk.Misc,
        title: str,
        install_dir: str,
        exe_paths: list[str],
        current_exe: str = "",
        on_confirm: Callable[[str], None] | None = None,
        rescan_callback: Callable[[], list[str]] | None = None,
    ) -> None:
        super().__init__(master)
        self.title("Piliin ang EXE")
        self.configure(bg=self.BG)
        self.resizable(True, True)
        self.transient(master)
        self.grab_set()
        self.on_confirm = on_confirm
        self.rescan_callback = rescan_callback
        self.install_dir = install_dir
        self.selected_exe = current_exe or (exe_paths[0] if exe_paths else "")

        self.geometry("680x460")
        self.minsize(540, 380)

        tk.Label(
            self,
            text=f"Nahanap na EXE files — {title}",
            bg=self.BG,
            fg=self.ACCENT,
            font=("Segoe UI", 13, "bold"),
            wraplength=620,
            justify="left",
        ).pack(anchor="w", padx=20, pady=(18, 4))

        tk.Label(
            self,
            text="Piliin kung alin ang magiging PLAY button sa Library Manager.",
            bg=self.BG,
            fg="#9a9a9a",
            font=("Segoe UI", 9),
        ).pack(anchor="w", padx=20, pady=(0, 12))

        list_frame = tk.Frame(self, bg=self.BG)
        list_frame.pack(fill="both", expand=True, padx=20, pady=(0, 12))

        scrollbar = ttk.Scrollbar(list_frame)
        scrollbar.pack(side="right", fill="y")

        self.listbox = tk.Listbox(
            list_frame,
            bg="#1f1d1d",
            fg=self.FG,
            selectbackground=self.ACCENT,
            selectforeground="#0b0a0a",
            font=("Consolas", 10),
            relief="flat",
            highlightthickness=1,
            highlightbackground="#333",
            yscrollcommand=scrollbar.set,
        )
        self.listbox.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=self.listbox.yview)
        self.listbox.bind("<Double-Button-1>", lambda _e: self._confirm())

        self._exe_map: list[str] = []
        self._populate_list(exe_paths)

        btn_row = tk.Frame(self, bg=self.BG)
        btn_row.pack(fill="x", padx=20, pady=(0, 18))

        if rescan_callback:
            tk.Button(
                btn_row,
                text="Rescan EXE",
                command=self._rescan,
                bg="#2a2828",
                fg=self.FG,
                relief="flat",
                font=("Segoe UI", 9),
                padx=12,
                pady=8,
                cursor="hand2",
            ).pack(side="left")

        tk.Button(
            btn_row,
            text="Cancel",
            command=self.destroy,
            bg="#2a2828",
            fg=self.FG,
            relief="flat",
            font=("Segoe UI", 10),
            padx=16,
            pady=8,
            cursor="hand2",
        ).pack(side="right")

        tk.Button(
            btn_row,
            text="Set as PLAY",
            command=self._confirm,
            bg=self.ACCENT,
            fg="#0b0a0a",
            relief="flat",
            font=("Segoe UI", 10, "bold"),
            padx=16,
            pady=8,
            cursor="hand2",
        ).pack(side="right", padx=(0, 8))

    def _populate_list(self, exe_paths: list[str]) -> None:
        self.listbox.delete(0, tk.END)
        self._exe_map = list(exe_paths)
        for exe in exe_paths:
            rel = exe
            if self.install_dir and exe.startswith(self.install_dir):
                rel = exe[len(self.install_dir) :].lstrip("\\/")
            self.listbox.insert(tk.END, rel)

        if self.selected_exe in self._exe_map:
            index = self._exe_map.index(self.selected_exe)
            self.listbox.selection_set(index)
            self.listbox.see(index)
        elif self._exe_map:
            self.listbox.selection_set(0)

    def _rescan(self) -> None:
        if not self.rescan_callback:
            return
        exes = self.rescan_callback()
        if not exes:
            messagebox.showwarning("PlayZip", "Walang EXE na nahanap.", parent=self)
            return
        self._populate_list(exes)

    def _confirm(self) -> None:
        selection = self.listbox.curselection()
        if not selection:
            return
        self.selected_exe = self._exe_map[selection[0]]
        if self.on_confirm:
            self.on_confirm(self.selected_exe)
        self.destroy()


class CapsuleCover(tk.Frame):
    """PlayZip-style portrait capsule — full-bleed cover with title overlay."""

    BG = "#0b0a0a"
    FG = "#e0e0e0"
    ACCENT = "#ffb347"

    def __init__(
        self,
        master: tk.Misc,
        title: str,
        width: int = 168,
        on_click: Callable[[], None] | None = None,
        show_download_hint: bool = False,
        **kwargs,
    ) -> None:
        card_w, card_h = capsule_size(width)
        super().__init__(master, bg=self.BG, cursor="hand2" if on_click else "", **kwargs)
        self.title_text = title
        self.card_w = card_w
        self.card_h = card_h
        self.on_click = on_click
        self._photo: tk.PhotoImage | None = None

        self.outer = tk.Frame(
            self,
            bg="#222",
            highlightthickness=1,
            highlightbackground="#2a2a2a",
        )
        self.outer.pack()
        self.outer.configure(width=card_w, height=card_h)
        self.outer.pack_propagate(False)

        self.canvas = tk.Canvas(
            self.outer,
            width=card_w,
            height=card_h,
            bg="#333333",
            highlightthickness=0,
            bd=0,
            cursor="hand2" if on_click else "",
        )
        self.canvas.pack()

        self._image_id = self.canvas.create_image(0, 0, anchor="nw")
        self._draw_title()

        if show_download_hint:
            self.hint = tk.Label(
                self.outer,
                text="Click to Download",
                bg=self.ACCENT,
                fg="#0b0a0a",
                font=("Segoe UI", max(7, title_font_size(card_w) - 1), "bold"),
                padx=6,
                pady=2,
            )
            self.hint.place(x=6, y=6)

        widgets: list[tk.Misc] = [self, self.outer, self.canvas]
        if show_download_hint:
            widgets.append(self.hint)
        for widget in widgets:
            if on_click:
                widget.bind("<Button-1>", self._handle_click)
            widget.bind("<Enter>", self._on_enter)
            widget.bind("<Leave>", self._on_leave)

    def _draw_title(self) -> None:
        self.canvas.delete("title")
        font = ("Segoe UI", title_font_size(self.card_w), "bold")
        x = 8
        y = self.card_h - 10
        wrap = self.card_w - 16
        self.canvas.create_text(
            x + 1,
            y + 1,
            text=self.title_text,
            anchor="sw",
            fill="#000000",
            font=font,
            width=wrap,
            justify="left",
            tags="title",
        )
        self.canvas.create_text(
            x,
            y,
            text=self.title_text,
            anchor="sw",
            fill=self.FG,
            font=font,
            width=wrap,
            justify="left",
            tags="title",
        )

    def _handle_click(self, _event: tk.Event) -> None:
        if self.on_click:
            self.on_click()

    def _on_enter(self, _event: tk.Event) -> None:
        self.outer.configure(highlightbackground=self.ACCENT)

    def _on_leave(self, _event: tk.Event) -> None:
        self.outer.configure(highlightbackground="#2a2a2a")

    def set_image(self, photo: tk.PhotoImage) -> None:
        self._photo = photo
        self.canvas.itemconfig(self._image_id, image=photo)

    def clear_image(self) -> None:
        """Reset to the skeleton/placeholder state (no cover)."""
        self._photo = None
        try:
            self.canvas.itemconfig(self._image_id, image="")
        except tk.TclError:
            pass

    def set_title(self, title: str) -> None:
        if title == self.title_text:
            return
        self.title_text = title
        self._draw_title()

    def set_on_click(self, callback: Callable[[], None] | None) -> None:
        self.on_click = callback

    def resize(self, width: int) -> None:
        """Update capsule dimensions without destroying the widget."""
        card_w, card_h = capsule_size(width)
        if card_w == self.card_w and card_h == self.card_h:
            return
        self.card_w = card_w
        self.card_h = card_h
        self.outer.configure(width=card_w, height=card_h)
        self.canvas.configure(width=card_w, height=card_h)
        self._draw_title()
        if self._photo is not None:
            self.canvas.itemconfig(self._image_id, image="")
            self._photo = None


class GameCoverCard(CapsuleCover):
    """Browse grid card."""

    def __init__(
        self,
        master: tk.Misc,
        title: str,
        width: int = 168,
        on_click: Callable[[], None] | None = None,
        show_download_hint: bool = False,
        **kwargs,
    ) -> None:
        super().__init__(
            master,
            title=title,
            width=width,
            on_click=on_click,
            show_download_hint=show_download_hint,
            **kwargs,
        )


class LibraryCard(tk.Frame):
    """Library card with PlayZip capsule cover and action buttons."""

    PANEL = "#161414"
    FG = "#e0e0e0"
    ACCENT = "#ffb347"

    def __init__(
        self,
        master: tk.Misc,
        title: str,
        exe_name: str,
        width: int = 168,
        on_play: Callable[[], None] | None = None,
        on_change_exe: Callable[[], None] | None = None,
        on_delete: Callable[[], None] | None = None,
        **kwargs,
    ) -> None:
        card_w, _card_h = capsule_size(width)
        super().__init__(master, bg=self.PANEL, padx=4, pady=4, **kwargs)

        self.capsule = CapsuleCover(self, title=title, width=width, on_click=on_play)
        self.capsule.pack()
        self.cover = self.capsule

        tk.Label(
            self,
            text=exe_name or "No EXE set",
            bg=self.PANEL,
            fg="#9a9a9a",
            font=("Segoe UI", max(7, title_font_size(card_w) - 1)),
            wraplength=card_w,
        ).pack(pady=(6, 0))

        btn_row = tk.Frame(self, bg=self.PANEL)
        btn_row.pack(fill="x", pady=(8, 0))

        tk.Button(
            btn_row,
            text="▶ PLAY",
            command=on_play,
            bg=self.ACCENT,
            fg="#0b0a0a",
            relief="flat",
            font=("Segoe UI", 9, "bold"),
            padx=10,
            pady=5,
            cursor="hand2",
        ).pack(side="left", padx=(0, 4))

        tk.Button(
            btn_row,
            text="EXE",
            command=on_change_exe,
            bg="#2a2828",
            fg=self.FG,
            relief="flat",
            font=("Segoe UI", 8),
            padx=8,
            pady=5,
            cursor="hand2",
        ).pack(side="left", padx=(0, 4))

        tk.Button(
            btn_row,
            text="Delete",
            command=on_delete,
            bg="#402020",
            fg="#ffb3b3",
            relief="flat",
            font=("Segoe UI", 8),
            padx=8,
            pady=5,
            cursor="hand2",
        ).pack(side="left")

    def set_image(self, photo: tk.PhotoImage) -> None:
        self.capsule.set_image(photo)

    def resize(self, width: int) -> None:
        self.capsule.resize(width)


_PLACEHOLDER_CACHE: dict[int, tk.PhotoImage] = {}


def get_shared_placeholder(width: int) -> tk.PhotoImage | None:
    width = capsule_size(width)[0]
    cached = _PLACEHOLDER_CACHE.get(width)
    if cached is not None:
        return cached
    photo = make_placeholder_photo(width)
    if photo is not None:
        _PLACEHOLDER_CACHE[width] = photo
    return photo


def _make_vertical_gradient(width: int, height: int, start_y: int) -> Image.Image:
    """Fast gradient strip for bottom overlay."""
    grad_h = max(1, height - start_y)
    strip = Image.new("L", (1, grad_h))
    pixels = strip.load()
    assert pixels is not None
    for y in range(grad_h):
        pixels[0, y] = int(255 * ((y / grad_h) ** 1.15))
    return strip.resize((width, grad_h), Image.Resampling.BILINEAR)


def load_cover_pil(image_bytes: bytes, width: int) -> Image.Image | None:
    if not image_bytes or Image is None:
        return None
    card_w, card_h = capsule_size(width)
    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    image = _crop_capsule(image, card_w, card_h)
    return _apply_capsule_style(image, card_w, card_h)


def load_cover_photo(image_bytes: bytes, width: int) -> tk.PhotoImage | None:
    if ImageTk is None:
        return None
    image = load_cover_pil(image_bytes, width)
    if image is None:
        return None
    return ImageTk.PhotoImage(image)


def _crop_capsule(image: Image.Image, width: int, height: int) -> Image.Image:
    """Crop landscape Steam capsule into portrait box (object-fit: cover)."""
    target_ratio = width / height
    src_w, src_h = image.size
    src_ratio = src_w / src_h

    if src_ratio > target_ratio:
        new_w = int(src_h * target_ratio)
        left = (src_w - new_w) // 2
        image = image.crop((left, 0, left + new_w, src_h))
    else:
        new_h = int(src_w / target_ratio)
        top = max(0, (src_h - new_h) // 5)
        image = image.crop((0, top, src_w, min(src_h, top + new_h)))

    return image.resize((width, height), Image.Resampling.BILINEAR)


def _apply_capsule_style(image: Image.Image, width: int, height: int) -> Image.Image:
    """Rounded corners + bottom gradient like playzip.com."""
    if ImageDraw is None:
        return image

    radius = max(4, int(CAPSULE_RADIUS * width / 168))
    mask = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0, width, height), radius=radius, fill=255)

    output = Image.new("RGB", (width, height), "#333333")
    output.paste(image, (0, 0), mask=mask)

    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    grad_top = int(height * 0.62)
    grad = _make_vertical_gradient(width, height, grad_top)
    overlay.paste(Image.merge("RGBA", (grad, grad, grad, grad)), (0, grad_top))

    output = output.convert("RGBA")
    output = Image.alpha_composite(output, overlay)
    return output.convert("RGB")


def make_placeholder_pil(width: int) -> Image.Image | None:
    if Image is None or ImageDraw is None:
        return None
    card_w, card_h = capsule_size(width)
    image = Image.new("RGB", (card_w, card_h), "#333333")
    radius = max(4, int(CAPSULE_RADIUS * card_w / 168))
    mask = Image.new("L", (card_w, card_h), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0, card_w, card_h), radius=radius, fill=255)
    base = Image.new("RGB", (card_w, card_h), "#333333")
    output = Image.new("RGB", (card_w, card_h), "#333333")
    output.paste(base, (0, 0), mask=mask)
    grad_top = int(card_h * 0.62)
    grad = _make_vertical_gradient(card_w, card_h, grad_top)
    dark = Image.new("RGBA", (card_w, card_h), (0, 0, 0, 0))
    dark.paste(Image.merge("RGBA", (grad, grad, grad, grad)), (0, grad_top))
    output = output.convert("RGBA")
    output = Image.alpha_composite(output, dark)
    return output.convert("RGB")


def make_placeholder_photo(width: int) -> tk.PhotoImage | None:
    if ImageTk is None:
        return None
    image = make_placeholder_pil(width)
    if image is None:
        return None
    return ImageTk.PhotoImage(image)
