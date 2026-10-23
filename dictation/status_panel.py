"""Панель диктовки: страница на браузерном движке и состояние для неё.

Раньше панель рисовалась встроенной графикой Tk картинкой в крупном размере.
Tk смешивать полупрозрачность с тем, что за окном, не умеет: прозрачность
была только общей на всё окно, поэтому панель выходила бледной, а край —
грубым. Теперь рисует браузерный движок: страница лежит в `ui/panel.html`,
палитра вида и темы переводится в переменные CSS, и всё это меняется без
перезапуска.

Здесь только состояние и его перевод в то, что видит страница. Логика
диктовки, распознавания, обработки и вставки живёт в `main.py`, а объект
тот же, что и раньше: `update`, `set_level`, `mark_transcribed`,
`set_ai_finished`, `set_ai_default`, `set_ai_ready`, `ai_enabled`,
`skip_auto_enter`, `reset_session`, `set_style`, `set_theme`, `stop`.

Если страница или движок недоступны, панель просто не показывается, а
причина пишется в журнал: остальное приложение работает как обычно.
"""
from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from typing import Any, Literal

from dictation import panel_style, web_lane

ActivityState = Literal["idle", "recording", "transcribing", "processing"]

# Все размеры — в точках макета, как в HTML. К ним Windows применяет масштаб:
# на 200% панель ровно в два раза крупнее. Высота считается по тому, что
# страница нарисовала сама: запас сверх этого дал бы вокруг плашки пустоту,
# через которую видно стол, а это выглядит как разъехавшийся край.
_PANEL_W = 396
_PANEL_H = 156
_MAX_PANEL_H = 320
_MARGIN_X = 16
_MARGIN_Y = 48
_PLACE_GUARD_S = 0.4

# Виды, перенесённые на страницу. Остальные показывают заглушку: страница
# говорит, что вид ещё не перенесён.
PAGE_STYLES = ("black",)

_log: Callable[[str], None] | None = None


def set_logger(logger: Callable[[str], None] | None) -> None:
    """Куда писать причину, если панель не показалась."""
    global _log
    _log = logger
    web_lane.set_logger(logger)


def _log_line(message: str) -> None:
    if _log is not None:
        try:
            _log(message)
        except Exception:  # noqa: BLE001
            pass


# --- палитра в переменные CSS ------------------------------------------------


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    value = str(color).lstrip("#")[:6]
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


def mix(color_a: str, color_b: str, t: float) -> str:
    """Смешать два цвета: t=0 — первый, t=1 — второй."""
    t = max(0.0, min(1.0, t))
    merged = tuple(
        round(a + (b - a) * t) for a, b in zip(_hex_to_rgb(color_a), _hex_to_rgb(color_b))
    )
    return "#" + "".join(f"{max(0, min(255, c)):02x}" for c in merged)


def _rgba(color: str, alpha: float) -> str:
    red, green, blue = _hex_to_rgb(color)
    return f"rgba({red}, {green}, {blue}, {max(0.0, min(1.0, alpha)):.2f})"


# Насколько корпус плотный: сквозь него должно читаться, что за окном, но
# текст остаётся текстом. Значения подобраны на живой панели.
_GLASS: dict[str, tuple[float, float, float]] = {
    "glow": (0.26, 0.42, 0.62),
    "black": (0.30, 0.46, 0.52),
    "smoke": (0.26, 0.42, 0.58),
    "frost": (0.34, 0.40, 0.62),
}


def palette_variables(style: str, theme: str) -> dict[str, str]:
    """Цвета вида и темы как переменные CSS для страницы панели."""
    colors = panel_style.palette(style, theme)  # type: ignore[arg-type]
    pal: dict[str, str] = {str(k): str(v) for k, v in colors.items() if isinstance(v, str)}
    bg = pal.get("bg", "#0b0e13")
    border = pal.get("border", "#26303c")
    top_glow = pal.get("top_glow", bg)
    title = pal.get("title", "#f7f9fc")
    light = sum(_hex_to_rgb(bg)) / 3 > 128
    start, end, tint = _GLASS.get(style, _GLASS["black"])
    smoke = 1.0 if style == "smoke" else 0.0
    variables: dict[str, str] = {
        "--radius": "22px" if style == "black" else "16px",
        "--corner": "22px" if style == "black" else "16px",
        "--corner-b": "44px" if style == "black" else "32px",
        "--corner-fill": "#15161a",
        "--pad-x": "16px",
        "--pad-y": "14px",
        "--gap-y": "6px",
        "--done-h": "32px",
        "--btn-radius": "10px" if style in ("black", "glow", "smoke") else "99px",
        "--bg-a": _rgba(mix(bg, "#000000" if not light else "#ffffff", 0.08), start),
        "--bg-b": _rgba(mix(bg, "#ffffff" if not light else "#000000", 0.06), end),
        "--bg-tint-a": _rgba(top_glow, tint),
        "--bg-tint-b": _rgba(bg, tint * 0.6),
        "--border": _rgba(border, 0.9),
        "--gloss": _rgba("#ffffff", 0.34 if light else 0.18),
        "--glow": _rgba(pal.get("bar_halo", "#6aa8ee"), 0.25),
        "--title": title,
        "--muted": mix(title, bg, 0.42),
        "--divider": _rgba(border, 0.9),
        "--accent-left": _rgba(pal.get("accent_left", "#ff8a66"), smoke),
        "--accent-right": _rgba(pal.get("accent_right", "#9d7bff"), smoke),
        "--dot": pal.get("dot", "#ff6f61"),
        "--dot-glow": pal.get("dot_glow", "#ff6f61"),
        "--dot-wave": _rgba(
            pal.get("dot_wave", "#ff6f61"), float(colors.get("dot_glow_alpha", 0.5) or 0.5)
        ),
        "--track": pal.get("track", "#1a2029"),
        "--stage-mark": pal.get("stage_mark", "#1f3550"),
        "--bar-fill": pal.get("bar_fill", "#4f93e8"),
        "--bar-fill-end": pal.get("bar_fill_end", "#6aa8ee"),
        "--bar-halo": _rgba(pal.get("bar_halo", "#7ab6f5"), 0.55),
        "--done-bg": pal.get("done_bg", "#2f6fd6"),
        "--done-top": mix(pal.get("done_bg", "#2f6fd6"), "#ffffff", 0.18),
        "--done-hover": mix(pal.get("done_bg", "#2f6fd6"), "#ffffff", 0.08),
        "--done-border": pal.get("done_border", "#5a9df0"),
        "--done-fg": pal.get("done_fg", "#ffffff"),
        "--done-shadow": _rgba(pal.get("done_shadow", "#3f7ed0"), 0.9),
        "--icon-bg": _rgba(pal.get("icon_bg", "#151a21"), 0.85),
        "--icon-border": pal.get("icon_border", "#232b35"),
        "--icon-fg": pal.get("icon_fg", "#9aa3af"),
        "--icon-hover-bg": _rgba(pal.get("icon_hover_bg", "#3a2023"), 0.9),
        "--icon-hover-fg": pal.get("icon_hover_fg", "#ffb3ab"),
        "--sw-off-bg": _rgba(pal.get("sw_off_bg", "#151a21"), 0.75),
        "--sw-off-border": pal.get("sw_off_border", "#242c36"),
        "--sw-off-fg": pal.get("sw_off_fg", "#9aa5b3"),
        "--sw-off-track": pal.get("sw_off_track", "#2d3641"),
        "--sw-off-knob": pal.get("sw_off_knob", "#7e8896"),
        "--sw-on-bg": _rgba(pal.get("sw_on_bg", "#16334f"), 0.9),
        "--sw-on-border": pal.get("sw_on_border", "#3f7ed0"),
        "--sw-on-fg": pal.get("sw_on_fg", "#eaf3ff"),
        "--sw-on-track": pal.get("sw_on_track", "#3f7ed0"),
        "--sw-on-track-end": pal.get("sw_on_track_end", "#6aa8ee"),
        "--sw-on-knob": pal.get("sw_on_knob", "#ffffff"),
    }
    if smoke:
        # У второго ползунка свой цвет, заметный в виде «Дымчатое».
        variables["--sw2-on-bg"] = _rgba(str(pal.get("sw2_on_bg", "#3d2419")), 0.9)
        variables["--sw2-on-border"] = pal.get("sw2_on_border", variables["--sw-on-border"])
        variables["--sw2-on-fg"] = pal.get("sw2_on_fg", variables["--sw-on-fg"])
        variables["--sw2-on-track"] = pal.get("sw2_on_track", variables["--sw-on-track"])
        variables["--sw2-on-track-end"] = pal.get("sw2_on_track_end", variables["--sw-on-track-end"])
    return variables


def apply_variables(variables: dict[str, str]) -> str:
    """Один вызов страницы, который меняет всю палитру."""
    payload = json.dumps(variables, ensure_ascii=False)
    return (
        "(() => { const v = " + payload + "; "
        "for (const k in v) { document.documentElement.style.setProperty(k, v[k]); } "
        "return true; })()"
    )


class StatusPanel:
    """Панель диктовки. Снаружи всё как раньше: см. описание модуля."""

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
    ) -> None:
        self._on_abort = on_abort
        self._on_stop_recording = on_stop_recording
        # Имена с «notify» намеренно: обработчик, названный как метод класса,
        # затирает метод собой.
        self._notify_toggle_ai = on_toggle_ai
        self._skip_auto_enter = threading.Event()
        # Одно состояние на ползунок и на решение в main.py: нажатие пишется
        # в настройки и живёт до следующего нажатия.
        self._ai_enabled = ai_enabled
        self._ai_default_enabled = ai_enabled
        self._ai_ready = ai_ready
        self._ai_last_error: str | None = None

        self._theme_choice = panel_style.normalize_theme(theme)
        self._style = panel_style.normalize_style(style)
        self._system_theme = panel_style.system_theme()

        self._state: ActivityState = "idle"
        self._detail = ""
        self._progress = 0.0
        self._shown = 0.0
        self._ai_shown = 0.0
        self._ai_result: bool | None = None
        self._transcribe_done = False
        self._ai_started = time.monotonic()
        self._level = 0.0
        self._recording_since: float | None = None
        self._lock = threading.Lock()
        self._alive = True
        self._measured = threading.Event()
        self._page_height = 0
        self._size: tuple[int, int] | None = None
        self._last_place = 0.0
        self._shown_window = False

        self._page = web_lane.make_page("panel", self._on_page_event)
        self._apply_look()
        self._ticker = threading.Thread(target=self._tick_loop, daemon=True, name="panel-tick")
        self._ticker.start()

    # --- что спрашивает остальное приложение -------------------------------

    @property
    def style(self) -> str:
        return self._style

    @property
    def theme_choice(self) -> str:
        return self._theme_choice

    def palette(self) -> dict[str, Any]:
        return panel_style.palette(self._style, self._resolved_theme())

    def ai_state(self) -> bool:
        return self._ai_enabled

    def ai_enabled(self) -> bool:
        return self._ai_enabled

    def ai_ready(self) -> bool:
        return self._ai_ready

    def ai_hint(self) -> str:
        if not self._ai_ready:
            return "нет ключа: заведите в кабинете федота.рф и вставьте в config.local.json"
        if self._ai_last_error:
            return f"последний запрос не прошёл: {self._ai_last_error}"
        return ""

    def skip_auto_enter(self) -> bool:
        return self._skip_auto_enter.is_set()

    # --- нажатия со страницы ------------------------------------------------

    def _on_page_event(self, name: str, value: Any) -> None:
        if name == "done":
            if self._on_stop_recording is not None:
                self._on_stop_recording()
        elif name == "abort":
            self._skip_auto_enter.set()
            if self._on_abort is not None:
                self._on_abort()
        elif name == "auto_enter":
            if bool(value):
                self._skip_auto_enter.clear()
            else:
                self._skip_auto_enter.set()
            self._push()
        elif name == "ai":
            self._set_ai_enabled(bool(value))
        elif name == "ready":
            self._apply_look()
            self._push()
            self._page.ask("measure")
        elif name == "measure":
            if isinstance(value, (int, float)) and value > 40:
                self._page_height = int(value)
                self._measured.set()
        elif name == "window-ready":
            # Окно появилось: показываем его там, где идёт работа.
            self._shown_window = False
            self._place(force=True)

    def _set_ai_enabled(self, enabled: bool, assume: bool = False) -> None:
        """Переключить обработку и сказать наружу.

        `assume` — в проверке: состояние задаётся снаружи, страницу не ждём.
        """
        if not assume and enabled == self._ai_enabled:
            return
        self._ai_enabled = enabled
        self._ai_default_enabled = enabled
        self._push()
        if self._notify_toggle_ai is not None:
            self._notify_toggle_ai(enabled)

    def _on_toggle_ai(self) -> None:
        """Переключить обработку без страницы: так делают проверки."""
        self._set_ai_enabled(not self._ai_enabled, assume=True)

    # --- настройки ----------------------------------------------------------

    def set_ai_default(self, enabled: bool) -> None:
        self._ai_default_enabled = enabled
        self._ai_enabled = enabled
        self._push()

    def set_ai_ready(self, ready: bool) -> None:
        self._ai_ready = ready
        self._push()

    def set_ai_error(self, reason: str | None) -> None:
        self._ai_last_error = reason
        self._push()

    def reset_session(self) -> None:
        self._skip_auto_enter.clear()
        self._ai_enabled = self._ai_default_enabled
        self._push()

    def set_style(self, style: str) -> None:
        target = panel_style.normalize_style(style)
        if target == self._style:
            return
        self._style = target
        self._apply_look()
        self._push()
        self._page.ask("measure")

    def set_theme(self, theme: str) -> None:
        target = panel_style.normalize_theme(theme)
        if target == self._theme_choice:
            return
        self._theme_choice = target
        self._system_theme = panel_style.system_theme()
        self._apply_look()
        self._push()

    def _resolved_theme(self) -> str:
        return panel_style.resolve_theme(self._theme_choice, self._system_theme)

    def _apply_look(self) -> None:
        self._page.execute(apply_variables(palette_variables(self._style, self._resolved_theme())))

    # --- ход работы ---------------------------------------------------------

    def update(
        self,
        state: ActivityState,
        detail: str = "",
        progress: float | None = None,
    ) -> None:
        with self._lock:
            previous = self._state
            self._state = state
            self._detail = detail.strip()
            if state != previous:
                self._reset_stages(state)
                if state == "recording":
                    self._recording_since = time.monotonic()
                    self._level = 0.0
                else:
                    self._recording_since = None
            if state == "processing":
                self._note_ai_progress()
            if progress is not None:
                self._move_progress(progress)
        self._push()
        self._place()

    def set_level(self, level: float) -> None:
        """Громкость микрофона 0..1. Без записи не бывает громкости."""
        if level is None:
            return
        with self._lock:
            self._level = max(0.0, min(1.0, float(level)))

    def mark_transcribed(self) -> None:
        with self._lock:
            self._transcribe_done = True
            self._progress = 1.0
            self._shown = 1.0
            self._ai_result = None
            self._ai_shown = 0.0
            self._ai_started = time.monotonic()
        self._push()
        self._page.ask("measure")
        self._place(force=True)

    def set_ai_finished(self, recovered: bool = True) -> None:
        with self._lock:
            if self._state == "idle":
                return
            self._ai_shown = 1.0
            self._ai_result = True if recovered else False
        self._push()

    def _reset_stages(self, state: ActivityState) -> None:
        self._transcribe_done = False
        self._ai_result = None
        if state in ("transcribing", "processing"):
            return
        self._progress = 0.0
        self._shown = 0.0
        self._ai_shown = 0.0

    def _note_ai_progress(self) -> None:
        self._transcribe_done = True
        self._shown = 1.0
        self._ai_started = time.monotonic()

    def _move_progress(self, progress: float) -> None:
        value = max(0.0, min(1.0, progress))
        if self._state == "processing":
            self._ai_shown = max(self._ai_shown, min(0.94, value))
            return
        self._transcribe_done = value >= 0.999
        if value >= self._progress:
            self._progress = value
            self._shown = max(self._shown, value)

    # --- что уходит на страницу --------------------------------------------

    def page_state(self) -> dict[str, Any]:
        with self._lock:
            mode = "work" if self._state in ("transcribing", "processing") else "recording"
            duration = 0.0
            if self._recording_since is not None:
                duration = time.monotonic() - self._recording_since
            note = ""
            if self._transcribe_done and self._ai_result is None:
                note = "· %.1f с" % max(0.0, time.monotonic() - self._ai_started)
            return {
                "mode": mode,
                "activity": self._state,
                "detail": self._detail,
                "duration": round(duration, 2),
                "level": round(self._level, 3),
                "auto_enter": not self._skip_auto_enter.is_set(),
                "ai_enabled": self._ai_enabled,
                "ai_ready": self._ai_ready,
                "ai_error": self._ai_last_error,
                "ai_hint": self.ai_hint(),
                "transcribe_progress": round(self._shown, 3),
                "transcribe_done": self._transcribe_done,
                "ai_progress": round(self._ai_shown, 3),
                "ai_note": note.replace(".", ","),
                "ai_result": self._ai_result,
                "style": self._style,
                "theme": self._resolved_theme(),
            }

    def _push(self) -> None:
        if not self._alive:
            return
        state = self.page_state()
        payload = json.dumps(state, ensure_ascii=False)
        self._page.execute(f"window.DSH && window.DSH.apply({payload})")

    # --- окно ----------------------------------------------------------------

    def _size_now(self) -> tuple[int, int]:
        """Размер окна: ширина макета и высота, которую нарисовала страница.

        Высота страницы мала, пока движок не пересчитал её после смены
        состояния: ползунки появляются позже и обрезались бы. Поэтому на
        время пересчёта окну дают предел, а потом оно ужимается по факту.
        """
        width = _PANEL_W
        height = self._page_height or _PANEL_H
        if self._page_height and self._page_height < _PANEL_H:
            height = _PANEL_H
        scale = web_lane.screen_scale()
        return (round(width * scale), round(min(_MAX_PANEL_H, height) * scale))

    def _apply_size(self, size: tuple[int, int]) -> None:
        """Сказать странице, какого размера окно.

        Окно без рамки страница узнаёт с задержкой: до этого она считает
        себя маленькой, и плашка выходит не такой, как в макете. Прямые
        размеры снимают эту задержку совсем.
        """
        width, height = size
        if getattr(self, "_drawn_size", None) == (width, height):
            return
        self._drawn_size = (width, height)
        self._page.execute(apply_variables({"panel-w": f"{width}px", "panel-h": f"{height}px"}))

    def _place(self, force: bool = False) -> None:
        """Поставить окно там, где стоит, или увести его за край экрана.

        Окно всегда остаётся открытым: пока идёт запись, его видно. Когда
        работа кончилась, окно уходит за край экрана. Спрятать его
        по-настоящему нельзя — спрятанное окно движок не перерисовывает, и
        при следующем показе на нём остаются следы прошлого вида.
        """
        if not self._alive or not self._page.ok:
            return
        size = self._size_now()
        self._apply_size(size)
        if self._state == "idle":
            if self._shown_window:
                self._page.hide()
                self._shown_window = False
                self._size = size
            return
        now = time.monotonic()
        changed = size != self._size
        self._size = size
        if self._shown_window and not changed and not force:
            return
        # Число и место бьют состояние часто: окно ставим реже, иначе
        # перетаскивание панели мышью дёргалось бы.
        if self._shown_window and now - self._last_place < _PLACE_GUARD_S and not changed:
            return
        width, height = size
        left, top, right, bottom = web_lane.work_area()
        x = max(left, right - width - round(_MARGIN_X * web_lane.screen_scale()))
        y = max(top, bottom - height - round(_MARGIN_Y * web_lane.screen_scale()))
        self._last_box = (x, y, x + width, y + height)
        self._page.place(x, y, width, height)
        self._shown_window = True
        self._last_place = now

    def _tick_loop(self) -> None:
        """Плавное приближение долей и текущее время записи.

        Доля приходит из main.py раз в полсекунды, а полоса не должна
        дёргаться: к цели подходим мягко, как на прежней панели. Время
        записи считаем сами, иначе секунды на панели замирали бы.
        """
        while self._alive:
            time.sleep(0.04)
            if not self._alive:
                return
            with self._lock:
                state = self._state
                moved = False
                if state in ("transcribing", "processing"):
                    if abs(self._progress - self._shown) > 0.004:
                        self._shown += (self._progress - self._shown) * 0.25
                        moved = True
                    if state == "processing" and self._ai_result is None:
                        if self._ai_shown < 0.94:
                            self._ai_shown = min(0.94, self._ai_shown + 0.004)
                            moved = True
                elif state == "recording" and self._recording_since is not None:
                    moved = True
            if moved:
                self._push()

    def stop(self) -> None:
        if not self._alive:
            return
        self._alive = False
        self._page.close()
