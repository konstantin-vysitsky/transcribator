"""Панель диктовки: рисуется в большом размере и уменьшается при показе.

Tk рисует только без сглаживания, и на экранах с масштабом 125–150%
скругления и углы букв выглядели ступеньками, как в низком разрешении.
Поэтому здесь вся панель рисуется картинкой через Pillow: рисунок делается
в два раза крупнее нужного и уменьшается с хорошим сглаживанием. На экране
появляется уже готовое изображение.

Живут так не только корпус и кнопки, но и текст: он нарисован той же
картинкой, а значит идёт с тем же сглаживанием, что и формы.
"""
from __future__ import annotations

import ctypes
import math
import sys
import threading
import time
import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from PIL import Image, ImageDraw, ImageFont, ImageTk

from dictation import panel_style

ActivityState = Literal["idle", "recording", "transcribing", "processing"]

# Все размеры ниже — в точках экрана, как в макете HTML. На экране с
# масштабом 125% панель вырастает вместе с системой.
_PANEL_W = 396
_PANEL_PAD_X = 16
_PANEL_PAD_Y = 14
_CONTENT_W = 364
_RADIUS = 20          # у чёрного стекла — 24
_STATUS_ROW_H = 34
_DONE_W = 64
_DONE_H = 32
_ABORT = 32
_DOT_SLOT = 20
_DOT = 10
_SWITCH_H = 34
_SWITCH_PAD_R = 12
_TRACK_W = 44
_TRACK_H = 24
_KNOB = 18
_TRACK_PAD = 3
_GAP = 8
_SWITCH_ROW_TOP = 12
_DIVIDER_GAP_TOP = 10
_DIVIDER_GAP_BOTTOM = 12
_MARGIN_X = 16
_MARGIN_Y = 48
_DRAG_BORDER = 3
_DRAG_THRESHOLD = 3

_SUPERSAMPLE = 2      # во сколько раз крупнее рисунок в движении: сглаживание в четверть пикселя
_SUPERSAMPLE_MAX = 3  # больше не растем: память растёт квадратом
_RENDER_DELAY_MS = 8  # наведение и нажатие: рисунок собирается не на каждый пиксель мыши
_PULSE_MS = 33
_PULSE_PEAK_MS = 120
_PULSE_BEAT_S = 1.1
# Полоса хода работы: заливка подходит к новой доле за _EASE_MS, а пока
# идёт обработка, по ней бежит светлая волна — движение видно даже когда
# доля не меняется (модель думает, а время не идёт).
_BAR_H = 10
_BAR_MS = 40
_EASE_MS = 160
_EASE_STEPS = 6
_SHEEN_STRIP = 72
_STAGE_CLEAR = 9    # место вокруг полосы: мягкое пятно у её конца не должно срезаться
_COLOR_STEPS = 8
_COLOR_MS = 20

_ICON_ABORT = "\ue711"
_ICON_FALLBACK = "\u2715"

_FONT_BODY = ("C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/segoeui.ttf")
_FONT_SMALL = ("C:/Windows/Fonts/segoeuisb.ttf", "C:/Windows/Fonts/segoeui.ttf")
_FONT_ICONS = ("C:/Windows/Fonts/SegoeIcons.ttf", "C:/Windows/Fonts/seguisym.ttf")

_SHEEN_MS = 1600  # сколько свет идёт от начала полосы до конца


def _seconds_note(seconds: float) -> str:
    """Сколько длилась обработка: «ИИ · 3,2 с»."""
    return f"· {seconds:.1f} с".replace(".", ",")


# --- цвета -------------------------------------------------------------------


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    # Прозрачность в этой работе не нужна: восьмизначный цвет даёт старшие шесть.
    color = color.lstrip("#")[:6]
    return (int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16))


def _rgb_to_hex(rgb: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{max(0, min(255, round(c))):02x}" for c in rgb)


def mix(color_a: str, color_b: str, t: float) -> str:
    """Смешать два цвета: t=0 — первый, t=1 — второй."""
    t = max(0.0, min(1.0, t))
    a = _hex_to_rgb(color_a)
    b = _hex_to_rgb(color_b)
    return _rgb_to_hex(
        (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t)
    )


def _hex_rgba(color: str) -> tuple[int, int, int, int]:
    """Цвет с прозрачностью: восемь знаков — своя, шесть — сплошной."""
    value = str(color).lstrip("#")
    r, g, b = _hex_to_rgb(value[:6])
    a = int(value[6:8], 16) if len(value) >= 8 else 255
    return (r, g, b, a)


def _solid(color: str) -> str:
    """Цвет без прозрачности: Tk восьмизначный не понимает."""
    return "#" + str(color).lstrip("#")[:6]


def _rgba(color: str, alpha: float) -> tuple[int, int, int, int]:
    r, g, b = _hex_to_rgb(color)
    return (r, g, b, max(0, min(255, round(alpha * 255))))


# --- шрифты ------------------------------------------------------------------


@dataclass(frozen=True)
class _Fonts:
    body: tuple[ImageFont.FreeTypeFont | ImageFont.ImageFont, int]
    small: tuple[ImageFont.FreeTypeFont | ImageFont.ImageFont, int]
    title: tuple[ImageFont.FreeTypeFont | ImageFont.ImageFont, int]
    icon: tuple[ImageFont.FreeTypeFont | ImageFont.ImageFont, int]
    tiny: tuple[ImageFont.FreeTypeFont | ImageFont.ImageFont, int]


def _load_font(paths: tuple[str, ...], size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in paths:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _fonts(scale: float, supersample: int = _SUPERSAMPLE_MAX) -> _Fonts:
    def size(points: float) -> int:
        return max(8, round(points * scale * supersample))

    return _Fonts(
        body=(_load_font(_FONT_BODY, size(9.5)), size(9.5)),
        small=(_load_font(_FONT_SMALL, size(9)), size(9)),
        title=(_load_font(_FONT_BODY, size(10.5)), size(10.5)),
        icon=(_load_font(_FONT_ICONS, size(10)), size(10)),
        tiny=(_load_font(_FONT_SMALL, size(8.5)), size(8.5)),
    )


def _icon_char() -> str:
    try:
        font = ImageFont.truetype(_FONT_ICONS[0], 16)
        mask = font.getmask(_ICON_ABORT)
        bbox = mask.getbbox() if hasattr(mask, "getbbox") else None
        if bbox:
            return _ICON_ABORT
    except OSError:
        pass
    return _ICON_FALLBACK


def _text_size(
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont, text: str
) -> tuple[int, int]:
    if isinstance(font, ImageFont.FreeTypeFont):
        left, top, right, bottom = font.getbbox(text)
        return right - left, bottom - top
    return font.getsize(text)


# --- кнопки и ползунки: их состояния ----------------------------------------


@dataclass
class _ButtonState:
    text: str
    fill: str
    fg: str
    border: str
    shadow: str = ""
    radius: int = 8
    w: int = 0
    h: int = 0
    icon: bool = False


class _SwitchState:
    def __init__(self, text: str, secondary: bool) -> None:
        self.text = text
        self.secondary = secondary
        self.progress = 1.0   # от выключенного к включённому: 0..1
        self.hover = False


def _is_panel_background(widget: tk.Misc, panel: "StatusPanel") -> bool:
    """Можно ли тащить панель, схватившись за этот виджет.

    Панель таскается за любое место, кроме кнопок. Перечислять классы
    виджетов нельзя: в живом приложении клик приходит то в Canvas, то в
    Frame, то в Label — набор зависит от того, что нарисовано под курсором.
    Поэтому проверяем обратное: виджет не принадлежит ни одной кнопке.
    """
    if sys.platform != "win32":
        return False
    for button in (
        panel._auto_enter_btn,
        panel._stop_btn,
        panel._abort_btn,
        panel._ai_btn,
    ):
        if button is None:
            continue
        try:
            if widget is button or str(widget).startswith(str(button)):
                return False
        except tk.TclError:
            continue
    return True


def _transparency_on() -> bool:
    """Включена ли прозрачность в оформлении Windows.

    Это настройка человека, а не наша: при выключенной Windows не рисует
    размытие и не делает окна полупрозрачными. Спрашиваем при каждой
    проверке — её меняют и ждут, что панель пойдёт следом.
    """
    if sys.platform != "win32":
        return False
    try:
        import winreg  # noqa: PLC0415

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "EnableTransparency")
        return bool(int(value))
    except (OSError, ValueError, ImportError):
        return False


class _GlassWindow:
    """Полупрозрачное окно: то, что за панелью, видно сквозь её цвета.

    Tk рисует непрозрачный прямоугольник. Windows умеет больше: окну с
    особым стилем (без рамки, слоёное) ставится прозрачность, и тогда цвет
    окна перестаёт быть стеной — сквозь него виден рабочий стол. Форму окна
    при этом задаёт сам рисунок: там, где у рисунка прозрачно, окна нет.

    Прозрачность у окна общая на всю панель, поэтому каждый цвет палитры
    написан восемью знаками: шесть — цвет, два — сколько сквозь него видно.
    Так фон панели выходит прозрачным, а её края — мягкими, без ступенек.

    Размытия фона здесь нет: у Windows оно поднимает каждую прозрачную клетку
    рисунка до плотного фона и стирает всю прозрачность. Зато без него видно
    рабочий стол, а не средний цвет под окном.
    """

    def __init__(self) -> None:
        self.ok = False
        self.reason = ""
        self._user32 = None
        self._wintypes = None
        self._hwnd = 0
        self._installed = False

    def install(self, window: tk.Toplevel) -> bool:
        if sys.platform != "win32":
            self.reason = "не Windows"
            return False
        try:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
        except OSError as error:
            self.reason = f"системные вызовы не нашлись: {error}"
            return False
        from ctypes import wintypes  # noqa: PLC0415

        self._user32 = user32
        self._wintypes = wintypes
        self._setup_calls()
        try:
            window.update_idletasks()
            # Виджет Tk — не окно верхнего уровня: сперва находим его окно.
            self._hwnd = int(user32.GetAncestor(wintypes.HWND(int(window.winfo_id())), 2))
        except Exception as error:  # noqa: BLE001
            self.reason = f"окно не нашлось: {error}"
            return False
        self._installed = True
        if not _transparency_on():
            self.reason = "прозрачность выключена в Windows"
            return False

        self.ok = self._apply()
        if not self.ok:
            self.reason = self.reason or "система отказала"
        return self.ok

    def _setup_calls(self) -> None:
        """Размеры и формы вызовов: 32-битные числа и указатели надо объявлять."""
        user32, w = self._user32, self._wintypes
        user32.GetAncestor.restype = w.HWND
        user32.GetAncestor.argtypes = [w.HWND, ctypes.c_uint]
        user32.GetWindowLongW.restype = ctypes.c_long
        user32.GetWindowLongW.argtypes = [w.HWND, ctypes.c_int]
        user32.SetWindowLongW.restype = ctypes.c_long
        user32.SetWindowLongW.argtypes = [w.HWND, ctypes.c_int, ctypes.c_long]

    def reapply(self) -> bool:
        """Поставить стиль окна заново после показа: показанное окно его теряет."""
        if not self.ok:
            return False
        try:
            window = self._wintypes.HWND(self._hwnd)
            self._user32.SetWindowLongW(window, -20,
                                        self._user32.GetWindowLongW(window, -20) | 0x00080000)
            self._user32.SetWindowLongW(window, -16,
                                        self._user32.GetWindowLongW(window, -16))
            return True
        except Exception:  # noqa: BLE001
            return False

    def _apply(self) -> bool:
        w = self._wintypes
        window = w.HWND(self._hwnd)
        try:
            style = self._user32.GetWindowLongW(window, -16)
            # Tk запоминает стиль окна сам и при следующей настройке вернёт
            # старый: подгоняем оба, иначе прозрачность слетит. Слоёное окно
            # нужно, чтобы альфа окна вообще действовала.
            extended = self._user32.GetWindowLongW(window, -20)
            self._user32.SetWindowLongW(window, -16, (style | 0x00200000) & ~0x00080000)
            self._user32.SetWindowLongW(window, -20, (extended | 0x00080000) & ~0x00000080)
        except Exception as error:  # noqa: BLE001
            self.reason = f"стиль окна: {error}"
            return False
        return True


def _rounded(d: ImageDraw.ImageDraw, box: tuple[float, float, float, float],
             radius: float, fill: Any = None, outline: Any = None,
             width: int = 1) -> None:
    """Прямоугольник со скруглением. Координаты — в точках рисунка."""
    d.rounded_rectangle(box, radius=max(0, radius), fill=fill, outline=outline, width=width)


class StatusPanel:
    def __init__(
        self,
        on_abort: Callable[[], None] | None = None,
        on_stop_recording: Callable[[], None] | None = None,
        on_toggle_ai: Callable[[bool], None] | None = None,
        ai_enabled: bool = False,
        ai_ready: bool = True,
        *,
        style: str | None = None,
        theme: str | None = None,
        scale: float = 0.0,
    ) -> None:
        self._on_abort = on_abort
        self._on_stop_recording = on_stop_recording
        # Имена с «notify» намеренно: обработчики, названные как методы класса
        # (`_on_toggle_ai`), затирают эти методы собой, и кнопка начинает звать
        # не панель, а внешний код — с чужими аргументами.
        self._notify_toggle_ai = on_toggle_ai
        self._root: tk.Tk | None = None
        self._window: tk.Toplevel | None = None
        self._canvas: tk.Canvas | None = None
        self._image_item: int | None = None
        self._photo: ImageTk.PhotoImage | None = None
        self._image: Image.Image | None = None
        self._glass = _GlassWindow()

        # Границы нажатий в точках экрана: по ним определяем, куда пришёл клик.
        self._hit_stop: tuple[int, int, int, int] | None = None
        self._hit_abort: tuple[int, int, int, int] | None = None
        self._hit_auto: tuple[int, int, int, int] | None = None
        self._hit_ai: tuple[int, int, int, int] | None = None

        self._stop_btn = _ButtonState("Готово", "", "", "")
        self._abort_btn = _ButtonState("", "", "", "", icon=True)
        self._auto_enter_btn = _SwitchState("Автоотправка", False)
        self._ai_btn = _SwitchState("Обработка ИИ", True)

        # `_ai_ready` — настроена ли обработка. Включённый ползунок при пустом
        # ключе выглядит как рабочий, а текст вставляется необработанным:
        # человек видит это и решает, что сломано приложение. Надпись на
        # ползунке должна говорить, что ещё не готово.
        self._ai_ready = ai_ready
        self._ai_last_error: str | None = None
        self._ai_btn.text = "ИИ не настроен" if not ai_ready else ("ИИ выкл" if not ai_enabled else "Обработка ИИ")

        # Вид и тема. Значения приходят из настроек; здесь только проверка
        # на случай, если настройки позвали напрямую.
        self._theme_choice = panel_style.normalize_theme(theme)
        self._style = panel_style.normalize_style(style)
        self._manual_scale = float(scale or 0.0)
        self._system_theme = panel_style.system_theme()
        self._alive = True
        self._ss = _SUPERSAMPLE
        self._fonts = _fonts(1.0)
        self._palette: dict[str, Any] = {}
        self._icon = _icon_char()
        self._recompute_palette()

        self._state: ActivityState = "idle"
        self._detail = ""
        # Четыре доли хода. `_progress` — то, что сказал вызывающий код,
        # `_shown` — то, что видно: оно подходит к цели плавно.
        self._progress = 0.0
        self._shown = 0.0
        self._ai_progress = 0.0
        self._ai_shown = 0.0
        self._ai_note = ""
        self._ai_elapsed = 0.0
        self._ai_started = time.monotonic()
        self._transcribe_done = False
        self._ai_result: bool | None = None  # True — вышло, False — нет
        self._bar_started = time.monotonic()
        self._bar_job: str | None = None
        self._hover_auto = False
        self._hover_ai = False
        self._hover_stop = False
        self._hover_abort = False
        self._pressed_stop_until = 0.0
        self._pressed_abort_until = 0.0
        self._pulse_started = 0.0
        self._render_job: str | None = None
        self._pulse_job: str | None = None
        self._tween_job: str | None = None
        self._bitmap_anim_job: str | None = None
        self._bitmap_anim_until = 0.0

        self._drag_active = False
        self._drag_press: tuple[int, int] | None = None
        self._drag_offset = (0, 0)
        self._drag_moved = False
        self._flash_until = 0.0

        self._skip_auto_enter = threading.Event()
        # `_alive` гаснет при остановке: после выхода из цикла окна очередь
        # Tk закрыта, а любой запрос в неё пишет ошибку в журнал.
        self._alive = True
        # Одно состояние на ползунок и на решение в main.py. Отдельного
        # «пропуска на сессию» нет: нажатие по ползунку пишется в настройки
        # и живёт до следующего нажатия, а не до перезапуска.
        self._ai_enabled = ai_enabled
        self._ai_default_enabled = ai_enabled

        self._ready = threading.Event()
        self._size: tuple[int, int] = (_PANEL_W, 0)
        threading.Thread(target=self._run, daemon=True, name="status-panel").start()
        self._ready.wait(timeout=5.0)

    # --- масштаб и палитра --------------------------------------------------

    def _spec(self, value: float) -> int:
        return max(1, round(value * self._scale))

    @property
    def _scale(self) -> float:
        """Во сколько раз панель крупнее макета, точка в точку.

        Ноль означает «как в макете»: 396 точек шириной, текст 9,5 пункта.
        Tk при этом дюйм считает равным 72 точкам, а Windows — 96, поэтому за
        основу берётся не `tk_scaling`, а частота точек экрана из
        `winfo_fpixels`: она уже учитывает системное увеличение, и на 125%
        панель выйдет крупнее, на 100% — ровно как в макете.

        Масштаб из настроек (`panel_scale`) главнее: 0 — как считает система,
        иначе ровно заданное число. Текст живёт в тех же единицах, что и
        клетка рисунка, поэтому при любом масштабе он выглядит одинаково
        чётко — просто крупнее или мельче.
        """
        manual = float(getattr(self, "_manual_scale", 0.0) or 0.0)
        if manual > 0:
            return max(0.6, min(3.0, manual))
        root = self._root
        if root is None:
            return 1.0
        try:
            ppi = float(root.winfo_fpixels("1i"))
        except (tk.TclError, ValueError):
            return 1.0
        # winfo_fpixels при системном масштабе 125% даёт 120, при 150% — 144.
        return max(1.0, min(3.0, round(ppi / 96.0, 2)))

    @property
    def palette(self) -> dict[str, Any]:
        return self._palette

    @property
    def style(self) -> str:
        return self._style

    @property
    def theme_choice(self) -> str:
        return self._theme_choice

    def _icon_font_available(self) -> bool:
        return self._icon == _ICON_ABORT

    def _recompute_palette(self) -> None:
        resolved = panel_style.resolve_theme(self._theme_choice, self._system_theme)
        colors = panel_style.palette(self._style, resolved)  # type: ignore[arg-type]
        r, g, b = _hex_to_rgb(str(colors["bg"]))
        colors["__luma"] = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255
        self._palette = colors
        # Цвет окна под рисунком — тоже палитра вида и темы: при смене вида
        # он должен смениться вместе с рисунком. У стекла окно служит только
        # подложкой под картинку.
        window = getattr(self, "_window", None)
        canvas = getattr(self, "_canvas", None)
        if window is not None and canvas is not None and not self._glass.ok:
            try:
                window.configure(bg=self._window_color())
                canvas.configure(bg=self._window_color())
            except tk.TclError:
                pass

    def _light(self) -> bool:
        return float(self._palette.get("__luma", 0)) > 0.5

    # --- цвета состояний ----------------------------------------------------

    def _stop_colors(self, active: bool) -> tuple[str, str, str, str]:
        pal = self._palette
        return (
            mix(str(pal["done_bg"]), "#ffffff", 0.10) if active else str(pal["done_bg"]),
            str(pal["done_fg"]),
            str(pal["done_border"]),
            str(pal.get("done_shadow", "")) if self._light() else "",
        )

    def _abort_colors(self, active: bool) -> tuple[str, str, str]:
        pal = self._palette
        if active:
            return str(pal["icon_hover_bg"]), str(pal["icon_hover_fg"]), str(pal["icon_hover_bg"])
        return str(pal["icon_bg"]), str(pal["icon_fg"]), str(pal["icon_border"])

    def _switch_colors(self, secondary: bool, t: float, hover: bool) -> dict[str, str]:
        pal = self._palette
        if secondary and "sw2_on_track" in pal:
            bg_off, bg_on = pal["sw_off_bg"], pal["sw2_on_bg"]
            border_off, border_on = pal["sw_off_border"], pal["sw2_on_border"]
            fg_off, fg_on = pal["sw_off_fg"], pal["sw2_on_fg"]
            track_off, track_a = pal["sw_off_track"], pal["sw2_on_track"]
            track_b = pal.get("sw2_on_track_end", track_a)
            knob_off, knob_on = pal["sw_off_knob"], pal["sw2_on_knob"]
        else:
            bg_off, bg_on = pal["sw_off_bg"], pal["sw_on_bg"]
            border_off, border_on = pal["sw_off_border"], pal["sw_on_border"]
            fg_off, fg_on = pal["sw_off_fg"], pal["sw_on_fg"]
            track_off, track_a = pal["sw_off_track"], pal["sw_on_track"]
            track_b = pal.get("sw_on_track_end", track_a)
            knob_off, knob_on = pal["sw_off_knob"], pal["sw_on_knob"]
        if hover:
            border_on = mix(str(border_on), str(fg_on), 0.35)
        return {
            "bg": mix(str(bg_off), str(bg_on), t),
            "border": mix(str(border_off), str(border_on), t),
            "fg": mix(str(fg_off), str(fg_on), t),
            "track": mix(str(track_off), mix(str(track_a), str(track_b), t), t),
            "knob": mix(str(knob_off), str(knob_on), t),
        }

    # --- построение окна ----------------------------------------------------

    def _run(self) -> None:
        root = tk.Tk()
        root.withdraw()
        self._root = root
        scale = self._scale
        self._fonts = _fonts(scale)
        self._recompute_palette()

        window = tk.Toplevel(root)
        window.withdraw()
        window.overrideredirect(True)
        window.attributes("-topmost", True)
        window.configure(bg=self._window_color())
        window.bind("<Button-1>", self._on_mouse_down, add="+")
        window.bind("<B1-Motion>", self._on_drag_move, add="+")
        window.bind("<ButtonRelease-1>", self._on_mouse_up, add="+")
        window.bind("<Motion>", self._on_motion, add="+")
        window.bind("<Leave>", lambda _e: self._set_hover(None), add="+")
        self._window = window

        # Стекло: окно становится полупрозрачным, и цвета рисунка читаются
        # как полупрозрачные. Не вышло — панель остаётся плотной, как раньше:
        # рисунок тогда сплющивается, а окно держит скруглённый край регионом.
        self._glass = _GlassWindow()
        self._glass.install(window)

        # Со стеклом цвет холста — сам рисунок: панель станет видна только
        # там, где у рисунка есть цвет, а пустые клетки по краям пропадут.
        canvas = tk.Canvas(window, highlightthickness=0, bd=0,
                           bg=self._window_color())
        canvas.pack()
        self._canvas = canvas

        self._size = (self._spec(_PANEL_W), self._spec(170))
        canvas.configure(width=self._size[0], height=self._size[1])

        self._render()
        self._ready.set()
        root.after(1500, self._watch_system_theme)
        root.mainloop()

    # --- рисование ----------------------------------------------------------

    def _layout(self) -> dict[str, int]:
        """Высоты строк панели в точках экрана. От режима зависит только низ."""
        pad_x = self._spec(_PANEL_PAD_X)
        pad_y = self._spec(_PANEL_PAD_Y)
        content_w = self._spec(_CONTENT_W)
        status_h = max(self._spec(_STATUS_ROW_H), self._spec(_DONE_H) + self._spec(2))
        y = pad_y + status_h
        out = {
            "pad_x": pad_x,
            "pad_y": pad_y,
            "content_w": content_w,
            "status_h": status_h,
            "status_top": pad_y,
            "body_top": y,
        }
        if self._state == "recording":
            divider_y = y + self._spec(_DIVIDER_GAP_TOP + _DIVIDER_GAP_BOTTOM)
            out["divider_y"] = divider_y
            out["switch_top"] = divider_y + self._spec(_DIVIDER_GAP_BOTTOM)
            out["switch_h"] = self._spec(_SWITCH_H)
            out["bottom"] = out["switch_top"] + out["switch_h"] + pad_y
        else:
            # Две ступени хода. У второй сверху и снизу есть место под мягкое
            # пятно: без него свечение срезалось бы краем полосы.
            clear = self._spec(_STAGE_CLEAR)
            gap = self._spec(3)
            bar_h = self._spec(_BAR_H)
            out["stage1_top"] = y + clear
            out["stage2_top"] = y + clear + bar_h + gap + clear * 2
            out["bar_h"] = bar_h
            out["progress_top"] = out["stage1_top"]
            out["progress_h"] = bar_h
            out["detail_top"] = out["stage2_top"] + bar_h + clear + self._spec(6)
            out["detail_h"] = self._spec(16) if self._detail else 0
            out["bottom"] = out["detail_top"] + out["detail_h"] + pad_y
        return out

    def _render(self) -> None:
        """Собрать рисунок панели и показать уменьшенным.

        Ошибку Tk здесь гасим не по лени: панель живёт в своём потоке, и
        исключение из него пишется в журнал как поломка всего приложения.
        Окно при этом просто остаётся прежним.
        """
        if self._root is None or self._canvas is None:
            return
        try:
            self._render_inner()
        except (tk.TclError, RuntimeError):
            return

    def _render_inner(self) -> None:
        pal = self._palette
        fonts = self._fonts
        lay = self._layout()
        w = self._spec(_PANEL_W)
        h = lay["bottom"]
        self._size = (w, h)

        # Сглаживание рисунка. При смене вида и темы окно не перерисовывается
        # каждый кадр, а мигание точки — кадр за кадром, поэтому крупный
        # рисунок берём для неподвижных кадров и полегче — для анимации.
        ss = _SUPERSAMPLE
        if self._state != "recording" or not self._pulse_job:
            ss = min(_SUPERSAMPLE_MAX, ss + 1)
        self._ss = ss
        self._fonts = _fonts(self._scale, ss)

        # Рисунок с прозрачностью: корпус сам решает, сколько сквозь него видно.
        # Без стекла та же картинка сплющивается в плотную: см. ниже.
        img = Image.new("RGBA", (w * ss, h * ss), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        radius = self._spec(_RADIUS)
        if self._style == "black":
            radius = round(radius * 1.2)
        r2 = radius * ss
        w2 = w * ss
        h2 = h * ss
        bg = str(pal["bg"])
        border = str(pal["border"])

        # Корпус: фон и рамка. Скругление рисуется сразу в крупном размере —
        # это и есть то сглаживание, которого не даёт Tk.
        # Прозрачность корпуса записана в его цвет восемью знаками: своя
        # прозрачность клетки плюс прозрачность окна и дают стекло. Без
        # стекла прозрачность клеток сплющивается на плотный цвет — как раньше.
        body = _hex_rgba(bg) if self._glass.ok else (*_hex_rgba(bg)[:3], 255)
        d.rounded_rectangle((0, 0, w2 - 1, h2 - 1), radius=r2, fill=body)
        # Свет сверху: полупрозрачная полоса от верха корпуса. Заливать её
        # непрозрачным цветом нельзя — панель вышла бы двухцветной.
        overlay = Image.new("RGBA", (w2, h2), (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        od.rounded_rectangle(
            (2 * ss, ss, w2 - 2 * ss, int(h2 / 2.2)),
            radius=max(2, r2 - 2 * ss),
            fill=_rgba(str(pal.get("top_glow", bg)), 0.5),
        )
        img.paste(overlay, (0, 0), overlay)
        d.rounded_rectangle(
            (0, 0, w2 - 1, h2 - 1), radius=r2, outline=border, width=ss
        )
        if self._style == "smoke":
            # Кромка идёт по высоте корпуса. Верх и низ скругляются по тому
            # же радиусу, что и корпус: иначе снаружи торчали бы прямые углы.
            top = lay["pad_y"]
            bottom = h - lay["pad_y"]
            bar_w = max(2, self._spec(3))
            mid = (top + bottom) // 2
            self._accent_bar(d, (0, top, bar_w, mid), str(pal["accent_left"]),
                             top_round=True, bottom_round=False)
            self._accent_bar(d, (0, mid, bar_w, bottom), str(pal["accent_right"]),
                             top_round=False, bottom_round=True)

        # Хром между строками.
        if self._state == "recording":
            dy = lay["divider_y"]
            d.line((lay["pad_x"] * ss, dy * ss, (w - lay["pad_x"]) * ss, dy * ss),
                   fill=border, width=ss)

        s = ss
        px = lay["pad_x"]

        # Строка состояния.
        cx = px
        cy = lay["status_top"] + lay["status_h"] // 2
        if self._state == "recording":
            # Точка с волнами и ореолом рисуется отдельным слоем с
            # прозрачностью: иначе ореол закрасил бы фон квадратом.
            slot = self._spec(_DOT_SLOT)
            layer = self._dot_layer(px + slot // 2, cy)
            if layer is not None:
                img.paste(layer, (px, cy - slot // 2), layer)
            cx = px + slot + self._spec(4)
        title = {
            "recording": "Запись — говорите",
            "transcribing": "Распознаю…",
            "processing": "Обрабатываю через ИИ…",
        }.get(self._state, "")
        self._text(d, cx * s, cy * s, title, fonts.title, str(pal["title"]), "lm")

        # Кнопки справа: крестик у края, «Готово» у него.
        ax = w - px - self._spec(_ABORT)
        self._hit_abort = (ax, cy - self._spec(_ABORT) // 2, ax + self._spec(_ABORT), cy + self._spec(_ABORT) // 2)
        active = self._hover_abort or time.monotonic() < self._pressed_abort_until
        bg_a, fg_a, br_a = self._abort_colors(active)
        self._button(d, (ax, cy - self._spec(_ABORT) // 2, ax + self._spec(_ABORT),
                         cy + self._spec(_ABORT) // 2), bg_a, fg_a, br_a, self._spec(8))
        self._text(d, (ax + self._spec(_ABORT) // 2) * s, cy * s, self._icon, fonts.icon, fg_a, "mm")

        if self._state == "recording":
            dx = ax - self._spec(_GAP) - self._spec(_DONE_W)
            self._hit_stop = (dx, cy - self._spec(_DONE_H) // 2, dx + self._spec(_DONE_W),
                              cy + self._spec(_DONE_H) // 2)
            active = self._hover_stop or time.monotonic() < self._pressed_stop_until
            fill, fg, br, shadow = self._stop_colors(active)
            self._button(d, self._hit_stop, fill, fg, br, self._spec(8), shadow, glow=self._light())
            self._text(d, (dx + self._spec(_DONE_W) // 2) * s, cy * s, "Готово",
                       fonts.body, fg, "mm")
        else:
            self._hit_stop = None

        if self._state == "recording":
            # Ползунки: два в ряд, ровно половина ширины.
            top = lay["switch_top"]
            sh = lay["switch_h"]
            half = (lay["content_w"] - self._spec(_GAP)) // 2
            self._hit_auto = (px, top, px + half, top + sh)
            self._hit_ai = (px + half + self._spec(_GAP), top, px + 2 * half + self._spec(_GAP), top + sh)
            self._switch(d, self._hit_auto, self._auto_enter_btn, top, sh)
            self._switch(d, self._hit_ai, self._ai_btn, top, sh)
        else:
            self._hit_auto = None
            self._hit_ai = None
            self._draw_progress(d, lay)
            if self._detail:
                self._text(d, px * s, lay["detail_top"] * s, self._detail, fonts.small,
                           mix(str(pal["title"]), bg, 0.45), "la")

        small = img.resize((w, h), Image.LANCZOS)
        self._image = small
        # Готовую картинку отдаём хранителю: у Tk дешевле уменьшить свою
        # картинку при показе, чем ехать через PIL для каждого кадра точки.
        # Картинка уходит отдельным пятном на прозрачном фоне: сквозь пустые
        # клетки видно то, что за панелью, а край выходит мягким.
        self._photo = ImageTk.PhotoImage(self._shown_image(small).convert("RGBA"))
        canvas = self._canvas
        if not self._glass.ok:
            canvas.configure(bg=self._window_color())
        if self._image_item is None:
            self._image_item = canvas.create_image(0, 0, image=self._photo, anchor="nw")
        else:
            canvas.itemconfigure(self._image_item, image=self._photo)
        canvas.configure(width=w, height=h)
        canvas.tag_raise(self._image_item)
        self._window_size_changed(apply_geometry=self._state == "idle")

    def _window_color(self) -> str:
        """Плотный цвет окна под рисунком: старший цвет панели.

        Tk умеет только такой цвет, а он нужен для подложки: без стекла —
        под непрозрачным рисунком, со стеклом — чтобы хорошо читались буквы.
        """
        return _solid(str(self._palette.get("bg", "#000000")))

    def _shown_image(self, image: Image.Image) -> Image.Image:
        """Картинка для показа: со стеклом — с прозрачностью, без него — плотная.

        Прозрачные клетки у стекла пропадают сами: окно под ними видно
        насквозь. Без стекла Tk вывел бы их чёрными, поэтому запасной путь
        сплющивает рисунок на свой же плотный фон — ровно как раньше.
        """
        if self._glass.ok:
            return image
        solid = Image.new("RGB", image.size, _solid(str(self._palette.get("bg", "#000000"))))
        solid.paste(image, (0, 0), image if image.mode == "RGBA" else None)
        return solid

    def _window_size_changed(self, *, apply_geometry: bool) -> None:
        if self._window is None:
            return
        self._window.update_idletasks()
        if self._state != "idle" and apply_geometry:
            self._reposition()

    def _draw_progress(self, d: ImageDraw.ImageDraw, lay: dict[str, int]) -> None:
        """Две ступени хода работы: распознавание и обработка через ИИ.

        Ступени различаются меткой перед полосой, а не порядком: квадрат с
        числом — распознавание, ромб с блеском — ИИ. Без метки две полоски
        друг под другом читались бы как одна длинная. Пока ступень не
        началась, она стоит погашенной: видно, что впереди ещё работа.
        """
        px = lay["pad_x"]
        bar_w = lay["content_w"]
        bar_h = lay["bar_h"]
        ai_on = self._ai_enabled
        note = ""
        if self._ai_result is not None:
            note = "готово" if self._ai_result else "без обработки"
        elif self._transcribe_done:
            note = _seconds_note(self._ai_elapsed)

        stages = [
            ("1", self._shown, {"active": True}, "готово" if self._transcribe_done else ""),
            ("ai", self._ai_shown,
             {"active": ai_on, "done": self._ai_result is True,
              "failed": self._ai_result is False, "doc": self._state == "processing"},
             note),
        ]
        for index, (mark, value, mode, tail) in enumerate(stages):
            top = lay["stage1_top"] if index == 0 else lay["stage2_top"]
            self._stage(d, px, top, bar_w, bar_h, mark, value, mode, tail)

    def _stage(
        self,
        d: ImageDraw.ImageDraw,
        px: int,
        top: int,
        width: int,
        height: int,
        mark: str,
        value: float,
        mode: dict[str, bool],
        tail: str,
    ) -> None:
        """Одна ступень: метка, полоса, подпись справа."""
        s = self._ss
        pal = self._palette
        fonts = self._fonts
        active = bool(mode.get("active"))
        done = bool(mode.get("done"))
        failed = bool(mode.get("failed"))
        frac = max(0.0, min(1.0, float(value) if active else 0.0))
        if done:
            frac = 1.0

        mark_box = self._spec(16)
        gap = self._spec(8)
        bar_x = px + mark_box + gap
        tail_w = 0
        if tail:
            face = fonts.tiny[0]
            tail_w = int(_text_size(face, tail)[0] / s) + self._spec(8)
        bar_w = max(self._spec(40), width - mark_box - gap - tail_w)
        cy = top + height // 2

        # Метка ступени: квадрат с числом у первой, ромб у второй.
        if mark == "1" or failed:
            _rounded(d, (px * s, (cy - mark_box // 2) * s, (px + mark_box) * s,
                         (cy + mark_box // 2) * s), self._spec(5) * s,
                     fill=str(pal["stage_mark"]))
            self._text(d, (px * s) + (mark_box * s) // 2, cy * s,
                       "\u2715" if failed else mark, fonts.tiny,
                       str(pal["dot"]) if failed else str(pal["title"]), "mm")
        else:
            diamond = self._diamond(mark_box, str(pal["stage_mark"]))
            if diamond is not None:
                d._image.paste(diamond, ((px + mark_box // 2) * s, cy * s), diamond)

        # Полоса: лоток, заливка, мягкое пятно у её конца и бегущий отсвет.
        _rounded(d, (bar_x * s, top * s, (bar_x + bar_w) * s, (top + height) * s),
                 height * s // 2, fill=str(pal["bar_bg"]))
        fill_w = max(height, frac * bar_w) if frac > 0 else 0.0
        doc = bool(mode.get("doc", False))
        bar_color = self._doc_color() if doc else self._bar_color(
            fraction=frac, done=done, failed=failed)
        bar_halo = self._doc_halo() if doc else str(pal["bar_halo"])
        if fill_w:
            # Пятно лежит на лотке, заливка — поверх пятна: иначе ореол
            # закрасил бы конец полосы и она оборвалась бы на просвет.
            spot, at = self._stage_glow(
                int(bar_x + fill_w - height // 2), int(cy),
                height // 2 + self._spec(4), bar_halo, 0.9 if doc else 0.42,
            )
            d._image.paste(spot, at, spot)
            _rounded(d, (bar_x * s, top * s, (bar_x + fill_w) * s, (top + height) * s),
                     height * s // 2, fill=bar_color)
            if doc and frac < 0.995:
                self._sheen(d, bar_x, top, fill_w, height,
                            self._spec(_SHEEN_STRIP))
            if not doc:
                self._dim_stage(d, bar_x, top, bar_w, height)
        if tail:
            tx = bar_x + bar_w + self._spec(8)
            self._text(d, tx * s, cy * s, tail, fonts.tiny,
                       str(pal["dot"]) if failed else str(pal["title"]), "lm")

    def _dim_stage(self, d: ImageDraw.ImageDraw, bar_x: int, top: int,
                   bar_w: int, height: int) -> None:
        """Притушить погашенную ступень: не начавшаяся работа не должна звать."""
        s = self._ss
        bg = _rgba(str(self._palette["bg"]), 0.62)
        left = bar_x - self._spec(22)
        right = bar_x + bar_w + self._spec(4)
        dim = Image.new("RGBA", ((right - left) * s, (height + 4) * s), (0, 0, 0, 0))
        dd = ImageDraw.Draw(dim)
        dd.rectangle((0, 0, (right - left) * s, (height + 4) * s), fill=bg)
        d._image.paste(dim, (left * s, (top - 2) * s), dim)

    def _doc_color(self) -> str:
        """Цвет второй ступени: узнаётся по цвету, без чтения подписи."""
        pal = self._palette
        return mix(str(pal["bar_fill"]), str(pal.get("dot", pal["bar_fill"])), 0.28)

    def _doc_halo(self) -> str:
        return mix(str(self._palette.get("bar_halo", "#ffffff")),
                   str(self._palette.get("dot", "#ffffff")), 0.35)

    def _bar_color(self, *, fraction: float, done: bool, failed: bool) -> str:
        """Цвет заливки: две ступени узнаются по цвету, а не по подписи."""
        pal = self._palette
        if failed:
            return mix(str(pal["bar_fill"]), str(pal["dot"]), 0.55)
        if done:
            return str(pal["bar_fill"])
        start = str(pal["bar_fill"])
        end = str(pal.get("bar_fill_end", pal["bar_fill"]))
        return mix(start, end, min(1.0, max(0.0, fraction)))

    def _sheen(
        self,
        d: ImageDraw.ImageDraw,
        bar_x: int,
        top: int,
        fill_w: int,
        height: int,
        strip: int,
    ) -> None:
        """Бегущий отсвет по заливке: движение видно, даже когда доля стоит.

        Свет идёт от начала заливки к концу мягкой волной: слой с
        прозрачностью, поэтому отсвет не спорит с цветом полосы.
        """
        s = self._ss
        if fill_w <= height:
            return
        elapsed = time.monotonic() - self._bar_started
        x = (elapsed % _SHEEN_MS) / _SHEEN_MS
        head = bar_x + int(x * (fill_w + strip)) - strip
        if head + strip <= bar_x or head - strip >= bar_x + fill_w:
            return
        left = max(bar_x + 1, head - strip)
        right = min(bar_x + fill_w - 1, head + strip)
        if right - left < 2:
            return
        wave = Image.new(
            "RGBA", ((right - left) * s, height * s), (0, 0, 0, 0)
        )
        wd = ImageDraw.Draw(wave)
        steps = 8
        for i in range(steps):
            k = i / (steps - 1)
            a = math.sin(k * math.pi) ** 2
            x0 = (left - bar_x + int(k * (right - left))) * s
            x1 = (left - bar_x + int((k + 1) * (right - left))) * s + 1
            wd.rectangle((x0, 0, x1, height * s), fill=_rgba("#ffffff", 0.30 * a))
        d._image.paste(wave, (left * s, int(top * s)), wave)

    def _glow_disc(
        self, d: ImageDraw.ImageDraw, cx: int, cy: int, radius: int, color: str
    ) -> None:
        """Круг с мягким краем: три круга, от светлого к фону.

        Прозрачности у рисунка панели нет, поэтому мягкость даёт не альфа,
        а ступени цвета. Крупный рисунок при уменьшении размывает их в
        ровное свечение.
        """
        s = self._ss
        bg = str(self._palette["bg"])
        for _k, r, color_here in self._disc_rings(radius, color):
            d.ellipse(((cx - r) * s, (cy - r) * s, (cx + r) * s, (cy + r) * s),
                      fill=color_here)

    def _stage_glow(self, cx: int, cy: int, radius: int, color: str,
                    alpha: float) -> tuple[Image.Image, tuple[int, int]]:
        """Мягкое пятно у конца полосы: слой с прозрачностью и место для него.

        Ступенями цвета мягкость не выходит: на тёмном фоне видны кольца, и
        пятно выглядит наклейкой. Отдельный слой гасится альфой ровно.
        """
        s = self._ss
        side = (radius * 2 + 2) * s
        img = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        gd = ImageDraw.Draw(img)
        c = side / 2
        for scale, k in ((1.0, 0.14), (0.84, 0.22), (0.66, 0.32), (0.46, 0.5)):
            rr = radius * scale * s
            gd.ellipse((c - rr, c - rr, c + rr, c + rr),
                       fill=_rgba(color, alpha * k))
        return img, (int((cx - radius - 1) * s), int((cy - radius - 1) * s))

    def _disc_rings(self, radius: int, color: str, bg: str = ""):
        base = bg or str(self._palette["bg"])
        for k, scale in ((0.55, 1.0), (0.28, 0.74), (0.14, 0.48)):
            yield k, max(1, int(radius * scale)), mix(base, color, k)

    def _glow_disc_image(self, size: int, color: str) -> Image.Image:
        """То же пятно отдельной картинкой: его надо класть поверх лотка."""
        s = self._ss
        side = size * s
        img = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        gd = ImageDraw.Draw(img)
        c = side / 2
        # Фон, на который ляжет пятно, — лоток полосы, а не корпус панели.
        for _k, r, color_here in self._disc_rings(
            size // 2, color, str(self._palette["bar_bg"])
        ):
            rr = r * s
            gd.ellipse((c - rr, c - rr, c + rr, c + rr), fill=color_here)
        return img

    def _diamond(self, size: int, color: str) -> Image.Image | None:
        """Метка второй ступени: ромб, как знак ИИ."""
        s = self._ss
        side = size * s
        if side <= 4:
            return None
        img = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        dd = ImageDraw.Draw(img)
        pad = 1 * s
        dd.polygon(
            [(side / 2, pad), (side - pad, side / 2), (side / 2, side - pad), (pad, side / 2)],
            fill=color,
        )
        return img

    def _accent_bar(
        self,
        d: ImageDraw.ImageDraw,
        box: tuple[int, int, int, int],
        color: str,
        *,
        top_round: bool,
        bottom_round: bool,
    ) -> None:
        """Цветная кромка у левого края «дымчатого» вида.

        Прямоугольник по краю панели накрывал бы её собственное скругление
        прямоугольным углом. Поэтому наружные углы кромки повторяют радиус
        корпуса, а внутренние остаются прямыми.
        """
        s = self._ss
        x1, y1, x2, y2 = box
        x1, y1, x2, y2 = x1 * s, y1 * s, x2 * s, y2 * s
        outer = 20 * s
        shape = Image.new("RGBA", (int(x2 - x1) + outer * 2, int(y2 - y1) + outer * 2), (0, 0, 0, 0))
        sd = ImageDraw.Draw(shape)
        sd.rounded_rectangle(
            (outer, outer, outer + (x2 - x1), outer + (y2 - y1)),
            radius=outer,
            fill=color,
        )
        # Запятки: скругление остаётся на наружных углах, внутренние обрезаем.
        sd.rectangle((outer * 2, outer, outer * 2 + (x2 - x1), outer + (y2 - y1)), fill=(0, 0, 0, 0))
        sd.rectangle((0, 0, outer * 2, outer * 2), fill=(0, 0, 0, 0))
        sd.rectangle((0, outer + (y2 - y1), outer * 2, outer * 2 + (y2 - y1)), fill=(0, 0, 0, 0))
        if not top_round:
            sd.rectangle((outer, 0, outer * 2, outer * 2), fill=color)
        if not bottom_round:
            sd.rectangle((outer, outer + (y2 - y1), outer * 2, outer * 2 + (y2 - y1)), fill=color)
        sprite = shape.crop((outer, outer, outer + int(x2 - x1), outer + int(y2 - y1)))
        base = d._image if hasattr(d, "_image") else None
        if base is not None:
            base.paste(sprite, (int(x1), int(y1)), sprite)
        else:  # запасной путь: без прозрачности углы будут прямоугольными
            d.rounded_rectangle(box, radius=outer, fill=color)

    def _button(
        self,
        d: ImageDraw.ImageDraw,
        box: tuple[int, int, int, int],
        fill: str,
        fg: str,
        border: str,
        radius: int,
        shadow: str = "",
        glow: bool = False,
    ) -> None:
        s = self._ss
        x1, y1, x2, y2 = box
        x1, y1, x2, y2 = x1 * s, y1 * s, x2 * s, y2 * s
        _rounded(d, (x1, y1, x2, y2), radius * s, fill=fill, outline=border, width=s)
        # Мягкий свет сверху: полоса в десять процентов светлее заливки.
        _rounded(d, (x1 + 2 * s, y1 + s, x2 - 2 * s, y1 + (y2 - y1) // 2),
                 max(2, radius - 2) * s, fill=mix(fill, "#ffffff", 0.10))

    def _switch(
        self,
        d: ImageDraw.ImageDraw,
        box: tuple[int, int, int, int],
        state: _SwitchState,
        top: int,
        height: int,
    ) -> None:
        s = self._ss
        x1, _, x2, _ = box
        colors = self._switch_colors(state.secondary, state.progress, state.hover)
        y1 = top * s
        y2 = (top + height) * s
        _rounded(d, (x1 * s, y1, x2 * s, y2), (height * s) // 2,
                 fill=colors["bg"], outline=colors["border"], width=s)
        track_w = self._spec(_TRACK_W)
        track_h = self._spec(_TRACK_H)
        pad = self._spec(_TRACK_PAD)
        knob = self._spec(_KNOB)
        left = x1 + self._spec(6)
        ty1 = top + (height - track_h) // 2
        ty2 = ty1 + track_h
        if state.progress > 0.05:
            g = max(1, self._spec(2))
            _rounded(
                d,
                ((left - g) * s, (ty1 - g) * s, (left + track_w + g) * s, (ty2 + g) * s),
                (track_h + 2 * g) * s // 2,
                fill=mix(colors["bg"], colors["track"], 0.30 * state.progress),
            )
        _rounded(d, (left * s, ty1 * s, (left + track_w) * s, ty2 * s),
                 track_h * s // 2, fill=colors["track"])
        travel = track_w - knob - pad * 2
        kx = left + pad + travel * state.progress
        d.ellipse(
            (kx * s, (ty1 + pad) * s, (kx + knob) * s, (ty2 - pad) * s),
            fill=colors["knob"],
        )
        self._text(
            d,
            (left + track_w + self._spec(7)) * s,
            (top + height / 2) * s,
            state.text,
            self._fonts.small,
            colors["fg"],
            "lm",
            max_width=(x2 - (left + track_w + self._spec(7)) - self._spec(6)),
        )

    def _dot_layer(self, center_x: int, center_y: int, *, size: float | None = None) -> Image.Image:
        """Слой с точкой записи: мигает и выпускает мягкие волны.

        Точка живёт на отдельном слое с прозрачностью и накладывается на
        картинку целиком: рисуй её прямо на фоне — ореол закрасил бы фон
        квадратом. Рисунок уже крупный, поэтому кольца выходят гладкими.
        Размер места под точку берётся общий для всех состояний.
        """
        pal = self._palette
        s = self._ss
        scale = self._spec(1) * self._ss  # доля точки в рисунке
        slot = self._spec(_DOT_SLOT)
        c = slot / 2 * s
        cy = slot / 2 * s
        elapsed = time.monotonic() - self._pulse_started
        beat = (elapsed % _PULSE_BEAT_S) / _PULSE_BEAT_S
        overlay = Image.new("RGBA", (slot * s, slot * s), (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        wave_alpha = float(pal["dot_glow_alpha"])
        for shift in (0.0, 0.5):
            t = (beat + shift) % 1.0
            radius = (3 + 9 * math.sqrt(t)) * scale
            a = (1 - t) ** 2 * wave_alpha
            if a <= 0.01:
                continue
            od.ellipse((c - radius, cy - radius, c + radius, cy + radius),
                       outline=_rgba(str(pal["dot_wave"]), a), width=s)
        if beat < _PULSE_PEAK_MS / 1000:
            pulse = 1.0
        else:
            pulse = max(0.45, 1.0 - 0.55 * (beat - 0.11) / 0.89)
        halo = 8.5 * scale
        od.ellipse((c - halo, cy - halo, c + halo, cy + halo),
                   fill=_rgba(str(pal["dot_glow"]), min(1.0, 0.55 * pulse * wave_alpha)))
        core = 5 * scale
        od.ellipse((c - core, cy - core, c + core, cy + core),
                   fill=_rgba(mix(str(pal["dot"]), str(pal["bg"]), (1 - pulse) * 0.75), 1.0))
        return overlay

    def _text(
        self,
        d: ImageDraw.ImageDraw,
        x: float,
        y: float,
        text: str,
        font: tuple[ImageFont.FreeTypeFont | ImageFont.ImageFont, int],
        color: str,
        anchor: str = "la",
        max_width: int | None = None,
    ) -> None:
        if not text:
            return
        face, _size = font
        if max_width is not None:
            text = self._fit(face, text, max_width * self._ss)
        d.text((x, y), text, font=face, fill=color, anchor=anchor)

    def _fit(
        self,
        font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
        text: str,
        max_width: float,
    ) -> str:
        """Укоротить подпись, если она не влезает в ползунок."""
        if _text_size(font, text)[0] <= max_width:
            return text
        ellipsis = "..."
        while text and _text_size(font, text + ellipsis)[0] > max_width:
            text = text[:-1]
        return text + ellipsis if text else ""

    # --- перерисовка с задержкой ------------------------------------------

    def _request_render(self, delay: int = 0) -> None:
        if self._render_job is not None or not self._alive:
            return
        self._render_job = delay
        self._after(delay, self._flush_render)

    def _flush_render(self) -> None:
        self._render_job = None
        self._render()

    # --- состояние точки записи -------------------------------------------

    def _start_pulse(self) -> None:
        self._pulse_started = time.monotonic()
        if self._pulse_job is None and self._alive:
            self._pulse_job = 0
            self._after(_PULSE_MS, self._tick_pulse)

    def _tick_pulse(self) -> None:
        if self._state != "recording" or not self._alive:
            self._pulse_job = None
            return
        self._render()
        self._after(_PULSE_MS, self._tick_pulse)

    def _stop_pulse(self) -> None:
        self._pulse_job = None

    def _tween_switch(self, state: _SwitchState, target: float) -> None:
        if not self._alive:
            state.progress = target
            return
        start = state.progress

        def _tick(i: int) -> None:
            state.progress = start + (target - start) * (i / _COLOR_STEPS)
            self._request_render()

        for i in range(1, _COLOR_STEPS + 1):
            self._after(_COLOR_MS * i, lambda k=i: _tick(k))

    # --- мышь ---------------------------------------------------------------

    def _hit(self, x: int, y: int) -> str | None:
        for name, box in (
            ("stop", self._hit_stop),
            ("abort", self._hit_abort),
            ("auto", self._hit_auto),
            ("ai", self._hit_ai),
        ):
            if box is not None and box[0] <= x < box[2] and box[1] <= y < box[3]:
                return name
        return None

    def _on_mouse_down(self, event: tk.Event) -> None:
        widget = getattr(event, "widget", None)
        if widget is not None and widget is not self._canvas:
            return
        x, y = int(event.x), int(event.y)
        hit = self._hit(x, y)
        if hit is None:
            self._on_drag_press(event)
            return
        now = time.monotonic()
        if hit == "stop":
            self._pressed_stop_until = now + 0.14
            self._request_render()
            self._on_stop_click()
        elif hit == "abort":
            self._pressed_abort_until = now + 0.14
            self._request_render()
            self._on_abort_click()
        elif hit == "auto":
            self._on_toggle_auto_enter()
        elif hit == "ai":
            self._on_toggle_ai()

    def _on_mouse_up(self, event: tk.Event) -> None:
        self._on_drag_release(event)

    def _on_motion(self, event: tk.Event) -> None:
        if getattr(event, "widget", None) is not self._canvas:
            return
        self._set_hover(self._hit(int(event.x), int(event.y)))

    def _set_hover(self, hit: str | None) -> None:
        changed = False
        for name, attr in (("stop", "_hover_stop"), ("abort", "_hover_abort"),
                           ("auto", "_hover_auto"), ("ai", "_hover_ai")):
            want = hit == name
            if getattr(self, attr) != want:
                setattr(self, attr, want)
                changed = True
        self._auto_enter_btn.hover = self._hover_auto
        self._ai_btn.hover = self._hover_ai
        if changed:
            self._request_render(_RENDER_DELAY_MS)

    # --- перетаскивание -----------------------------------------------------

    def _reposition(self) -> None:
        if self._root is None or self._window is None:
            return
        if self._drag_active:
            return
        self._window.update_idletasks()
        width, height = self._size
        x = self._root.winfo_screenwidth() - width - self._spec(_MARGIN_X)
        y = self._root.winfo_screenheight() - height - self._spec(_MARGIN_Y)
        self._window.geometry(f"{width}x{height}+{x}+{y}")

    def _begin_local_grab(self) -> None:
        """Локальный захват: панель остаётся перетаскиваемой, другие окна не блокируются.

        grab_set() здесь не годится — он захватывает мышь глобально и при
        потере кнопки может упереться в проверку Tk «grab failed: window
        not viewable».
        """
        if self._window is None:
            return
        try:
            self._window.tk.call("grab", "current", self._window._w)
        except tk.TclError:
            pass

    def _end_local_grab(self) -> None:
        if self._window is None:
            return
        try:
            self._window.tk.call("grab", "release", self._window._w)
        except tk.TclError:
            pass

    def _on_drag_press(self, event: tk.Event) -> None:
        if self._window is None:
            return
        self._window.update_idletasks()
        self._drag_active = True
        self._drag_moved = False
        self._drag_press = (int(event.x_root), int(event.y_root))
        self._drag_offset = (
            self._window.winfo_x() - int(event.x_root),
            self._window.winfo_y() - int(event.y_root),
        )
        self._begin_local_grab()

    def _on_drag_move(self, event: tk.Event) -> None:
        if not self._drag_active or self._window is None or self._drag_press is None:
            return
        x_root = int(event.x_root)
        y_root = int(event.y_root)
        if not self._drag_moved:
            dx = abs(x_root - self._drag_press[0])
            dy = abs(y_root - self._drag_press[1])
            if max(dx, dy) < _DRAG_THRESHOLD:
                return
            self._drag_moved = True
        self._window.geometry(f"+{x_root + self._drag_offset[0]}+{y_root + self._drag_offset[1]}")

    def _on_drag_release(self, _event: tk.Event) -> None:
        if not self._drag_active or self._window is None:
            return
        self._drag_active = False
        self._drag_press = None
        self._end_local_grab()
        if self._drag_moved:
            self._clamp_to_screen()
        self._drag_moved = False

    def _clamp_to_screen(self) -> None:
        """Не даём утащить панель за пределы экрана."""
        if self._root is None or self._window is None:
            return
        self._window.update_idletasks()
        screen_w = self._root.winfo_screenwidth()
        screen_h = self._root.winfo_screenheight()
        width, height = self._size
        x = min(max(self._window.winfo_x(), _DRAG_BORDER),
                max(_DRAG_BORDER, screen_w - width - _DRAG_BORDER))
        y = min(max(self._window.winfo_y(), _DRAG_BORDER),
                max(_DRAG_BORDER, screen_h - height - _DRAG_BORDER))
        self._window.geometry(f"+{x}+{y}")

    # --- вид и тема ---------------------------------------------------------

    def _after(self, delay: int, callback: Callable[[], None]) -> bool:
        """Поставить действие в очередь потока Tk. False — окно уже закрыто.

        После выхода из цикла окна `after` бросает RuntimeError и пишет его в
        журнал. Это не поломка, поэтому проверяем заранее и молчим.
        """
        if self._root is None or not self._alive:
            return False
        try:
            self._root.after(delay, callback)
            return True
        except (RuntimeError, tk.TclError):
            return False

    def set_style(self, style: str) -> None:
        """Сменить вид панели. Можно звать из любого потока."""
        target = panel_style.normalize_style(style)
        self._after(0, lambda: self._style_changed(target))

    def set_theme(self, theme: str) -> None:
        """Сменить тему: auto (как в Windows), dark, light. Из любого потока."""
        target = panel_style.normalize_theme(theme)
        self._after(0, lambda: self._theme_changed(target))

    def set_scale(self, scale: float) -> None:
        """Задать размер панели вручную: 1 — ровно как в макете.

        На экранах с высоким разрешением система считает панель крупнее, а
        текст в ней мельчает вместе с клеткой. Кому нужен привычный размер
        почерпнее, тот выставляет этот масштаб. 0 — «как считает система».
        """
        target = max(0.0, min(3.0, float(scale)))
        self._after(0, lambda: self._scale_changed(target))

    def _scale_changed(self, scale: float) -> None:
        self._manual_scale = scale
        try:  # шрифты пересобираются под новый размер сразу, а не при смене вида
            self._fonts = _fonts(self._scale, _SUPERSAMPLE_MAX)
        except Exception:
            pass
        self._request_render()

    def _style_changed(self, style: str) -> None:
        if style == self._style:
            return
        self._style = style
        self._recompute_palette()
        self._request_render()

    def _theme_changed(self, theme: str) -> None:
        self._theme_choice = theme
        self._system_theme = panel_style.system_theme()
        self._recompute_palette()
        self._request_render()

    def _watch_system_theme(self) -> None:
        """Тема Windows может смениться в любой момент: «Как в Windows» идёт следом."""
        if self._root is None or not self._alive:
            return
        current = panel_style.system_theme()
        if current != self._system_theme:
            self._system_theme = current
            if self._theme_choice == "auto":
                self._recompute_palette()
                self._request_render()
        self._after(1500, self._watch_system_theme)

    # --- ползунки и кнопки --------------------------------------------------

    def _on_toggle_auto_enter(self) -> None:
        if self._skip_auto_enter.is_set():
            self._skip_auto_enter.clear()
        else:
            self._skip_auto_enter.set()
        self._tween_switch(self._auto_enter_btn, 0.0 if self._skip_auto_enter.is_set() else 1.0)

    def _on_stop_click(self) -> None:
        if self._on_stop_recording is not None:
            self._on_stop_recording()

    # --- обработка через ИИ ------------------------------------------------

    def _ai_text(self) -> str:
        """Надпись на ползунке: сначала состояние, потом настроенность.

        Отдельной строчки нет намеренно: панель висит у края экрана и растёт
        от любой новой строки. Надпись на ползунке говорит всё, а причина
        читается в подсказке — короткой строке статуса.
        """
        if not self._ai_ready:
            return "ИИ не настроен"
        if self._ai_last_error:
            return "ИИ · ошибка" if self.ai_state() else "ИИ выкл · ошибка"
        # «вкл» не пишется: включённый ползунок это и так говорит, а место в
        # строке кончается — длинная надпись обрезалась бы по живому.
        return "ИИ выкл" if not self.ai_state() else "Обработка ИИ"

    def _ai_hint(self) -> str:
        if not self._ai_ready:
            return "нет ключа: заведите в кабинете федота.рф и вставьте в config.local.json"
        if self._ai_last_error:
            return f"последний запрос не прошёл: {self._ai_last_error}"
        return ""

    def _render_ai(self) -> None:
        self._ai_btn.text = self._ai_text()
        self._ai_btn.progress = 1.0 if self.ai_state() else 0.0
        self._request_render()

    def _on_toggle_ai(self) -> None:
        """Ползунок переключает обработку и запоминает решение.

        Нажатие уходит наружу (`_notify_toggle_ai`), а там оно пишется в
        `config.json`. Так состояние ползунка переживает перезапуск, и в меню
        трея ничего переключать не нужно.

        Состояние берётся из `ai_state()`, а не из второго флага: флагов два, и
        расхождение между ними и превращало каждое второе нажатие в холостое.
        """
        enabled = not self.ai_state()
        self._ai_enabled = enabled
        self._ai_default_enabled = enabled
        self._ai_btn.text = self._ai_text()
        self._tween_switch(self._ai_btn, 1.0 if enabled else 0.0)
        if self._notify_toggle_ai is not None:
            self._notify_toggle_ai(enabled)

    def _apply_reset_session_ai(self) -> None:
        self._ai_enabled = self._ai_default_enabled
        self._ai_btn.text = self._ai_text()
        self._ai_btn.progress = 1.0 if self.ai_state() else 0.0
        self._request_render()

    def ai_state(self) -> bool:
        """Включена ли обработка сейчас. Это и есть состояние ползунка."""
        return self._ai_enabled

    def ai_enabled(self) -> bool:
        return self.ai_state()

    def set_ai_default(self, enabled: bool) -> None:
        """Применить решение, сохранённое в настройках, и показать его на ползунке."""
        self._ai_default_enabled = enabled
        self._ai_enabled = enabled
        if self._alive and self._root is not None:
            self._after(0, self._apply_reset_session_ai)

    def set_ai_ready(self, ready: bool) -> None:
        """Сказать панели, настроена ли обработка. Без ключа ползунок об этом пишет."""
        self._ai_ready = ready
        if self._alive and self._root is not None:
            self._after(0, self._render_ai)

    def ai_ready(self) -> bool:
        return self._ai_ready

    def ai_hint(self) -> str:
        return self._ai_hint()

    def set_ai_error(self, reason: str | None) -> None:
        """Показать на ползунке, что последний запрос к модели не прошёл."""
        self._ai_last_error = reason
        if self._alive and self._root is not None:
            self._after(0, self._render_ai)

    def _on_abort_click(self) -> None:
        self._skip_auto_enter.set()
        self._tween_switch(self._auto_enter_btn, 0.0)
        if self._on_abort is not None:
            self._on_abort()
        # Панель закроется после фактической остановки транскрибации в main.py

    def _apply_reset_session(self) -> None:
        self._skip_auto_enter.clear()
        self._auto_enter_btn.progress = 1.0
        self._apply_reset_session_ai()

    def reset_session(self) -> None:
        if self._alive and self._root is not None:
            self._after(0, self._apply_reset_session)

    def skip_auto_enter(self) -> bool:
        return self._skip_auto_enter.is_set()

    # --- состояния ----------------------------------------------------------

    # --- ход работы: две ступени и плавная заливка --------------------------

    def _reset_stages(self, state: ActivityState) -> None:
        """Новая диктовка — ступени с нуля.

        Доли сбрасывать обязательно: иначе вторая диктовка начиналась бы с
        заполненной до половины полосы от прошлой.
        """
        self._transcribe_done = False
        self._ai_result = None
        self._ai_progress = 0.0
        self._ai_note = ""
        self._ai_elapsed = 0.0
        if state == "recording":
            return
        # Смена «распознаю» ↔ «обрабатываю» — это переход между ступенями,
        # а не новая диктовка. Доли тут не трогаем: их ведёт метка ступени.
        if state in ("transcribing", "processing"):
            return
        self._progress = 0.0
        self._shown = 0.0
        self._ai_shown = 0.0

    def _note_ai_progress(self) -> None:
        """Заметить, что пришли вести от обработки ИИ.

        Счётчик распознавания зовёт панель с долей распознавания раз в
        полсекунды и без всяких вестей об ИИ. Вести про ИИ приходят без доли.
        Поэтому весть без доли — это про ИИ.
        """
        if self._state != "processing":
            return
        self._transcribe_done = True
        self._ai_elapsed += max(0.0, time.monotonic() - self._ai_started)
        self._ai_started = time.monotonic()
        self._ai_progress = min(0.94, self._ai_progress + 0.1)
        self._ai_note = self._detail or self._ai_note

    def _move_progress(self, progress: float | None) -> None:
        """Раздать пришедшую долю нужной ступени."""
        if progress is None:
            return
        value = max(0.0, min(1.0, progress))
        if self._state == "processing":
            self._ai_progress = max(self._ai_progress, min(0.94, value))
        else:
            self._transcribe_done = False
            # Задним числом доля не уходит: иначе заполненная полоса
            # откатывалась бы назад, когда неточная оценка сдаёт позиции.
            if value >= 0.999:
                self._transcribe_done = True
            self._progress = value if value >= self._progress else self._progress

    def _start_bar(self) -> None:
        if self._bar_job is not None or not self._alive:
            return
        self._bar_job = 0
        self._after(_BAR_MS, self._tick_bar)

    def _stop_bar(self) -> None:
        self._bar_job = None

    def _tick_bar(self) -> None:
        """Кадр хода: подтянуть доли и, если надо, перерисовать.

        Отсвет и мягкое приближение доли — тоже движение, поэтому панель
        обновляется и когда новых вестей нет.
        """
        if not self._alive or self._state in ("idle", "recording"):
            self._bar_job = None
            return
        busy = self._state == "processing"
        near = self._step(self._shown, self._progress)
        if busy:
            near = max(near, self._step(self._ai_shown, self._ai_progress))
        self._shown = near
        if busy:
            self._ai_shown = near
        self._render()
        self._after(_BAR_MS, self._tick_bar)

    @staticmethod
    def _step(shown: float, target: float) -> float:
        """Один шаг к цели: к концу подходит медленнее, как поезд к станции."""
        delta = target - shown
        if abs(delta) < 0.004:
            return target
        return shown + delta * 0.22

    def mark_transcribed(self) -> None:
        """Распознавание позади: первая ступень заполнена, начинается вторая.

        Состояние здесь не меняется: его ставит вызывающий код следующей
        вестью. Иначе три разных действия наперегонки переставляли бы
        состояние друг за другом, а рисуется по последнему.
        """
        if self._window is None:
            return

        def _apply() -> None:
            self._mark_now()
            self._render()

        self._after(0, _apply)

    def _mark_now(self) -> None:
        """То же без очереди: метку порой надо поставить в середине отрисовки."""
        self._transcribe_done = True
        self._progress = 1.0
        self._shown = 1.0
        self._ai_result = None
        self._ai_progress = 0.0
        self._ai_shown = 0.0
        self._ai_note = ""
        self._ai_elapsed = 0.0
        self._ai_started = time.monotonic()

    def set_ai_finished(self, recovered: bool = True) -> None:
        """Обработка закончилась. С `recovered` — вторая ступень вышла.

        Когда не вышло, метка ступени перечёркивается: человек видит, что
        текст ушёл в вставку без обработки, а не что панель просто исчезла.
        """
        if self._window is None:
            return

        def _apply() -> None:
            # Не «идёт обработка» — значит после неё панель уже погасили или
            # перевели в простой. Тогда итог второй ступени никому не нужен.
            if self._state == "idle":
                return
            self._ai_progress = 1.0
            self._ai_shown = 1.0
            self._ai_result = True if recovered else False
            self._render()

        self._after(0, _apply)

    def _apply_update(
        self,
        state: ActivityState,
        detail: str = "",
        progress: float | None = None,
    ) -> None:
        if self._window is None:
            return
        previous = self._state
        self._state = state
        self._detail = detail.strip()
        if state != previous:
            self._reset_stages(state)
        self._note_ai_progress()
        self._move_progress(progress)

        if state == "idle":
            self._stop_pulse()
            self._stop_bar()
            self._window.withdraw()
            return

        if state == "recording" and previous != "recording":
            self._start_pulse()
        if state != "recording":
            self._stop_pulse()
        self._start_bar()

        self._render()
        # Окно и размер ставим вместе с показом: одно окно в процессе — обычный
        # случай, второе (в проверке) иначе вышло бы без места на экране.
        self._window.deiconify()
        self._window.geometry(f"{self._size[0]}x{self._size[1]}")
        self._reposition()
        self._window.lift()
        # Показанное окно снова плотное: стиль, поставленный до показа, при
        # показе слетает. Без этого панель закрыта непрозрачным прямоугольником.
        self._glass.reapply()
        self._request_render()

    def update(
        self,
        state: ActivityState,
        detail: str = "",
        progress: float | None = None,
    ) -> None:
        self._after(0, lambda: self._apply_update(state, detail, progress))

    def stop(self) -> None:
        if self._root is None:
            return

        def _shutdown() -> None:
            self._alive = False
            self._stop_pulse()
            self._window = None
            self._root.quit()

        self._after(0, _shutdown)
