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

from dictation import autostart, config, panel_style
from dictation.prompt import load_prompt, save_prompt
from dictation.prompt_editor import PromptEditor
from dictation.status_panel import StatusPanel

ActivityState = Literal["idle", "recording", "transcribing", "processing"]
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
        on_toggle_ai: Callable[[bool], None] | None = None,
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
        # Имена с «notify»: обработчик, названный как метод класса, затирает
        # этот метод собой, и вызвать его становится нечем.
        self._notify_toggle_ai = on_toggle_ai
        self._on_hotkey_mode_change: Callable[[str], None] | None = None
        self._icon: pystray.Icon | None = None
        self._ready = threading.Event()
        self._history: deque[str] = deque(maxlen=_HISTORY_SIZE)
        self._history_lock = threading.Lock()
        cfg = config.load_config()
        self._hotkey_mode = cfg.get("hotkey_mode", "hold")
        self._cpu_threads = int(cfg.get("cpu_threads", 6))
        self._ai_enabled = bool(cfg.get("ai_enabled", False))
        self._autostart_enabled = autostart.is_autostart_enabled()
        self._panel_style = panel_style.normalize_style(cfg.get("panel_style"))
        self._panel_theme = panel_style.normalize_theme(cfg.get("panel_theme"))
        self._status_panel = StatusPanel(
            on_abort=self._handle_abort,
            on_stop_recording=self._handle_stop_recording,
            on_toggle_ai=self._handle_toggle_ai,
            ai_enabled=self._ai_enabled,
            ai_ready=bool(str(cfg.get("ai_api_key", "")).strip()),
            style=self._panel_style,
            theme=self._panel_theme,
        )
        self._prompt_editor: PromptEditor | None = None

    def reset_session(self) -> None:
        self._status_panel.reset_session()

    def should_skip_auto_enter(self) -> bool:
        return self._status_panel.skip_auto_enter()

    def ai_enabled(self) -> bool:
        return self._status_panel.ai_enabled()

    def ai_ready(self) -> bool:
        return self._status_panel.ai_ready()

    def refresh_ai_settings(self) -> None:
        """Перечитать настройки обработки и показать их на кнопке панели.

        Ключ правят руками в `config.local.json`, а приложение работает без
        окна: без этого перечитывания кнопка продолжала бы говорить «нет
        ключа» до перезапуска.
        """
        cfg = config.load_config()
        self._status_panel.set_ai_ready(bool(str(cfg.get("ai_api_key", "")).strip()))

    def report_ai_error(self, reason: str) -> None:
        """Обработка не прошла: сказать в уведомлении и пометить кнопку."""
        self._status_panel.set_ai_error(reason)
        self.notify_ai_error(reason)

    def clear_ai_error(self) -> None:
        self._status_panel.set_ai_error(None)

    def mark_transcribed(self) -> None:
        """Распознавание позади: на панели зажигается вторая ступень."""
        self._status_panel.mark_transcribed()

    def finish_ai_stage(self, recovered: bool = True) -> None:
        """Итог обработки ИИ: вторая ступень заполнилась или перечёркнута."""
        self._status_panel.set_ai_finished(recovered)

    def ai_hint(self) -> str:
        return self._status_panel.ai_hint()

    def _handle_abort(self) -> None:
        if self._on_abort is not None:
            self._on_abort()

    def _handle_stop_recording(self) -> None:
        if self._on_stop_recording is not None:
            self._on_stop_recording()

    def _save_ai_enabled(self, enabled: bool) -> None:
        """Записать решение об обработке в настройки.

        Пишут оба входа — пункт меню и кнопка панели. Иначе кнопка жила до
        перезапуска, а после него обработка возвращалась к старому значению.
        """
        self._ai_enabled = enabled
        cfg = config.load_config()
        cfg["ai_enabled"] = enabled
        config.save_config(cfg)
        self._refresh_menu()
        if self._notify_toggle_ai is not None:
            self._notify_toggle_ai(enabled)
        # Включили, а обрабатывать нечем — говорим сейчас, иначе человек
        # узнает об этом только по вставленной необработанной диктовке.
        if enabled and not self._status_panel.ai_ready():
            self.notify_ai_not_ready()

    def _handle_toggle_ai(self, enabled: bool) -> None:
        self._save_ai_enabled(enabled)

    def open_prompt_editor(self) -> None:
        if self._prompt_editor is None:
            self._prompt_editor = PromptEditor(
                load=lambda: load_prompt(self._project_root),
                save=lambda text: save_prompt(self._project_root, text),
            )
        self._prompt_editor.open()

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

    def notify_ai_error(self, reason: str) -> None:
        """Сказать, что обработка через ИИ не удалась.

        Молчать об этом нельзя: человек увидит в поле ввода необработанный
        текст и решит, что промпт плохой или модель не работает, — а причина
        в ключе, связи или балансе.
        """
        if self._icon is None:
            return
        try:
            self._icon.notify("Обработка через ИИ не удалась", reason)
        except Exception:
            pass

    def notify_ai_not_ready(self) -> None:
        """Обработку включили, а ключа нет. Сказать об этом сразу, а не после диктовки."""
        if self._icon is None:
            return
        try:
            self._icon.notify(
                "ИИ не настроен",
                "Ключа нет. Заведите его в кабинете федота.рф и вставьте "
                "в config.local.json рядом с config.json.",
            )
        except Exception:
            pass

    def notify_hotkey_failed(self) -> None:
        """Сказать, что горячая клавиша не работает.

        Молчать нельзя: человек нажимает клавишу, ничего не происходит, и
        выглядит это как поломка приложения, а не как перехват ввода чужим
        процессом.
        """
        self.set_tooltip("Диктовка: горячая клавиша НЕ работает")
        if self._icon is None:
            return
        try:
            self._icon.notify(
                "Горячая клавиша не работает",
                "Нажатия не доходят до диктовки. Обычно мешает приложение, "
                "запущенное от администратора.",
            )
        except Exception:
            pass

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
                "Обработка через ИИ",
                self._toggle_ai,
                checked=lambda _item: self._ai_enabled,
            ),
            pystray.MenuItem("Промпт обработки…", self._edit_prompt),
            pystray.MenuItem(
                "Вид панели",
                pystray.Menu(
                    *[
                        pystray.MenuItem(
                            label,
                            self._select_panel_style(name),
                            checked=lambda _item, n=name: self._panel_style == n,
                            radio=True,
                        )
                        for name, label in panel_style.STYLE_LABELS.items()
                    ]
                ),
            ),
            pystray.MenuItem(
                "Тема панели",
                pystray.Menu(
                    *[
                        pystray.MenuItem(
                            label,
                            self._select_panel_theme(name),
                            checked=lambda _item, n=name: self._panel_theme == n,
                            radio=True,
                        )
                        for name, label in panel_style.THEME_LABELS.items()
                    ]
                ),
            ),
            pystray.MenuItem(
                "Запуск с Windows",
                self._toggle_autostart,
                checked=lambda _item: self._autostart_enabled,
            ),
            pystray.MenuItem("Перезагрузить", self._restart),
            pystray.MenuItem("Выход", self._exit),
        )

    def _select_panel_style(self, style: str) -> Callable[[pystray.Icon, pystray.MenuItem], None]:
        """Вид панели: показать сразу и запомнить.

        Панель живёт всё время работы приложения, поэтому смена вида не ждёт
        перезапуска: человек выбирает пункт и тут же видит его в деле.
        """

        def _handler(_icon: pystray.Icon, _item: pystray.MenuItem) -> None:
            if self._panel_style == style:
                return
            self._panel_style = panel_style.apply_style(style)
            self._status_panel.set_style(self._panel_style)
            self._refresh_menu()

        return _handler

    def _select_panel_theme(self, theme: str) -> Callable[[pystray.Icon, pystray.MenuItem], None]:
        def _handler(_icon: pystray.Icon, _item: pystray.MenuItem) -> None:
            if self._panel_theme == theme:
                return
            self._panel_theme = panel_style.apply_theme(theme)
            self._status_panel.set_theme(self._panel_theme)
            self._refresh_menu()

        return _handler

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

    def _toggle_ai(self, _icon: pystray.Icon, _item: pystray.MenuItem) -> None:
        """Меню трея и кнопка панели делают одно и то же: переключают и запоминают."""
        enabled = not self._ai_enabled
        # Панель тоже надо сказать про новое значение: без этого кнопка держала
        # бы прежнее состояние и при первом нажатии вела себя вслепую.
        self._status_panel.set_ai_default(enabled)
        self._save_ai_enabled(enabled)

    def _edit_prompt(self, _icon: pystray.Icon, _item: pystray.MenuItem) -> None:
        self.open_prompt_editor()

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
        if self._prompt_editor is not None:
            self._prompt_editor.stop()
        self._status_panel.stop()
        if self._icon is not None:
            self._icon.stop()
