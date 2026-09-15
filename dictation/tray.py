from __future__ import annotations

import ctypes
import threading
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Literal

import pyperclip
import pystray
from PIL import Image, ImageDraw

from dictation import autostart, config
from dictation.status_panel import StatusPanel

ActivityState = Literal["idle", "recording", "transcribing"]
_HISTORY_SIZE = 3
_PREVIEW_LEN = 48


def _make_icon(size: int = 64) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    cx = size // 2
    body_w = size // 3
    body_h = size // 2
    top = size // 5
    draw.rounded_rectangle(
        (cx - body_w // 2, top, cx + body_w // 2, top + body_h),
        radius=body_w // 2,
        fill=(66, 133, 244, 255),
    )
    draw.arc(
        (cx - body_w, top + body_h // 2, cx + body_w, top + body_h + body_w),
        start=0,
        end=180,
        fill=(66, 133, 244, 255),
        width=max(2, size // 16),
    )
    draw.line(
        (cx, top + body_h + body_w, cx, size - size // 6),
        fill=(66, 133, 244, 255),
        width=max(2, size // 16),
    )
    draw.line(
        (cx - body_w // 2, size - size // 6, cx + body_w // 2, size - size // 6),
        fill=(66, 133, 244, 255),
        width=max(2, size // 16),
    )
    return img


class _LeftClickIcon(pystray.Icon):
    """Открывает меню по левому и правому клику."""

    def __init__(
        self,
        *args,
        menu_factory: Callable[[], pystray.Menu] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._menu_factory = menu_factory
        self._menu_dirty = True

    def mark_menu_dirty(self) -> None:
        self._menu_dirty = True

    def _sync_menu_if_needed(self) -> None:
        if not self._menu_dirty or self._menu_factory is None:
            return
        self.menu = self._menu_factory()
        self._menu_dirty = False

    def _show_popup_menu(self) -> None:
        if not self._menu_handle:
            return

        from ctypes import wintypes

        from pystray._util import win32

        win32.SetForegroundWindow(self._hwnd)
        point = wintypes.POINT()
        win32.GetCursorPos(ctypes.byref(point))
        hmenu, descriptors = self._menu_handle
        index = win32.TrackPopupMenuEx(
            hmenu,
            win32.TPM_RIGHTALIGN
            | win32.TPM_BOTTOMALIGN
            | win32.TPM_RETURNCMD,
            point.x,
            point.y,
            self._menu_hwnd,
            None,
        )
        if index > 0:
            descriptors[index - 1](self)

    def _on_notify(self, wparam, lparam):  # type: ignore[no-untyped-def]
        from pystray._util import win32

        if lparam in (win32.WM_LBUTTONUP, win32.WM_RBUTTONUP):
            self._sync_menu_if_needed()
            self._show_popup_menu()
            return
        super()._on_notify(wparam, lparam)


class TrayApp:
    def __init__(
        self,
        project_root: Path,
        status_text: str,
        on_exit: Callable[[], None],
        on_restart: Callable[[], None] | None = None,
        on_abort: Callable[[], None] | None = None,
        on_stop_recording: Callable[[], None] | None = None,
    ) -> None:
        self._project_root = project_root
        self._idle_tooltip = status_text
        self._activity: ActivityState = "idle"
        self._activity_detail = ""
        self._runtime_info = ""
        self._on_exit = on_exit
        self._on_restart = on_restart
        self._on_abort = on_abort
        self._on_stop_recording = on_stop_recording
        self._on_hotkey_mode_change: Callable[[str], None] | None = None
        self._icon: pystray.Icon | None = None
        self._ready = threading.Event()
        self._history: deque[str] = deque(maxlen=_HISTORY_SIZE)
        self._history_lock = threading.Lock()
        cfg = config.load_config()
        self._hotkey_mode = cfg.get("hotkey_mode", "hold")
        self._cpu_threads = int(cfg.get("cpu_threads", 6))
        self._autostart_enabled = autostart.is_autostart_enabled()
        self._status_panel = StatusPanel(
            on_abort=self._handle_abort,
            on_stop_recording=self._handle_stop_recording,
        )

    def reset_session(self) -> None:
        self._status_panel.reset_session()

    def should_skip_auto_enter(self) -> bool:
        return self._status_panel.skip_auto_enter()

    def _handle_abort(self) -> None:
        if self._on_abort is not None:
            self._on_abort()

    def _handle_stop_recording(self) -> None:
        if self._on_stop_recording is not None:
            self._on_stop_recording()

    def set_hotkey_mode_handler(self, handler: Callable[[str], None]) -> None:
        self._on_hotkey_mode_change = handler

    def set_hotkey_mode(self, mode: str) -> None:
        if mode in ("hold", "toggle"):
            self._hotkey_mode = mode
            self._refresh_menu()

    def set_runtime_info(self, info: str) -> None:
        self._runtime_info = info.strip()
        self._refresh_menu()

    def set_tooltip(self, text: str) -> None:
        self._idle_tooltip = text
        if self._activity == "idle" and self._icon is not None:
            self._icon.title = text

    def set_activity(
        self,
        state: ActivityState,
        detail: str = "",
        progress: float | None = None,
    ) -> None:
        state_changed = state != self._activity
        self._activity = state
        self._activity_detail = detail
        self._status_panel.update(state, detail, progress)
        if self._icon is None:
            return

        if state == "recording":
            self._icon.title = "● Запись — говорите"
            try:
                self._icon.notify("Диктовка", "Запись — говорите")
            except Exception:
                pass
        elif state == "transcribing":
            title = "Распознаю…"
            if detail:
                title = f"Распознаю… {detail}"
            self._icon.title = title
        else:
            self._icon.title = self._idle_tooltip

        if state_changed:
            self._refresh_menu()

    @staticmethod
    def _preview(text: str) -> str:
        one_line = " ".join(text.split())
        if len(one_line) <= _PREVIEW_LEN:
            return one_line
        return one_line[: _PREVIEW_LEN - 1] + "…"

    def add_transcription(self, text: str) -> None:
        if not text.strip():
            return
        with self._history_lock:
            self._history.appendleft(text.strip())
        self._refresh_menu()

    def _copy_history_item(self, text: str) -> Callable[[pystray.Icon, pystray.MenuItem], None]:
        def _handler(_icon: pystray.Icon, _item: pystray.MenuItem) -> None:
            try:
                pyperclip.copy(text)
            except Exception:
                return
            if self._icon is not None:
                try:
                    self._icon.notify("Скопировано", self._preview(text))
                except Exception:
                    pass

        return _handler

    def _build_history_menu(self) -> pystray.Menu:
        with self._history_lock:
            items = list(self._history)
        if not items:
            return pystray.Menu(pystray.MenuItem("Пока пусто", None, enabled=False))
        return pystray.Menu(
            *[
                pystray.MenuItem(self._preview(item), self._copy_history_item(item))
                for item in items
            ]
        )

    def _menu_status_text(self, _item: pystray.MenuItem) -> str:
        if self._activity == "recording":
            return "● Запись"
        if self._activity == "transcribing":
            if self._activity_detail:
                return f"Распознаю… {self._activity_detail}"
            return "Распознаю…"
        if self._runtime_info:
            return f"Готов · {self._runtime_info}"
        return "Готов"

    def _refresh_menu(self) -> None:
        if isinstance(self._icon, _LeftClickIcon):
            self._icon.mark_menu_dirty()

    def _build_menu(self) -> pystray.Menu:
        cpu_items = [
            pystray.MenuItem(
                str(n),
                self._select_cpu_threads(n),
                checked=lambda _item, n=n: self._cpu_threads == n,
                radio=True,
            )
            for n in range(1, 11)
        ]
        return pystray.Menu(
            pystray.MenuItem(self._menu_status_text, None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Последние", self._build_history_menu()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Режим клавиши",
                pystray.Menu(
                    pystray.MenuItem(
                        "Удержание (отпустить — стоп)",
                        self._select_hotkey_mode("hold"),
                        checked=lambda _item: self._hotkey_mode == "hold",
                        radio=True,
                    ),
                    pystray.MenuItem(
                        "Нажать — старт, ещё раз — стоп",
                        self._select_hotkey_mode("toggle"),
                        checked=lambda _item: self._hotkey_mode == "toggle",
                        radio=True,
                    ),
                ),
            ),
            pystray.MenuItem(
                "Потоки CPU (перезагрузка)",
                pystray.Menu(*cpu_items),
            ),
            pystray.MenuItem(
                "Запуск с Windows",
                self._toggle_autostart,
                checked=lambda _item: self._autostart_enabled,
            ),
            pystray.MenuItem("Перезагрузить", self._restart),
            pystray.MenuItem("Выход", self._exit),
        )

    def _select_hotkey_mode(self, mode: str) -> Callable[[pystray.Icon, pystray.MenuItem], None]:
        def _handler(_icon: pystray.Icon, _item: pystray.MenuItem) -> None:
            if self._hotkey_mode == mode:
                return
            self._hotkey_mode = mode
            cfg = config.load_config()
            cfg["hotkey_mode"] = mode
            config.save_config(cfg)
            if self._on_hotkey_mode_change is not None:
                self._on_hotkey_mode_change(mode)
            self._refresh_menu()

        return _handler

    def _select_cpu_threads(self, threads: int) -> Callable[[pystray.Icon, pystray.MenuItem], None]:
        def _handler(_icon: pystray.Icon, _item: pystray.MenuItem) -> None:
            if self._cpu_threads == threads:
                return
            self._cpu_threads = threads
            cfg = config.load_config()
            cfg["cpu_threads"] = threads
            config.save_config(cfg)
            self._refresh_menu()
            if self._icon is not None:
                try:
                    self._icon.notify(
                        "Потоки CPU",
                        f"Установлено {threads}. Перезагрузите приложение.",
                    )
                except Exception:
                    pass

        return _handler

    def _toggle_autostart(self, _icon: pystray.Icon, _item: pystray.MenuItem) -> None:
        if self._autostart_enabled:
            autostart.disable_autostart()
            self._autostart_enabled = False
        else:
            autostart.enable_autostart(self._project_root)
            self._autostart_enabled = True
        self._refresh_menu()

    def _restart(self, _icon: pystray.Icon, _item: pystray.MenuItem) -> None:
        if self._on_restart is not None:
            self._on_restart()
        if self._icon is not None:
            self._icon.stop()

    def _exit(self, _icon: pystray.Icon, _item: pystray.MenuItem) -> None:
        self._on_exit()
        if self._icon is not None:
            self._icon.stop()

    def run(self) -> None:
        self._icon = _LeftClickIcon(
            "dictation",
            _make_icon(),
            self._idle_tooltip,
            menu=self._build_menu(),
            menu_factory=self._build_menu,
        )
        self._ready.set()
        self._icon.run()

    def start(self) -> None:
        threading.Thread(target=self.run, daemon=True, name="tray-icon").start()
        self._ready.wait()

    def stop(self) -> None:
        self._status_panel.stop()
        if self._icon is not None:
            self._icon.stop()
