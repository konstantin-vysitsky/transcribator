"""Окно правки промпта обработки диктовки.

Промпт живёт в `prompt.txt` в корне проекта. Здесь его видно и можно править:
кнопка «Сохранить» пишет файл, «Восстановить» возвращает готовый текст по
умолчанию. Окно самостоятельное, а не часть панели статуса: панель маленькая и
исчезает в простое, а текст правят спокойно и подолгу.

Как и панель, окно переключается в одном месте — здесь. Сейчас работает
встроенная графика Tk. Окно и панель пытались перенести на страницу
`ui/editor.html`, перенос сломался, и от него остался задел: сама страница
`ui/editor.html`, `ui/editor.css`, `ui/editor.js` да выбор способа рисовать.
Сохранение, восстановление и правило «пустой промпт не сохраняется» живут
здесь, в Python: так они одни для любого способа рисовать окно.
"""
from __future__ import annotations

import os
import sys
import threading
import tkinter as tk
from collections.abc import Callable

from dictation.prompt import DEFAULT_PROMPT

# --- чем рисуем окно ----------------------------------------------------------
#
# `tk` — встроенная графика, рабочее окно, оно и стоит по умолчанию.
# `page` — страница `ui/editor.html`, задел на будущее; пока не работает.
_DEFAULT_SURFACE = "tk"


_log: Callable[[str], None] | None = None


def set_logger(logger: Callable[[str], None] | None) -> None:
    """Куда писать причину, если окно не поднялось. Принимается всегда."""
    global _log
    _log = logger


def _log_line(message: str) -> None:
    if _log is not None:
        try:
            _log(message)
        except Exception:  # noqa: BLE001
            pass


def prompt_surface() -> str:
    """Чем рисуется окно промпта: встроенная графика, если не выбрано иное."""
    value = str(os.environ.get("DICTATION_SURFACE", "")).strip().lower()
    if value == "page":
        _log_line("выбрано окно промпта-страница, а перенос сломан: окно на Tk")
    return _DEFAULT_SURFACE


_BG = "#1e1e1e"
_FG = "#e8e8e8"
_HINT_FG = "#b0b0b0"
_EDIT_BG = "#252526"
_EDIT_FG = "#e8e8e8"
_OK_BG = "#2e7d32"
_OK_ACTIVE_BG = "#1b5e20"
_WARN_BG = "#1565c0"
_WARN_ACTIVE_BG = "#0d47a1"
_RESTORE_BG = "#3a3a3a"
_RESTORE_ACTIVE_BG = "#4a4a4a"
_WINDOW_W = 640
_WINDOW_H = 560
_PAD = 14


class PromptEditor:
    """Окно правки промпта. Имя одно для любого способа рисовать окно."""

    def __init__(self, load: Callable[[], str], save: Callable[[str], None]) -> None:
        if prompt_surface() != "tk":
            # Задел на будущее: окно-страница здесь не поднимается, и
            # приложение без него остаётся рабочим.
            _log_line("окно промпта-страница не работает: перенос сломан")
        self._load = load
        self._save = save
        self._root: tk.Tk | None = None
        self._window: tk.Toplevel | None = None
        self._text: tk.Text | None = None
        self._hint: tk.Label | None = None
        self._ready = threading.Event()
        threading.Thread(target=self._run_guarded, daemon=True, name="prompt-editor").start()
        self._ready.wait(timeout=5.0)

    # --- построение окна ---------------------------------------------------

    def _run_guarded(self) -> None:
        """Построить окно в своём потоке и не уронить приложение при сбое.

        Исключение из потока окна пишется в журнал как поломка всего
        приложения, хотя без окна промпта диктовка работает. Причина уходит
        одной строкой в журнал, а нерабочее окно молча ничего не делает.
        """
        try:
            self._run()
        except Exception as error:  # noqa: BLE001
            _log_line(f"окно промпта не поднялось: {type(error).__name__}: {error}")
            self._root = None
        finally:
            self._ready.set()

    def _run(self) -> None:
        root = tk.Tk()
        root.withdraw()
        self._root = root

        window = tk.Toplevel(root)
        window.title("Промпт обработки диктовки")
        window.configure(bg=_BG)
        window.withdraw()
        window.protocol("WM_DELETE_WINDOW", self._hide)
        self._window = window

        frame = tk.Frame(window, bg=_BG)
        frame.pack(fill="both", expand=True, padx=_PAD, pady=_PAD)

        header = tk.Label(
            frame,
            text=(
                "Инструкция для модели: как приводить надиктованное к чёткому виду. "
                "Смысл менять нельзя — это записано в промпте явно."
            ),
            font=("Segoe UI", 9),
            fg=_HINT_FG,
            bg=_BG,
            justify="left",
            anchor="w",
            wraplength=_WINDOW_W - _PAD * 2,
        )
        header.pack(fill="x", pady=(0, 8))

        text_frame = tk.Frame(frame, bg=_EDIT_BG, highlightthickness=1)
        text_frame.configure(highlightbackground="#3a3a3a", highlightcolor="#3a3a3a")
        text_frame.pack(fill="both", expand=True)

        self._text = tk.Text(
            text_frame,
            wrap="word",
            font=("Segoe UI", 10),
            bg=_EDIT_BG,
            fg=_EDIT_FG,
            insertbackground=_EDIT_FG,
            relief="flat",
            bd=0,
            padx=8,
            pady=8,
            undo=True,
        )
        scrollbar = tk.Scrollbar(text_frame, command=self._text.yview)
        self._text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self._text.pack(side="left", fill="both", expand=True)

        self._hint = tk.Label(
            frame,
            text="",
            font=("Segoe UI", 9),
            fg=_HINT_FG,
            bg=_BG,
            anchor="w",
        )
        self._hint.pack(fill="x", pady=(8, 0))

        buttons = tk.Frame(frame, bg=_BG)
        buttons.pack(fill="x", pady=(10, 0))

        self._button(
            buttons,
            "Сохранить",
            self._on_save,
            bg=_OK_BG,
            active_bg=_OK_ACTIVE_BG,
        ).pack(side="left")

        self._button(
            buttons,
            "Восстановить",
            self._on_restore,
            bg=_RESTORE_BG,
            active_bg=_RESTORE_ACTIVE_BG,
        ).pack(side="left", padx=(8, 0))

        self._button(
            buttons,
            "Закрыть",
            self._hide,
            bg=_WARN_BG,
            active_bg=_WARN_ACTIVE_BG,
        ).pack(side="right")

        self._center()
        self._ready.set()
        root.mainloop()

    def _center(self) -> None:
        if self._root is None or self._window is None:
            return
        screen_w = self._root.winfo_screenwidth()
        screen_h = self._root.winfo_screenheight()
        x = max(0, (screen_w - _WINDOW_W) // 2)
        y = max(0, (screen_h - _WINDOW_H) // 3)
        self._window.geometry(f"{_WINDOW_W}x{_WINDOW_H}+{x}+{y}")

    def _button(
        self,
        parent: tk.Misc,
        text: str,
        command: Callable[[], None],
        *,
        bg: str,
        active_bg: str,
    ) -> tk.Button:
        return tk.Button(
            parent,
            text=text,
            command=command,
            font=("Segoe UI", 10, "bold"),
            bg=bg,
            fg="#ffffff",
            activebackground=active_bg,
            activeforeground="#ffffff",
            relief="flat",
            bd=0,
            padx=18,
            pady=7,
            cursor="hand2",
            highlightthickness=0,
        )

    def _center(self) -> None:
        if self._root is None or self._window is None:
            return
        screen_w = self._root.winfo_screenwidth()
        screen_h = self._root.winfo_screenheight()
        x = max(0, (screen_w - _WINDOW_W) // 2)
        y = max(0, (screen_h - _WINDOW_H) // 3)
        self._window.geometry(f"{_WINDOW_W}x{_WINDOW_H}+{x}+{y}")

    # --- действия ----------------------------------------------------------

    def _set_hint(self, message: str) -> None:
        if self._hint is not None:
            self._hint.configure(text=message)

    def _write_text(self, value: str) -> None:
        if self._text is None:
            return
        self._text.delete("1.0", "end")
        self._text.insert("1.0", value)

    def _on_save_done(self, message: str) -> None:
        """Положить подсказку под полем: её видно там, где правят текст."""
        self._set_hint(message)

    def _on_save(self) -> None:
        """Сохранить правку из поля окна.

        Правило одно на все пути: пустой промпт не сохраняется, а причина
        видна под полем. Так было и до переноса на страницу.
        """
        if self._text is None:
            return
        value = self._text.get("1.0", "end").rstrip("\n")
        if not value.strip():
            self._on_save_done("Пустой промпт не сохраняется: без инструкции обработка бессмысленна.")
            return
        self._save_text(value, "Сохранено.")

    def _on_restore(self) -> None:
        text = DEFAULT_PROMPT
        self._write_text(text + "\n")
        self._save_text(text, "Восстановлен текст по умолчанию.")

    def _save_text(self, value: str, done: str) -> None:
        """Записать промпт на диск и сказать об этом.

        Ошибка записи — не поломка приложения: окно говорит, что не вышло,
        а диктовка живёт со старым промптом.
        """
        try:
            self._save(value + "\n")
        except OSError as error:
            _log_line(f"промпт не записан: {error}")
            self._on_save_done(f"Не записалось: {error}")
            return
        self._on_save_done(done)

    def _hide(self) -> None:
        if self._window is not None:
            self._window.withdraw()

    # --- публичный интерфейс ----------------------------------------------

    def _visible(self) -> bool:
        """Показано ли окно на самом деле: спрятанное считается закрытым."""
        window = self._window
        if window is None:
            return False
        try:
            return bool(window.winfo_viewable())
        except tk.TclError:
            return False

    def is_open(self) -> bool:
        return self._visible()

    def close(self) -> None:
        """Спрятать окно. Звать можно из любого потока."""
        root = self._root
        if root is None:
            return
        try:
            root.after(0, self._hide)
        except (RuntimeError, tk.TclError):
            pass

    def mark_ready(self) -> None:
        """Окно построено: кто ждёт готовности, больше не ждёт."""
        self._ready.set()

    def wait_ready(self, limit_s: float = 10.0) -> bool:
        """Дождаться, пока окно построится по-настоящему."""
        return self._ready.wait(limit_s)

    def open(self) -> None:
        if self._root is None:
            return

        def _show() -> None:
            if self._window is None:
                return
            self._write_text(self._load())
            self._set_hint("")
            self._window.deiconify()
            self._window.lift()
            self._window.focus_force()

        try:
            self._root.after(0, _show)
        except (RuntimeError, tk.TclError):
            pass

    def stop(self) -> None:
        if self._root is None:
            return

        def _shutdown() -> None:
            if self._window is not None:
                self._window.destroy()
                self._window = None
            self._ready.clear()
            self._root.quit()

        try:
            self._root.after(0, _shutdown)
        except (RuntimeError, tk.TclError):
            pass


def supported() -> bool:
    return sys.platform == "win32"
