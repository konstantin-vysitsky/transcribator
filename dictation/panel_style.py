"""Внешний вид панели диктовки: четыре вида, у каждого тёмная и светлая тема.

Модуль без окон: здесь только цвета и размеры на каждую роль элемента.
Панель берёт отсюда палитру и рисует себя. Ключи палитры для всех четырёх
видов одни и те же, поэтому сама панель не знает, какое «стекло» она рисует.

Светлая тема — не просто «посветлее»: у светлых видов текст тёмный, а у
«Чёрного стекла» он после переключения остаётся светлым, потому что панель
становится плотной графитовой. Иначе на светлых обоях два стекла выглядели
одинаково серыми.
"""
from __future__ import annotations

import ctypes
import sys
from typing import Any, Literal, Mapping

StyleName = Literal["glow", "black", "smoke", "frost"]
ThemeName = Literal["auto", "dark", "light"]
ResolvedTheme = Literal["dark", "light"]

DEFAULT_STYLE: StyleName = "black"
DEFAULT_THEME: ThemeName = "auto"

# Подписи для меню и журнала. Порядок — порядок в меню.
STYLE_LABELS: Mapping[str, str] = {
    "glow": "1 · Тихое свечение",
    "black": "2 · Чёрное стекло",
    "smoke": "3 · Дымчатое с акцентом",
    "frost": "4 · Светлое стекло",
}
THEME_LABELS: Mapping[str, str] = {
    "auto": "Как в Windows",
    "dark": "Тёмная",
    "light": "Светлая",
}
# Размер панели: 0 — по масштабу экрана, иначе ровно число.
SCALE_LABELS: Mapping[str, float] = {
    "Как в системе": 0.0,
    "1 · как в макете": 1.0,
    "2 · вдвое крупнее": 2.0,
    "3 · втрое крупнее": 3.0,
}

_PALETTES: dict[tuple[str, str], dict[str, object]] = {
    # ---------- 1 · Тихое свечение: графит, цвет только у точки и «Готово» ---
    ("glow", "dark"): {
        "bg": "#13171d",
        "border": "#2b323c",
        "top_glow": "#1b2330",
        "title": "#eef1f5",
        "title_font": ("Segoe UI", 10, "bold"),
        "dot": "#ff8a7e",
        "dot_glow": "#ff8a7e",
        "dot_wave": "#ff8a7e",
        "dot_glow_alpha": 0.55,
        "track": "#1d222a",
        "track_fill": "#88b8e6",
        "done_bg": "#3a78cf",
        "done_border": "#5a9df0",
        "done_fg": "#ffffff",
        "done_shadow": "#3a7bd8",
        "icon_bg": "#171b21",
        "icon_border": "#262c35",
        "icon_fg": "#aeb7c2",
        "icon_hover_bg": "#33212a",
        "icon_hover_fg": "#ffb3aa",
        "sw_off_bg": "#1a1e25",
        "sw_off_border": "#2a3038",
        "sw_off_fg": "#a2abb6",
        "sw_off_track": "#323942",
        "sw_off_knob": "#8f99a4",
        "sw_on_bg": "#21303f",
        "sw_on_border": "#3f6a8f",
        "sw_on_fg": "#e8f1f8",
        "sw_on_track": "#3a9ad6",
        "sw_on_track_end": "#3a9ad6",
        "sw_on_knob": "#ffffff",
    },
    ("glow", "light"): {
        "bg": "#f4f7fb",
        "border": "#d3dbe5",
        "top_glow": "#e8f0fa",
        "title": "#1a2430",
        "title_font": ("Segoe UI", 10, "bold"),
        "dot": "#e25a4d",
        "dot_glow": "#e25a4d",
        "dot_wave": "#e25a4d",
        "dot_glow_alpha": 0.26,
        "track": "#e2e8ef",
        "track_fill": "#3d7fd4",
        "done_bg": "#3d80da",
        "done_border": "#5b9cf0",
        "done_fg": "#ffffff",
        "done_shadow": "#4a8ee8",
        "icon_bg": "#eef2f7",
        "icon_border": "#dbe2ea",
        "icon_fg": "#5b6a7a",
        "icon_hover_bg": "#fbe3e0",
        "icon_hover_fg": "#c93a32",
        "sw_off_bg": "#eef2f7",
        "sw_off_border": "#dbe2ea",
        "sw_off_fg": "#55657a",
        "sw_off_track": "#c7d0db",
        "sw_off_knob": "#ffffff",
        "sw_on_bg": "#ffffff",
        "sw_on_border": "#8fb6e4",
        "sw_on_fg": "#143154",
        "sw_on_track": "#4a8ee8",
        "sw_on_track_end": "#4a8ee8",
        "sw_on_knob": "#ffffff",
    },
    # ---------- 2 · Чёрное стекло: плотное, синее, сильное скругление -------
    # Светлая тема — тот же плотный корпус, но графитовый: чёрное стекло
    # остаётся тёмным, и два вида не сливаются в один серый.
    ("black", "dark"): {
        "bg": "#0b0e13",
        "border": "#26303c",
        "top_glow": "#1a2734",
        "title": "#f7f9fc",
        "title_font": ("Segoe UI", 10, "bold"),
        "dot": "#ff6f61",
        "dot_glow": "#ff6f61",
        "dot_wave": "#ff6f61",
        "dot_glow_alpha": 0.7,
        "track": "#1a2029",
        "track_fill": "#6aa8ee",
        "done_bg": "#2f6fd6",
        "done_border": "#5a9df0",
        "done_fg": "#ffffff",
        "done_shadow": "#3f7ed0",
        "icon_bg": "#151a21",
        "icon_border": "#232b35",
        "icon_fg": "#9aa3af",
        "icon_hover_bg": "#3a2023",
        "icon_hover_fg": "#ffb3ab",
        "sw_off_bg": "#151a21",
        "sw_off_border": "#242c36",
        "sw_off_fg": "#9aa5b3",
        "sw_off_track": "#2d3641",
        "sw_off_knob": "#7e8896",
        "sw_on_bg": "#16334f",
        "sw_on_border": "#3f7ed0",
        "sw_on_fg": "#eaf3ff",
        "sw_on_track": "#3f7ed0",
        "sw_on_track_end": "#6aa8ee",
        "sw_on_knob": "#ffffff",
    },
    ("black", "light"): {
        "bg": "#5b6675",
        "border": "#7d8a9a",
        "top_glow": "#6b7787",
        "title": "#ffffff",
        "title_font": ("Segoe UI", 10, "bold"),
        "dot": "#ffd2cc",
        "dot_glow": "#ffd2cc",
        "dot_wave": "#ffd2cc",
        "dot_glow_alpha": 0.55,
        "track": "#4b5664",
        "track_fill": "#a9cff8",
        "done_bg": "#3f7ed0",
        "done_border": "#a9cff8",
        "done_fg": "#ffffff",
        "done_shadow": "#6aa8ee",
        "icon_bg": "#525d6b",
        "icon_border": "#77838f",
        "icon_fg": "#eef2f6",
        "icon_hover_bg": "#6e4a48",
        "icon_hover_fg": "#ffd9d4",
        "sw_off_bg": "#4b5664",
        "sw_off_border": "#6f7b89",
        "sw_off_fg": "#e7edf4",
        "sw_off_track": "#3f4955",
        "sw_off_knob": "#cdd6e0",
        "sw_on_bg": "#2f5f9c",
        "sw_on_border": "#8fc0f0",
        "sw_on_fg": "#ffffff",
        "sw_on_track": "#3f7ed0",
        "sw_on_track_end": "#6aa8ee",
        "sw_on_knob": "#ffffff",
    },
    # ---------- 3 · Дымчатое с акцентом: кромка, два цвета ползунков --------
    ("smoke", "dark"): {
        "bg": "#17131f",
        "border": "#332c44",
        "top_glow": "#211a2e",
        "accent_left": "#ff8a66",
        "accent_right": "#9d7bff",
        "title": "#f7f3ff",
        "title_font": ("Segoe UI", 10, "bold"),
        "dot": "#ff8a66",
        "dot_glow": "#ff8a66",
        "dot_wave": "#ff8a66",
        "dot_glow_alpha": 0.6,
        "track": "#1b1626",
        "track_fill": "#b39dff",
        "done_bg": "#7552e0",
        "done_border": "#a98cff",
        "done_fg": "#ffffff",
        "done_shadow": "#7c5cff",
        "icon_bg": "#1a1523",
        "icon_border": "#2c2438",
        "icon_fg": "#a49bb8",
        "icon_hover_bg": "#38232a",
        "icon_hover_fg": "#ffb8a3",
        "sw_off_bg": "#1a1523",
        "sw_off_border": "#2c2438",
        "sw_off_fg": "#a49bb8",
        "sw_off_track": "#2f2740",
        "sw_off_knob": "#8d85a3",
        "sw_on_bg": "#2b2150",
        "sw_on_border": "#7a5cf0",
        "sw_on_fg": "#f1ebff",
        "sw_on_track": "#7a5cf0",
        "sw_on_track_end": "#a98cff",
        "sw_on_knob": "#ffffff",
        # Второй ползунок отличается цветом: две настройки — два цвета.
        "sw2_on_bg": "#3d2419",
        "sw2_on_border": "#b5654a",
        "sw2_on_fg": "#ffe4d9",
        "sw2_on_track": "#e87752",
        "sw2_on_track_end": "#ffa07a",
        "sw2_on_knob": "#ffffff",
    },
    ("smoke", "light"): {
        "bg": "#faf7fd",
        "border": "#ded7ea",
        "top_glow": "#f1ecf8",
        "accent_left": "#f07a52",
        "accent_right": "#8266e8",
        "title": "#211a2e",
        "title_font": ("Segoe UI", 10, "bold"),
        "dot": "#e0623f",
        "dot_glow": "#e0623f",
        "dot_wave": "#e0623f",
        "dot_glow_alpha": 0.3,
        "track": "#ece7f3",
        "track_fill": "#9d84f5",
        "done_bg": "#7552e0",
        "done_border": "#9d84f5",
        "done_fg": "#ffffff",
        "done_shadow": "#8266e8",
        "icon_bg": "#f2edf8",
        "icon_border": "#e2dbea",
        "icon_fg": "#6a5f7d",
        "icon_hover_bg": "#fbe4dc",
        "icon_hover_fg": "#b84528",
        "sw_off_bg": "#f2edf8",
        "sw_off_border": "#e2dbea",
        "sw_off_fg": "#6a5f7d",
        "sw_off_track": "#cdc4dc",
        "sw_off_knob": "#ffffff",
        "sw_on_bg": "#ece6fb",
        "sw_on_border": "#9f8bdc",
        "sw_on_fg": "#2f2260",
        "sw_on_track": "#6f4fdb",
        "sw_on_track_end": "#9d84f5",
        "sw_on_knob": "#ffffff",
        "sw2_on_bg": "#fbe8e0",
        "sw2_on_border": "#d98a6d",
        "sw2_on_fg": "#6a2a14",
        "sw2_on_track": "#d9603a",
        "sw2_on_track_end": "#f29170",
        "sw2_on_knob": "#ffffff",
    },
    # ---------- 4 · Светлое стекло: белое, сильное закругление -------------
    ("frost", "light"): {
        "bg": "#ffffff",
        "border": "#e2e8ef",
        "top_glow": "#f6f9fc",
        "title": "#1d2733",
        "title_font": ("Segoe UI", 10, "bold"),
        "dot": "#ff5b52",
        "dot_glow": "#ff5b52",
        "dot_wave": "#ff5b52",
        "dot_glow_alpha": 0.28,
        "track": "#eef2f6",
        "track_fill": "#5b9ae8",
        "done_bg": "#ffffff",
        "done_border": "#b7cde6",
        "done_fg": "#20406a",
        "done_shadow": "#9fb8d4",
        "icon_bg": "#f3f6f9",
        "icon_border": "#e2e8ef",
        "icon_fg": "#5b6d80",
        "icon_hover_bg": "#fde8e6",
        "icon_hover_fg": "#c93a32",
        "sw_off_bg": "#f3f6f9",
        "sw_off_border": "#e2e8ef",
        "sw_off_fg": "#44576a",
        "sw_off_track": "#d3dae2",
        "sw_off_knob": "#ffffff",
        "sw_on_bg": "#eaf3fd",
        "sw_on_border": "#8fbfe8",
        "sw_on_fg": "#17324f",
        "sw_on_track": "#4b8fe4",
        "sw_on_track_end": "#86c0f2",
        "sw_on_knob": "#ffffff",
    },
    ("frost", "dark"): {
        "bg": "#161a20",
        "border": "#2a3038",
        "top_glow": "#1f262f",
        "title": "#eef2f6",
        "title_font": ("Segoe UI", 10, "bold"),
        "dot": "#ff6b61",
        "dot_glow": "#ff6b61",
        "dot_wave": "#ff6b61",
        "dot_glow_alpha": 0.5,
        "track": "#1e232a",
        "track_fill": "#8ec2f2",
        "done_bg": "#1f2a36",
        "done_border": "#37455a",
        "done_fg": "#eef4fb",
        "done_shadow": "#5b9ae8",
        "icon_bg": "#191e25",
        "icon_border": "#2a313a",
        "icon_fg": "#9aa5b2",
        "icon_hover_bg": "#372226",
        "icon_hover_fg": "#ffb3ac",
        "sw_off_bg": "#191e25",
        "sw_off_border": "#2a313a",
        "sw_off_fg": "#9aa5b2",
        "sw_off_track": "#2e353e",
        "sw_off_knob": "#8792a0",
        "sw_on_bg": "#1d2a38",
        "sw_on_border": "#5b9ae8",
        "sw_on_fg": "#f0f6fc",
        "sw_on_track": "#4b8fe4",
        "sw_on_track_end": "#86c0f2",
        "sw_on_knob": "#ffffff",
    },
}


def normalize_style(value: object) -> StyleName:
    """Свести записанное в настройках к известному виду.

    В `config.json` правят руками, и опечатка не должна ронять панель:
    неизвестный вид заменяется видом по умолчанию.
    """
    name = str(value or "").strip().lower()
    if name in STYLE_LABELS:
        return name  # type: ignore[return-value]
    return DEFAULT_STYLE


def normalize_theme(value: object) -> ThemeName:
    name = str(value or "").strip().lower()
    if name in THEME_LABELS:
        return name  # type: ignore[return-value]
    return DEFAULT_THEME


def system_theme() -> ResolvedTheme:
    """Какая тема у Windows прямо сейчас.

    Спрашиваем систему при каждой проверке, а не запоминаем: человек
    переключает тему в настройках Windows и ждёт, что панель пойдёт следом.
    """
    if sys.platform != "win32":
        return "dark"
    try:
        # 20 — «Настройки персонализации», ключ AppsUseLightTheme: 0 — тёмная.
        import winreg  # noqa: PLC0415

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return "light" if int(value) else "dark"
    except (OSError, ValueError, ImportError):
        pass
    try:  # запасной путь: тот же ответ через WinAPI
        return "light" if ctypes.windll.uxtheme.ShouldAppsUseLightTheme() else "dark"
    except Exception:
        return "dark"


def resolve_theme(theme: ThemeName, system: ResolvedTheme | None = None) -> ResolvedTheme:
    if theme == "auto":
        return system if system is not None else system_theme()
    return "light" if theme == "light" else "dark"


def palette(style: StyleName, theme: ResolvedTheme) -> dict[str, Any]:
    """Цвета и шрифты для вида и темы. Возвращается копия: панель может менять."""
    colors = _PALETTES.get((style, theme)) or _PALETTES[(DEFAULT_STYLE, theme)]
    return dict(colors)


# --- применение вида и темы в настройках -----------------------------------


def apply_style(value: object) -> str:
    """Записать вид панели в `config.json` и вернуть принятый вид."""
    from dictation import config  # noqa: PLC0415 — config ввозит не этот модуль

    style = normalize_style(value)
    data = config.load_config()
    data["panel_style"] = style
    config.save_config(data)
    return style


def apply_theme(value: object) -> str:
    """Записать тему панели в `config.json` и вернуть принятую тему."""
    from dictation import config  # noqa: PLC0415

    theme = normalize_theme(value)
    data = config.load_config()
    data["panel_theme"] = theme
    config.save_config(data)
    return theme


def apply_scale(value: object) -> float:
    """Записать размер панели в `config.json` и вернуть принятый размер."""
    from dictation import config  # noqa: PLC0415

    try:
        scale = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        scale = 0.0
    data = config.load_config()
    data["panel_scale"] = scale
    config.save_config(data)
    return float(config.load_config()["panel_scale"])
