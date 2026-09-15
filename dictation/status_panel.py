from __future__ import annotations

import threading
import tkinter as tk
from collections.abc import Callable
from tkinter import ttk
from typing import Literal

ActivityState = Literal["idle", "recording", "transcribing"]

_BG = "#1e1e1e"
_FG = "#e8e8e8"
_ACCENT_RECORD = "#e53935"
_ACCENT_TRANSCRIBE = "#4285f4"
_BTN_ON_BG = "#2e7d32"
_BTN_ON_ACTIVE_BG = "#1b5e20"
_BTN_OFF_BG = "#c62828"
_BTN_OFF_ACTIVE_BG = "#8e0000"
_ABORT_BG = "#3a3a3a"
_ABORT_ACTIVE_BG = "#c62828"
_STOP_BG = "#1565c0"
_STOP_ACTIVE_BG = "#0d47a1"
_MARGIN_X = 16
_MARGIN_Y = 48
_PANEL_PAD_X = 14
_PANEL_PAD_Y = 10
_RADIUS = 10
_TOP_ROW_H = 40
_ABORT_SIZE = 40
_STOP_W = 72
_PANEL_W = 320


def _round_rect(
    canvas: tk.Canvas,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    radius: int,
    **kwargs,
) -> int:
    r = min(radius, int((x2 - x1) / 2), int((y2 - y1) / 2))
    points = [
        x1 + r,
        y1,
        x2 - r,
        y1,
        x2,
        y1,
        x2,
        y1 + r,
        x2,
        y2 - r,
        x2,
        y2,
        x2 - r,
        y2,
        x1 + r,
        y2,
        x1,
        y2,
        x1,
        y2 - r,
        x1,
        y1 + r,
        x1,
        y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


class _RoundButton(tk.Frame):
    def __init__(
        self,
        parent: tk.Misc,
        text: str,
        command: Callable[[], None],
        *,
        width: int | None = None,
        height: int = _TOP_ROW_H,
        bg: str,
        active_bg: str,
        fg: str = "#ffffff",
        font: tuple[str, int, str] | tuple[str, int] = ("Segoe UI", 10, "bold"),
        radius: int = _RADIUS,
        expand: bool = False,
    ) -> None:
        super().__init__(parent, bg=_BG)
        self._text = text
        self._command = command
        self._bg = bg
        self._active_bg = active_bg
        self._fg = fg
        self._font = font
        self._radius = radius
        self._pressed = False
        self._expand = expand

        self._canvas = tk.Canvas(
            self,
            height=height,
            highlightthickness=0,
            bd=0,
            bg=_BG,
            cursor="hand2",
        )
        if width is not None:
            self._canvas.configure(width=width)
        self._canvas.pack(fill="both", expand=True)
        self._canvas.bind("<Button-1>", self._on_click)
        self._canvas.bind("<Configure>", self._on_resize)
        if expand:
            self.bind("<Configure>", self._on_resize)

    def _on_resize(self, _event: tk.Event | None = None) -> None:
        self._draw()

    def _draw(self) -> None:
        self._canvas.delete("all")
        w = max(4, self._canvas.winfo_width())
        h = max(4, self._canvas.winfo_height())
        fill = self._active_bg if self._pressed else self._bg
        _round_rect(self._canvas, 1, 1, w - 1, h - 1, self._radius, fill=fill, outline="")
        self._canvas.create_text(
            w // 2,
            h // 2,
            text=self._text,
            fill=self._fg,
            font=self._font,
        )

    def configure_button(
        self,
        *,
        text: str | None = None,
        bg: str | None = None,
        active_bg: str | None = None,
    ) -> None:
        if text is not None:
            self._text = text
        if bg is not None:
            self._bg = bg
        if active_bg is not None:
            self._active_bg = active_bg
        self._draw()

    def _on_click(self, _event: tk.Event) -> None:
        self._pressed = True
        self._draw()
        self._command()
        self.after(120, self._release_visual)

    def _release_visual(self) -> None:
        self._pressed = False
        self._draw()


class StatusPanel:
    def __init__(
        self,
        on_abort: Callable[[], None] | None = None,
        on_stop_recording: Callable[[], None] | None = None,
    ) -> None:
        self._on_abort = on_abort
        self._on_stop_recording = on_stop_recording
        self._root: tk.Tk | None = None
        self._window: tk.Toplevel | None = None
        self._canvas: tk.Canvas | None = None
        self._content: tk.Frame | None = None
        self._status_label: tk.Label | None = None
        self._detail_label: tk.Label | None = None
        self._progress: ttk.Progressbar | None = None
        self._record_dot: tk.Label | None = None
        self._top_row: tk.Frame | None = None
        self._bottom_col: tk.Frame | None = None
        self._auto_enter_btn: _RoundButton | None = None
        self._stop_btn: _RoundButton | None = None
        self._abort_btn: _RoundButton | None = None
        self._skip_auto_enter = threading.Event()
        self._ready = threading.Event()
        self._pulse_job: str | None = None
        self._pulse_on = True
        threading.Thread(target=self._run, daemon=True, name="status-panel").start()
        self._ready.wait(timeout=5.0)

    def _run(self) -> None:
        root = tk.Tk()
        root.withdraw()
        self._root = root

        window = tk.Toplevel(root)
        window.withdraw()
        window.overrideredirect(True)
        window.attributes("-topmost", True)
        window.configure(bg=_BG)
        self._window = window

        canvas = tk.Canvas(window, highlightthickness=0, bd=0, bg=_BG)
        canvas.pack()
        self._canvas = canvas

        content = tk.Frame(canvas, bg=_BG)
        self._content = content
        content_window = canvas.create_window(
            0,
            0,
            window=content,
            anchor="nw",
            width=_PANEL_W,
        )

        def _resize_canvas(_event: tk.Event | None = None) -> None:
            if self._canvas is None or self._content is None:
                return
            self._content.update_idletasks()
            w = _PANEL_W + _PANEL_PAD_X * 2
            h = self._content.winfo_reqheight() + _PANEL_PAD_Y * 2
            self._canvas.configure(width=w, height=h)
            self._canvas.delete("panel_bg")
            _round_rect(
                self._canvas,
                0,
                0,
                w,
                h,
                _RADIUS,
                fill=_BG,
                outline="#3a3a3a",
                tags="panel_bg",
            )
            self._canvas.coords(content_window, _PANEL_PAD_X, _PANEL_PAD_Y)
            self._canvas.itemconfigure(content_window, width=_PANEL_W)

        frame = tk.Frame(content, bg=_BG)
        frame.pack(fill="x")
        frame.bind("<Configure>", _resize_canvas)
        frame.columnconfigure(0, weight=1)

        top_row = tk.Frame(frame, bg=_BG, height=_TOP_ROW_H)
        top_row.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        top_row.grid_propagate(False)
        top_row.columnconfigure(0, weight=1)
        top_row.columnconfigure(1, weight=0)
        top_row.columnconfigure(2, weight=0)
        self._top_row = top_row

        self._auto_enter_btn = _RoundButton(
            top_row,
            text="Автоотправка вкл",
            command=self._on_toggle_auto_enter,
            height=_TOP_ROW_H,
            bg=_BTN_ON_BG,
            active_bg=_BTN_ON_ACTIVE_BG,
            font=("Segoe UI", 9, "bold"),
            expand=True,
        )
        self._auto_enter_btn.grid(row=0, column=0, sticky="nsew", padx=(0, 6))

        self._stop_btn = _RoundButton(
            top_row,
            text="Готово",
            command=self._on_stop_click,
            width=_STOP_W,
            height=_TOP_ROW_H,
            bg=_STOP_BG,
            active_bg=_STOP_ACTIVE_BG,
            font=("Segoe UI", 9, "bold"),
            radius=8,
        )

        self._abort_btn = _RoundButton(
            top_row,
            text="✕",
            command=self._on_abort_click,
            width=_ABORT_SIZE,
            height=_TOP_ROW_H,
            bg=_ABORT_BG,
            active_bg=_ABORT_ACTIVE_BG,
            font=("Segoe UI", 13, "bold"),
            radius=8,
        )
        self._abort_btn.grid(row=0, column=2, sticky="ns")

        bottom_col = tk.Frame(frame, bg=_BG)
        bottom_col.grid(row=1, column=0, sticky="ew")
        bottom_col.columnconfigure(0, weight=1)
        self._bottom_col = bottom_col

        header = tk.Frame(bottom_col, bg=_BG)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)

        self._record_dot = tk.Label(
            header,
            text="●",
            font=("Segoe UI", 11),
            fg=_ACCENT_RECORD,
            bg=_BG,
        )

        self._status_label = tk.Label(
            header,
            text="",
            font=("Segoe UI", 10, "bold"),
            fg=_FG,
            bg=_BG,
            anchor="w",
        )
        self._status_label.grid(row=0, column=1, sticky="w")

        style = ttk.Style(window)
        style.theme_use("clam")
        style.configure(
            "Dictation.Horizontal.TProgressbar",
            troughcolor="#3a3a3a",
            background=_ACCENT_TRANSCRIBE,
            bordercolor=_BG,
            lightcolor=_ACCENT_TRANSCRIBE,
            darkcolor=_ACCENT_TRANSCRIBE,
            thickness=6,
        )

        self._progress = ttk.Progressbar(
            bottom_col,
            style="Dictation.Horizontal.TProgressbar",
            mode="determinate",
            maximum=100,
        )

        self._detail_label = tk.Label(
            bottom_col,
            text="",
            font=("Segoe UI", 9),
            fg="#b0b0b0",
            bg=_BG,
            anchor="w",
        )

        self._ready.set()
        _resize_canvas()
        root.mainloop()

    def _apply_auto_enter_on(self) -> None:
        if self._auto_enter_btn is not None:
            self._auto_enter_btn.configure_button(
                text="Автоотправка вкл",
                bg=_BTN_ON_BG,
                active_bg=_BTN_ON_ACTIVE_BG,
            )

    def _apply_auto_enter_off(self) -> None:
        if self._auto_enter_btn is not None:
            self._auto_enter_btn.configure_button(
                text="Без автоотправки",
                bg=_BTN_OFF_BG,
                active_bg=_BTN_OFF_ACTIVE_BG,
            )

    def _on_toggle_auto_enter(self) -> None:
        if self._skip_auto_enter.is_set():
            self._skip_auto_enter.clear()
            self._apply_auto_enter_on()
        else:
            self._skip_auto_enter.set()
            self._apply_auto_enter_off()

    def _on_stop_click(self) -> None:
        if self._on_stop_recording is not None:
            self._on_stop_recording()

    def _on_abort_click(self) -> None:
        self._skip_auto_enter.set()
        self._apply_auto_enter_off()
        if self._on_abort is not None:
            self._on_abort()
        # Панель закроется после фактической остановки транскрибации в main.py

    def _apply_reset_session(self) -> None:
        self._skip_auto_enter.clear()
        self._apply_auto_enter_on()

    def reset_session(self) -> None:
        if self._root is None:
            return
        self._root.after(0, self._apply_reset_session)

    def skip_auto_enter(self) -> bool:
        return self._skip_auto_enter.is_set()

    def _cancel_pulse(self) -> None:
        if self._root is None or self._pulse_job is None:
            self._pulse_job = None
            return
        try:
            self._root.after_cancel(self._pulse_job)
        except Exception:
            pass
        self._pulse_job = None

    def _schedule_pulse(self) -> None:
        self._cancel_pulse()
        if self._root is None or self._record_dot is None:
            return

        def _tick() -> None:
            if self._record_dot is None or self._root is None:
                return
            self._pulse_on = not self._pulse_on
            self._record_dot.configure(
                fg=_ACCENT_RECORD if self._pulse_on else _BG,
            )
            self._pulse_job = self._root.after(500, _tick)

        _tick()

    def _reposition(self) -> None:
        if self._root is None or self._window is None:
            return
        self._window.update_idletasks()
        width = self._window.winfo_width()
        height = self._window.winfo_height()
        x = self._root.winfo_screenwidth() - width - _MARGIN_X
        y = self._root.winfo_screenheight() - height - _MARGIN_Y
        self._window.geometry(f"+{x}+{y}")

    def _refresh_canvas_bg(self) -> None:
        if self._canvas is None or self._content is None:
            return
        self._content.update_idletasks()
        w = _PANEL_W + _PANEL_PAD_X * 2
        h = self._content.winfo_reqheight() + _PANEL_PAD_Y * 2
        self._canvas.configure(width=w, height=h)
        self._canvas.delete("panel_bg")
        _round_rect(
            self._canvas,
            0,
            0,
            w,
            h,
            _RADIUS,
            fill=_BG,
            outline="#3a3a3a",
            tags="panel_bg",
        )

    def _apply_update(
        self,
        state: ActivityState,
        detail: str = "",
        progress: float | None = None,
    ) -> None:
        if self._window is None or self._status_label is None:
            return

        self._cancel_pulse()

        if state == "idle":
            self._window.withdraw()
            return

        if self._progress is not None:
            self._progress.grid_remove()
        if self._detail_label is not None:
            self._detail_label.grid_remove()

        if state == "recording":
            if self._stop_btn is not None:
                self._stop_btn.grid(row=0, column=1, sticky="ns", padx=(0, 6))
            if self._record_dot is not None:
                self._record_dot.grid(row=0, column=0, sticky="w", padx=(0, 6))
            self._status_label.configure(text="Запись — говорите")
            self._schedule_pulse()
        elif state == "transcribing":
            if self._stop_btn is not None:
                self._stop_btn.grid_remove()
            if self._record_dot is not None:
                self._record_dot.grid_remove()
            self._status_label.configure(text="Распознаю…")
            if self._progress is not None:
                self._progress.grid(row=1, column=0, sticky="ew", pady=(6, 4))
                value = 0.0 if progress is None else max(0.0, min(1.0, progress)) * 100
                self._progress.configure(value=value)
            if self._detail_label is not None:
                detail_text = detail.strip()
                self._detail_label.configure(text=detail_text)
                if detail_text:
                    self._detail_label.grid(row=2, column=0, sticky="ew")

        self._refresh_canvas_bg()
        self._window.deiconify()
        self._reposition()
        self._window.lift()

    def update(
        self,
        state: ActivityState,
        detail: str = "",
        progress: float | None = None,
    ) -> None:
        if self._root is None:
            return
        self._root.after(0, lambda: self._apply_update(state, detail, progress))

    def stop(self) -> None:
        if self._root is None:
            return

        def _shutdown() -> None:
            self._cancel_pulse()
            if self._window is not None:
                self._window.destroy()
                self._window = None
            self._root.quit()

        self._root.after(0, _shutdown)
