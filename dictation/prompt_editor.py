"""Окно правки промпта обработки диктовки — страница на браузерном движке.

Промпт живёт в `prompt.txt` в корне проекта. Здесь его видно и можно править:
кнопка «Сохранить» пишет файл, «Восстановить» возвращает готовый текст по
умолчанию. Окно самостоятельное, а не часть панели: панель маленькая и
исчезает в простое, а текст правят спокойно и подолгу.

Прежде окно было на Tk: цвета были зашиты тёмными, а полоса прокрутки —
нативная и светлая, поэтому на светлой системе окно выглядело бледным.
Теперь страница `ui/editor.html`: тема светлая и тёмная, полоса прокрутки
нарисована стилями. Правило «пустой промпт не сохраняется» живёт здесь, в
Python, а страница только показывает сказанное.
"""
from __future__ import annotations

import json
import threading
from collections.abc import Callable
from typing import Any

from dictation import panel_style, web_lane
from dictation.prompt import DEFAULT_PROMPT

_log: Callable[[str], None] | None = None


def set_logger(logger: Callable[[str], None] | None) -> None:
    global _log
    _log = logger
    web_lane.set_logger(logger)


def _log_line(message: str) -> None:
    if _log is not None:
        try:
            _log(message)
        except Exception:  # noqa: BLE001
            pass


def current_theme() -> str:
    """Тема окна промпта: та же, что у системы. «Как в Windows» — без выбора."""
    return "light" if panel_style.system_theme() == "light" else "dark"


class PromptEditor:
    def __init__(self, load: Callable[[], str], save: Callable[[str], None]) -> None:
        self._load = load
        self._save = save
        self._hint = ""
        self._kind = ""
        self._opened = False
        self._ready = threading.Event()
        self._page = web_lane.make_page("editor", self._on_page_event)

    # --- публичный интерфейс ------------------------------------------------

    def open(self) -> None:
        if not self._page.ok:
            _log_line("окно промпта не открылось: страница недоступна")
            return
        self._opened = True
        self._hint = ""
        self._kind = ""
        self._send_full()
        self._page.show_windowed()
        self._ready.set()

    def is_open(self) -> bool:
        return self._opened

    def close(self) -> None:
        self._opened = False
        self._ready.clear()
        self._page.hide()

    def stop(self) -> None:
        self._opened = False
        self._ready.clear()
        self._page.close()

    def wait_ready(self, limit_s: float = 10.0) -> bool:
        """Дождаться, пока окно откроется по-настоящему.

        Открытие лишь просит окно показаться: оно поднимается в своём
        процессе и не сразу. Проверкам и повторному открытию нужно знать,
        что окно уже на экране.
        """
        return self._ready.wait(limit_s)

    def mark_ready(self) -> None:
        self._ready.set()

    # --- обмен --------------------------------------------------------------

    def _send_full(self) -> None:
        theme = current_theme()
        self._page.execute(f"window.EX && window.EX.theme({theme!r})")
        payload = json.dumps(
            {"text": self._load(), "hint": self._hint, "kind": self._kind, "theme": theme},
            ensure_ascii=False,
        )
        self._page.execute(f"window.EX && window.EX.apply({payload})")

    def _set_hint(self, message: str, kind: str = "") -> None:
        self._hint = message
        self._kind = kind
        payload = json.dumps({"hint": message, "kind": kind}, ensure_ascii=False)
        self._page.execute(f"window.EX && window.EX.apply({payload})")

    def _on_page_event(self, name: str, value: Any) -> None:
        if name == "save":
            self._on_save(value)
        elif name == "restore":
            self._on_restore()
        elif name == "close":
            self.close()
        elif name == "ready" and self._opened:
            self._send_full()

    def _on_save(self, value: Any) -> None:
        text = (value if isinstance(value, str) else "").rstrip("\n")
        if not text.strip():
            self._set_hint(
                "Пустой промпт не сохраняется: без инструкции обработка бессмысленна.",
                "bad",
            )
            return
        try:
            self._save(text + "\n")
        except OSError as error:
            _log_line(f"промпт не сохранён: {error}")
            self._set_hint(f"Не сохранилось: {error}", "bad")
            return
        self._set_hint("Сохранено.", "good")

    def _on_restore(self) -> None:
        text = DEFAULT_PROMPT + "\n"
        try:
            self._save(text)
        except OSError as error:
            _log_line(f"промпт не восстановлен: {error}")
            self._set_hint(f"Не восстановилось: {error}", "bad")
            return
        self._hint = "Восстановлен текст по умолчанию."
        self._kind = "good"
        payload = json.dumps(
            {"text": text, "hint": self._hint, "kind": self._kind, "theme": current_theme()},
            ensure_ascii=False,
        )
        self._page.execute(f"window.EX && window.EX.apply({payload})")


def supported() -> bool:
    return web_lane.available()
