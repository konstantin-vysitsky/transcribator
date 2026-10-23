"""Отдельный процесс с окнами интерфейса.

Движок браузера требует, чтобы его цикл окон запускался из главного потока
процесса, а главный поток диктовки занят хоткеем и записью. Поэтому окна
живут в своём процессе, а связь идёт через два потока:

    диктовка --send--> очередь --> поставить в окно
    диктовка <--queue-- окно <--нажатие

Если процессы разошлись (окна закрылись, процесс умер), диктовка пишет
причину в журнал и работает дальше без панели.

Запуск из кода: `Lane(callback).start()`. В отдельном режиме
(`python -m dictation.web_lane`) процесс читает те же сообщения от
родителя, чтобы долгий запуск не зависел от срока жизни питона.
"""
from __future__ import annotations

import ctypes
import importlib
import multiprocessing
import threading
import time
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

UI_DIR = Path(__file__).resolve().parent.parent / "ui"
PAGES = {"panel": "panel.html", "editor": "editor.html"}


def _put(queue: Any, kind: str, name: str, value: Any = None) -> None:
    try:
        queue.put({"kind": kind, "name": name, "value": value})
    except Exception:  # noqa: BLE001
        pass


_log: Callable[[str], None] | None = None


def set_logger(logger: Callable[[str], None] | None) -> None:
    """Куда писать причину, если окна не поднялись."""
    global _log
    _log = logger
    for lane_item in (_lane,):
        if lane_item is not None:
            lane_item._log = logger


def _log_line(message: str) -> None:
    if _log is not None:
        try:
            _log(message)
        except Exception:  # noqa: BLE001
            pass


def available() -> bool:
    """Есть ли движок и файлы страниц. Причина пишется в журнал."""
    if not _runtime_present():
        _log_line("WebView2 не установлен: окна интерфейса не покажутся")
        return False
    for name in PAGES.values():
        if not (UI_DIR / name).is_file():
            _log_line(f"страницы нет: {UI_DIR / name}")
            return False
    return True


class Lane:
    """Поток окон на стороне диктовки."""

    def __init__(
        self,
        on_event: Callable[[str, str, Any], None],
        logger: Callable[[str], None] | None = None,
    ) -> None:
        self._on_event = on_event
        self._log = logger
        self._ctx = multiprocessing.get_context("spawn")
        self._to_lane = self._ctx.Queue()
        self._from_lane = self._ctx.Queue()
        self._process: Any = None
        self._stop = threading.Event()
        self._started = threading.Event()
        self._summary: dict[str, bool] = {}
        self._lock = threading.Lock()
        self._asks: dict[str, Callable[[str, Any], None]] = {}

    # --- снаружи -------------------------------------------------------

    def start(self, timeout_s: float = 30.0) -> dict[str, bool]:
        """Поднять процесс окон. Возвращает, какие страницы поднялись."""
        self._process = self._ctx.Process(
            target=_lane_main,
            args=(self._to_lane, self._from_lane, self._summary, self._log),
            daemon=True,
            name="ui-lane",
        )
        self._process.start()
        threading.Thread(target=self._read, daemon=True, name="ui-lane-read").start()
        self._started.wait(timeout_s)
        return dict(self._summary)

    def call(self, uid: str, name: str, value: Any = None) -> None:
        """Что-то сделать с окном: выполнить страницу, поставить место, спрятать."""
        _put(self._to_lane, "call", name, {"uid": uid, "value": value})

    def execute(self, uid: str, script: str) -> None:
        """Позвать функцию страницы: она уже загружена и готова."""
        self.call(uid, "js", script)

    def measure(self, uid: str) -> None:
        self.call(uid, "measure")

    def ask_value(self, uid: str, script: str, limit_s: float = 5.0) -> Any:
        """Спросить страницу и дождаться ответа: так проверяют, что она видит.

        Ответ идёт тем же путём, что и нажатия: через очередь из процесса
        окон. Ждём здесь, в потоке диктовки: страница отвечает быстро.
        """
        box: dict[str, Any] = {}

        def take(name: str, value: Any) -> None:
            box["value"] = value

        with self._lock:
            self._asks[uid] = take
        try:
            self.call(uid, "value", script)
            deadline = time.monotonic() + limit_s
            while time.monotonic() < deadline and "value" not in box:
                time.sleep(0.02)
        finally:
            with self._lock:
                self._asks.pop(uid, None)
        return box.get("value")

    def show_windowed(self, uid: str) -> None:
        self.call(uid, "show")

    def close(self, uid: str) -> None:
        self.call(uid, "close")

    def alive(self) -> bool:
        return self._process is not None and self._process.is_alive()

    def stop(self) -> None:
        self._stop.set()
        _put(self._to_lane, "call", "quit", None)
        process = self._process
        if process is not None:
            process.join(timeout=3.0)
            if process.is_alive():
                process.terminate()

    # --- чтение ---------------------------------------------------------

    def _read(self) -> None:
        while not self._stop.is_set():
            try:
                message = self._from_lane.get(timeout=0.2)
            except Exception:  # noqa: BLE001
                if self._process is not None and not self._process.is_alive():
                    self._say("процесс окон завершился")
                    self._started.set()
                    return
                continue
            kind = message.get("kind")
            if kind == "event":
                try:
                    self._on_event(message.get("uid", ""), message.get("name", ""), message.get("value"))
                except Exception as error:  # noqa: BLE001
                    self._say(f"нажатие не обработано: {error}")
            elif kind == "ready":
                self._started.set()
            elif kind in ("measure", "text", "hint", "value"):
                uid = message.get("uid", "")
                with self._lock:
                    waiter = self._asks.get(uid) if kind == "value" else None
                if waiter is not None:
                    waiter(kind, message.get("value"))
                    continue
                try:
                    self._on_event(uid, kind, message.get("value"))
                except Exception as error:  # noqa: BLE001
                    self._say(f"ответ страницы не разобран: {error}")
            elif kind == "log":
                self._say(str(message.get("value", "")))

    def _say(self, message: str) -> None:
        if self._log is not None:
            try:
                self._log(message)
            except Exception:  # noqa: BLE001
                pass


# --- процесс окон -----------------------------------------------------------


class _Host:
    """Одно окно страницы внутри процесса окон."""

    def __init__(self, uid: str, kind: str, out: Any) -> None:
        self.uid = uid
        self.kind = kind
        self._out = out
        self._window: Any = None
        self._hwnd = 0
        self._form: Any = None
        self._size: tuple[int, int] = (1, 1)
        self._shown = False
        self._wanted_place: tuple[int, int] | None = None
        self._hidden_spot = (0, 0)
        self.ready = False
        self._page_ready = False
        self._pending: list[dict[str, Any]] = []

    def create(self, webview: Any) -> None:
        """Создать окно видимым, но далеко за краем экрана.

        Скрытое окно движок не перерисовывает: после первой отрисовки оно
        замирает и при показе выходит пустым. Поэтому окно живёт открытым
        на пару экранов ниже и выше, где его никто не видит, а на экран
        попадает уже нарисованным.
        """
        rect = work_area()
        self._hidden_spot = (rect[2] + 20, rect[3] + 20)
        self._size = (720, 600) if self.kind == "editor" else (1, 1)
        window = webview.create_window(
            "dictation-panel" if self.kind == "panel" else "dictation-prompt",
            url=str((UI_DIR / PAGES[self.kind]).resolve()),
            width=self._size[0],
            height=self._size[1],
            x=self._hidden_spot[0],
            y=self._hidden_spot[1],
            frameless=self.kind == "panel",
            easy_drag=False,
            on_top=True,
            transparent=self.kind == "panel",
            background_color="#000000" if self.kind == "panel" else "#1e1e1e",
            resizable=self.kind != "panel",
            shadow=False,
            focus=False,
            hidden=False,
            js_api=_Door(self),
        )
        self._window = window
        self._shown = True
        if self.kind == "panel":
            threading.Thread(target=self._polish_panel, daemon=True, name="panel-polish").start()
        else:
            self._page_ready = True
            self.flush()
            threading.Thread(target=self._await_editor, daemon=True, name="editor-await").start()

    def _await_editor(self) -> None:
        """Дождаться окна промпта и поставить его: место готово заранее."""
        found = self._await(limit_s=60.0)
        if not found:
            self._log("окно промпта не нашлось")
            return
        width, height = self._size
        left, top, right, bottom = work_area()
        x = left + max(0, (right - left - width) // 2)
        y = top + max(0, (bottom - top - height) // 3)
        if self._wanted_place is not None:
            x, y = self._wanted_place
            self._wanted_place = None
        move_window(self._hwnd, x, y, width, height)
        self._log(f"окно промпта готово в {x},{y} {self._size}")

    def _polish_panel(self) -> None:
        """Дождаться окна за краем экрана и подготовить его к стеклу.

        Пока окно за краем, человек его не видит, и белая подложка формы
        не успевает мелькнуть. Дальше её снимают совсем.
        """
        try:
            found = self._await()
            self._log(f"панель: окно {'нашлось' if found else 'не нашлось'}")
            if found:
                hand = self._form
                if hand is not None:
                    import System.Drawing as drawing  # noqa: PLC0415
                    import System.Windows.Forms as win_forms  # noqa: PLC0415

                    def shave() -> None:
                        # Панель целиком рисует страница. Сама форма не
                        # должна добавлять ни рамки, ни полос, ни подложки:
                        # её белая подложка и полосы прокрутки и есть тот
                        # дефект, который виден вокруг стекла. Чёрная
                        # подложка с ключом «этот цвет не рисовать» стирает
                        # и подложку, и следы прошлой отрисовки.
                        hand.FormBorderStyle = getattr(win_forms.FormBorderStyle, "None")
                        hand.AutoScroll = False
                        hand.BackColor = drawing.Color.Black
                        hand.TransparencyKey = drawing.Color.Black
                        hand.SetStyle(
                            getattr(win_forms.ControlStyles, "SupportsTransparentBackColor"),
                            True,
                        )

                    hand.Invoke(__import__("System").Action(shave))
                make_transparent(self._hwnd)
                self._park()
                self._log(f"панель: окно {self._hwnd} готово {window_state(self._hwnd)}")
        except Exception as error:  # noqa: BLE001
            self._log(f"окно панели не подготовилось: {error}")
        self._page_ready = True
        self.flush()
        self._out({"kind": "window-ready", "uid": self.uid})
        if self.kind == "panel":
            # Панель ждёт за краем экрана до первой записи. Пока страница не
            # нарисована, она не рисует ничего, а окно без движения Windows
            # не просит её перерисовать: первая запись вышла бы пустой.
            self._nudge()

    def _nudge(self) -> None:
        """Незаметно подвинуть окно, чтобы Windows попросил перерисовать."""
        if not self._hwnd:
            return
        try:
            width, height = self._size
            spot = (self._hidden_spot[0] + width, self._hidden_spot[1] + height)
            move_window(self._hwnd, spot[0], spot[1], 1, 1)
            move_window(self._hwnd, spot[0], spot[1], width, height)
            self._log("панель: страница перерисована за краем экрана")
        except Exception as error:  # noqa: BLE001
            self._log(f"панель не перерисовалась: {error}")

    def _park(self) -> None:
        """Увести окно за край экрана: место, где его никто не видит."""
        width = self._size[0]
        move_window(
            self._hwnd, self._hidden_spot[0] + width, self._hidden_spot[1] + self._size[1],
            1, 1,
        )

    def move(self, x: int, y: int, width: int | None = None, height: int | None = None) -> None:
        """Поставить окно на место, показав его.

        `window.move` и `window.resize` из движка с прозрачным окном
        загоняют его за экран, поэтому место и размер меняются прямым
        вызовом Windows. Скрытое окно при этом ещё и настраивается: с него
        снимают белый фон формы, иначе вокруг страницы видна рамка.
        """
        if width is not None and height is not None:
            self._size = (max(1, int(width)), max(1, int(height)))
        x, y = int(x), int(y)
        self._wanted_place = (x, y)
        if not self._hwnd:
            return
        try:
            cover(self._hwnd, x, y, self._size[0], self._size[1])
            self._shown = True
            self._log(f"панель: окно в {x},{y} {self._size}")
        except Exception as error:  # noqa: BLE001
            self._log(f"окно не переместилось: {error}")

    def keep_hand(self) -> Any:
        """Состояние окна промпта: его размер и место на экране."""
        return (self._hwnd, self._size)

    def run_js(self, script: str) -> Any:
        window = self._window
        if window is None:
            return None
        try:
            return window.evaluate_js(script)
        except Exception as error:  # noqa: BLE001
            self._log(f"страница не ответила: {error}")
            return None

    def flush(self) -> None:
        pending = self._pending
        self._pending = []
        for item in pending:
            value = self.run_js(item["script"])
            if value is False or (isinstance(value, str) and value.startswith("ошибка")):
                self._log(f"страница не приняла состояние: {value}")

    def command(self, name: str, payload: Any) -> None:
        """Что сделать с окном: содержимое, палитра, место."""
        if name == "js":
            script = payload
            self._pending.append({"script": script})
            self._pending = self._pending[-20:]
            # Выполняем сразу: иначе окно, стоящее за краем экрана, не
            # просит перерисовки, очередь копится и панель замирает.
            if self._page_ready:
                self.flush()
        elif name == "ate":
            x, y, width, height = payload
            self.move(int(x), int(y), int(width), int(height))
        elif name == "hide":
            if not self._shown or not self._hwnd:
                return
            width, height = self._size
            move_window(
                self._hwnd, self._hidden_spot[0] + width, self._hidden_spot[1] + height, 1, 1
            )
        elif name == "value":
            value = self.run_js(payload)
            self._out({"kind": "value", "uid": self.uid, "value": value})
        elif name == "measure":
            height = self.run_js(
                "(() => { const b = document.getElementById('bar'); "
                "return b ? Math.ceil(b.getBoundingClientRect().height) : 0; })()"
            )
            self._out({"kind": "measure", "uid": self.uid, "value": height})
        elif name == "text":
            value = self.run_js("(() => (window.EX || window.DSH).text && (window.EX || window.DSH).text())()")
            self._out({"kind": "text", "uid": self.uid, "value": value})
        elif name == "hint":
            value = self.run_js("(() => window.EX ? window.EX.hint() : '')()")
            self._out({"kind": "hint", "uid": self.uid, "value": value})
        elif name == "show":
            self.show_windowed()
        elif name == "extend":
            self.extend()
        elif name == "close":
            if self._window is not None:
                closing = self._window
                self._window = None
                closing.destroy()

    def show_windowed(self) -> None:
        """Показать окно промпта: на своей высоте, по середине, поверх окон."""
        if self.kind == "panel":
            return
        width, height = self._size
        left, top, right, bottom = work_area()
        x = left + max(0, (right - left - width) // 2)
        y = top + max(0, (bottom - top - height) // 3)
        if not self._shown:
            # Окно показано человеку впервые: дальше оно живёт на своём месте.
            self._shown = True
            self._log(f"окно промпта открыто в {x},{y} {self._size}")
        try:
            if self._hwnd:
                move_window(self._hwnd, x, y, width, height)
            else:
                self._wanted_place = (int(x), int(y))
            self._log(f"окно промпта показано в {x},{y} {self._size}")
        except Exception as error:  # noqa: BLE001
            self._log(f"окно промпта не показалось: {error}")

    def extend(self) -> None:
        """Окно на всё место между приборами и панелью задач."""
        left, top, right, bottom = work_area()
        self.move(left + 8, top + 8, right - left - 16, bottom - top - 16)

    def _await(self, limit_s: float = 20.0) -> None:
        """Дождаться своего окна от движка: оно появляется не сразу.

        Окно формы создаётся в потоке окон после страницы, и пока его нет,
        место и размер применить не к чему.
        """
        import time  # noqa: PLC0415

        deadline = time.monotonic() + limit_s
        while time.monotonic() < deadline:
            window = self._window
            form = getattr(window, "native", None) if window is not None else None
            if form is not None:
                self._form = form
                try:
                    self._hwnd = int(form.Handle.ToInt64())
                except Exception:  # noqa: BLE001
                    self._hwnd = 0
                if self._hwnd:
                    return
            time.sleep(0.05)

    def _log(self, message: str) -> None:
        self._out({"kind": "log", "value": message})


class _Door:
    """Что видит страница: `pywebview.api.control(...)` и `press()`."""

    def __init__(self, host: _Host) -> None:
        self._host = host

    def __dir__(self) -> list[str]:
        return ["control", "press", "theme", "text"]

    def theme(self) -> object:
        return None

    def text(self) -> object:
        return None

    def control(self, name: str, value: Any = None) -> bool:
        host = self._host
        if name == "ready":
            host.ready = True
            host._page_ready = True
            host.flush()
            host._out({"kind": "ready", "uid": host.uid})
        else:
            host._out({"kind": "event", "uid": host.uid, "name": name, "value": value})
        return True

    def press(self) -> bool:
        host = self._host
        if host.kind != "panel" or not host._hwnd:
            return False
        try:
            user32 = _win32()
            user32.ReleaseCapture()
            user32.SendMessageW(host._hwnd, 0x00A1, 2, 0)
        except Exception:  # noqa: BLE001
            return False
        return True


# --- системные вызовы --------------------------------------------------------


class _Rect(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


_user32: Any = None


def _win32() -> Any:
    global _user32
    if _user32 is not None:
        return _user32
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    user32.SetWindowPos.restype = ctypes.c_int
    user32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
    user32.GetWindowLongW.restype = ctypes.c_long
    user32.SetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_long]
    user32.SetWindowLongW.restype = ctypes.c_long
    user32.SetClassLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_size_t]
    user32.SetClassLongPtrW.restype = ctypes.c_size_t
    user32.SystemParametersInfoW.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
    user32.ReleaseCapture.restype = ctypes.c_int
    user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_ssize_t, ctypes.c_ssize_t]
    user32.SendMessageW.restype = ctypes.c_ssize_t
    user32.BringWindowToTop.argtypes = [ctypes.c_void_p]
    user32.BringWindowToTop.restype = ctypes.c_int
    user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
    user32.ShowWindow.restype = ctypes.c_int
    user32.SetWindowCompositionAttribute.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    user32.SetWindowCompositionAttribute.restype = ctypes.c_int
    user32.GetDpiForSystem.restype = ctypes.c_uint
    user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    user32.GetWindowRect.restype = ctypes.c_int
    _user32 = user32
    return user32


def as_number(hwnd: Any) -> int:
    """Ручка окна числом: форма движка отдаёт указатель, а не число."""
    if isinstance(hwnd, int):
        return hwnd
    to_int64 = getattr(hwnd, "ToInt64", None)
    if to_int64 is not None:
        return int(to_int64())
    return int(hwnd)


def move_window(hwnd: Any, x: int, y: int, width: int, height: int) -> bool:
    """Поставить окно на место одним вызовом.

    Ручку принимаем и указателем, и числом: число переводят на 64 разряда,
    чтобы окно не уехало при большом значении ручки.
    """
    user32 = _win32()
    target = ctypes.c_void_p(as_number(hwnd))
    return bool(
        user32.SetWindowPos(target, None, int(x), int(y), int(width), int(height), 0x0010)
    )


def window_state(hwnd: Any) -> tuple[int, int]:
    """Состояние окна: расширенный стиль и стиль, которые видит Windows."""
    user32 = _win32()
    target = ctypes.c_void_p(as_number(hwnd))
    return (int(user32.GetWindowLongW(target, -20)), int(user32.GetWindowLongW(target, -16)))


def make_transparent(hwnd: Any) -> None:
    """Прозрачное окно: своим цветом класс красит плоскую заливку.

    Пока класс красит фон сам, вокруг страницы видна белая полоса, а в
    местах, где страница прозрачная, — сплошная заливка. С альфой в цвете
    класса заливка становится прозрачной клеткой, и сквозь окно видно стол.
    Окно ещё и обязано само сообщить Windows, где оно прозрачное, иначе та
    не станет смешивать его с тем, что за ним.
    """
    user32 = _win32()
    target = ctypes.c_void_p(as_number(hwnd))
    user32.SetClassLongPtrW(target, -10, 0x01000000)
    ex_style = int(user32.GetWindowLongW(target, -20))
    user32.SetWindowLongW(
        target, -20, (ex_style | 0x00080000 | 0x08000000) & ~0x00040000
    )
    user32.SetWindowPos(target, None, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0004 | 0x0020)
    # Окно без рамки: страница занимает его целиком. Полосы прокрутки и
    # край рамки добавляла сама форма — их снимают у окна Windows.
    style = int(user32.GetWindowLongW(target, -16))
    user32.SetWindowLongW(target, -16, style & ~0x00C00000 & ~0x00040000)
    user32.SetWindowPos(target, None, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0004 | 0x0020)


def show_window(hwnd: Any, x: int, y: int, width: int, height: int) -> None:
    """Показать окно на своём месте, не забирая фокус.

    Скрытое окно Windows считает видимым для формы, пока не сдвинешь его
    старым способом: совсем скрытое окно не получает отрисовки и выходит
    пустым. Поэтому окно сдвигают вместе с вызовом показа.
    """
    user32 = _win32()
    user32.SetWindowPos(
        ctypes.c_void_p(as_number(hwnd)), ctypes.c_void_p(-1), int(x), int(y),
        int(width), int(height), 0x0004 | 0x0010 | 0x0040,
    )


def cover(hwnd: Any, x: int, y: int, width: int, height: int) -> None:
    """Окно под страницу, а страница под окно: сперва окно поменьше.

    Движок пересобирает страницу по новому размеру окна только после
    невозвратного события окна, то есть когда окно станет больше. Если
    сразу сделать окно меньше, страница дольше живёт в старом размере: в
    живом окне это видно по обрезанной подписи. Поэтому окно чуть
    уменьшают, затем ставят ровно.
    """
    user32 = _win32()
    target = ctypes.c_void_p(as_number(hwnd))
    user32.SetWindowPos(target, None, int(x), int(y), int(width) - 1, int(height) - 1,
                        0x0004 | 0x0010 | 0x0020)
    user32.SetWindowPos(target, None, int(x), int(y), int(width), int(height),
                        0x0004 | 0x0010 | 0x0020)
    user32.RedrawWindow.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
    user32.RedrawWindow.restype = ctypes.c_int
    user32.RedrawWindow(target, None, None, 0x0001 | 0x0004 | 0x0100 | 0x0080)
    user32.UpdateWindow.argtypes = [ctypes.c_void_p]
    user32.UpdateWindow.restype = ctypes.c_int
    user32.UpdateWindow(target)


def reflow(hwnd: Any, x: int, y: int, width: int, height: int) -> None:
    """Сдвинуть окно невидимым для глаза вызовом и прогнать очередь окон.

    После сдвига движок пересобирает страницу только когда очередь окон
    свободна. Эти два вызова и отдают ему очередь.
    """
    user32 = _win32()
    user32.SetWindowPos(
        ctypes.c_void_p(as_number(hwnd)), None, int(x), int(y), int(width), int(height),
        0x0004 | 0x0010 | 0x0020,
    )
    user32.RedrawWindow.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
    user32.RedrawWindow.restype = ctypes.c_int
    user32.RedrawWindow(
        ctypes.c_void_p(as_number(hwnd)), None, None, 0x0001 | 0x0004 | 0x0100 | 0x0080
    )
    user32.UpdateWindow.argtypes = [ctypes.c_void_p]
    user32.UpdateWindow.restype = ctypes.c_int
    user32.UpdateWindow(ctypes.c_void_p(as_number(hwnd)))


def enable_blur(hwnd: Any) -> None:
    """Размытие того, что за окном.

    Вызовов два: первый ставит размытие, второй снимает его и ставит заново.
    На этой Windows повторное включение без снятия оставляет окно без стекла.
    """
    class AccentPolicy(ctypes.Structure):
        _fields_ = [
            ("AccentState", ctypes.c_int),
            ("AccentFlags", ctypes.c_int),
            ("GradientColor", ctypes.c_uint),
            ("AnimationId", ctypes.c_int),
        ]

    class WindowComposition(ctypes.Structure):
        _fields_ = [
            ("Attribute", ctypes.c_int),
            ("Data", ctypes.c_void_p),
            ("SizeOfData", ctypes.c_size_t),
        ]

    user32 = _win32()
    target = ctypes.c_void_p(as_number(hwnd))
    for state in (3, 3):
        policy = AccentPolicy(state, 2, 0, 0)
        data = WindowComposition(19, ctypes.addressof(policy), ctypes.sizeof(policy))
        user32.SetWindowCompositionAttribute(target, ctypes.byref(data))


def window_rect(hwnd: Any) -> tuple[int, int, int, int]:
    rect = _Rect()
    _win32().GetWindowRect(ctypes.c_void_p(as_number(hwnd)), ctypes.byref(rect))
    return (rect.left, rect.top, rect.right, rect.bottom)


def screen_scale() -> float:
    try:
        return max(1.0, min(4.0, _win32().GetDpiForSystem() / 96.0))
    except Exception:  # noqa: BLE001
        return 1.0


def work_area() -> tuple[int, int, int, int]:
    rect = _Rect()
    try:
        _win32().SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)
    except Exception:  # noqa: BLE001
        return (0, 0, 1920, 1080)
    if rect.right <= rect.left:
        return (0, 0, 1920, 1080)
    return (rect.left, rect.top, rect.right, rect.bottom)


# --- процесс -------------------------------------------------------------------


_lane: "Lane | None" = None
_dispatchers: dict[str, Callable[[str, Any], None]] = {}


def _dispatch(uid: str, name: str, value: Any) -> None:
    handler = _dispatchers.get(uid)
    if handler is not None:
        handler(name, value)


class Page:
    """Страница в процессе окон, как её видит диктовка."""

    def __init__(self, kind: str, on_event: Callable[[str, Any], None]) -> None:
        self.kind = kind
        self.uid = kind
        self._on_event = on_event
        self._lane: Lane | None = None
        self.ok = False

    def execute(self, script: str) -> None:
        if self._lane is not None:
            self._lane.execute(self.uid, script)

    def ask(self, name: str) -> None:
        if self._lane is not None:
            self._lane.call(self.uid, name)

    def place(self, x: int, y: int, width: int, height: int) -> None:
        if self._lane is not None:
            self._lane.call(self.uid, "ate", [x, y, width, height])

    def hide(self) -> None:
        if self._lane is not None:
            self._lane.call(self.uid, "hide")

    def show_windowed(self) -> None:
        if self._lane is not None:
            self._lane.call(self.uid, "show")

    def extend(self) -> None:
        if self._lane is not None:
            self._lane.call(self.uid, "extend")

    def ask_value(self, script: str) -> Any:
        """Спросить страницу и дождаться ответа. Окна нет — ответа нет."""
        if self._lane is None:
            return None
        return self._lane.ask_value(self.uid, script)

    def close(self) -> None:
        if self._lane is not None:
            self._lane.close(self.uid)

    def _handle(self, name: str, value: Any) -> None:
        try:
            self._on_event(name, value)
        except Exception as error:  # noqa: BLE001
            _log_line(f"страница сказала «{name}», а разобрать не вышло: {error}")


def make_page(kind: str, on_event: Callable[[str, Any], None]) -> Page:
    """Поднять страницу интерфейса. Не вышло — причина в журнале, панели нет."""
    global _lane
    page = Page(kind, on_event)
    if not available():
        return page
    lane = _lane
    if lane is None or not lane.alive():
        lane = Lane(_dispatch, _log_line)
        summary = lane.start()
        _lane = lane
        for name, ok in summary.items():
            if not ok:
                _log_line(f"страница «{name}» не поднялась в процессе окон")
    page._lane = lane
    _dispatchers[kind] = page._handle
    page.ok = lane.alive()
    return page


def _lane_main(to_lane: Any, from_lane: Any, summary: Any, logger: Any) -> None:
    hosts: dict[str, _Host] = {}
    webview: Any = None
    try:
        if not _runtime_present():
            _put(from_lane, "log", "", "WebView2 не установлен: окна интерфейса не покажутся")
            summary["panel"] = False
            summary["editor"] = False
            _put(from_lane, "ready", "")
            return
        webview = importlib.import_module("webview")
        for kind in PAGES:
            try:
                host = _Host(kind, kind, lambda msg, q=from_lane: q.put(msg))
                host.create(webview)
                hosts[kind] = host
                summary[kind] = True
            except Exception as error:  # noqa: BLE001
                summary[kind] = False
                _put(from_lane, "log", "", f"окно «{kind}» не создалось: {error}")
    except Exception as error:  # noqa: BLE001
        _put(from_lane, "log", "", f"движок окон не поднялся: {error}\n{traceback.format_exc()}")
        summary.setdefault("panel", False)
        summary.setdefault("editor", False)
        _put(from_lane, "ready", "")
        return
    _put(from_lane, "ready", "")
    _serve(webview, hosts, to_lane, from_lane)


def _serve(webview: Any, hosts: dict[str, _Host], to_lane: Any, from_lane: Any) -> None:
    stop = threading.Event()

    def pump() -> None:
        while not stop.is_set():
            try:
                message = to_lane.get(timeout=0.2)
            except Exception:  # noqa: BLE001
                continue
            if message.get("kind") != "call":
                continue
            payload = message.get("value") or {}
            uid = payload.get("uid")
            name = message.get("name")
            host = hosts.get(uid)
            if host is None:
                continue
            try:
                host.command(str(name), payload.get("value"))
            except Exception as error:  # noqa: BLE001
                _put(from_lane, "log", "", f"окно «{uid}»: {name}: {error}")

    reader = threading.Thread(target=pump, daemon=True, name="ui-lane-pump")
    reader.start()
    try:
        webview.start(gui="edgechromium", debug=False, private_mode=True)
    finally:
        stop.set()


def _runtime_present() -> bool:
    import os  # noqa: PLC0415

    if os.environ.get("WEBVIEW2_RUNTIME_PATH"):
        return True
    try:
        import winreg  # noqa: PLC0415

        key = r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as handle:
            return bool(winreg.QueryValueEx(handle, "pv")[0])
    except (OSError, ImportError):
        return False


if __name__ == "__main__":
    multiprocessing.freeze_support()
