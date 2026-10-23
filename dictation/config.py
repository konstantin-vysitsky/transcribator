from __future__ import annotations

import json
from pathlib import Path
from typing import Any

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"
# Ключ к сервису обработки держим отдельно и не отправляем в git: `config.json`
# лежит в репозитории, а ключ — это доступ к деньгам. Файл необязательный.
LOCAL_CONFIG_PATH = CONFIG_PATH.with_name("config.local.json")

DEFAULTS: dict[str, Any] = {
    "hotkey": "f7",
    "hotkey_mode": "hold",
    "model": "v3_e2e_rnnt",
    "remove_fillers": True,
    "cpu_threads": 6,
    "paste_append_space": True,
    "paste_auto_enter": True,
    "sound_feedback": True,
    "min_utterance_s": 0.3,
    "autostart": False,
    # Обработка диктовки через LLM. По умолчанию выключена: без ключа она
    # всё равно не работает, и включать её молча нельзя.
    "ai_enabled": False,
    "ai_base_url": "https://xn--80ahc1bui.xn--p1ai/v1",
    "ai_api_key": "",
    "ai_model": "deepseek-flash",
    "ai_timeout_s": 30.0,
    # Вид панели и её тема. Тема «auto» — панель следует за Windows: у
    # человека ночью тёмная тема, днём светлая, и выбор ему не нужен.
    "panel_style": "black",
    "panel_theme": "auto",
}

# Поля, которые читаются ещё и из config.local.json и там главнее.
_LOCAL_OVERRIDES = ("ai_api_key",)

# Допустимые значения вида и темы панели. Держим здесь, а не в panel_style:
# panel_style ввозит этот модуль, и обратная зависимость замкнула бы круг.
_STYLES = ("glow", "black", "smoke", "frost")
_THEMES = ("auto", "dark", "light")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _clamp(merged: dict[str, Any]) -> dict[str, Any]:
    merged["cpu_threads"] = max(1, min(10, int(merged.get("cpu_threads", 6))))
    merged["ai_timeout_s"] = max(5.0, min(120.0, float(merged.get("ai_timeout_s", 30.0))))
    # Вид и тему панели правят и руками в файле: опечатка не должна ронять
    # окно, она превращается в значение по умолчанию.
    merged["panel_style"] = _normalize_style(merged.get("panel_style"))
    merged["panel_theme"] = _normalize_theme(merged.get("panel_theme"))
    return merged


def _normalize_style(value: object) -> str:
    style = str(value or "").strip().lower()
    return style if style in _STYLES else str(DEFAULTS["panel_style"])


def _normalize_theme(value: object) -> str:
    theme = str(value or "").strip().lower()
    return theme if theme in _THEMES else str(DEFAULTS["panel_theme"])


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        save_config(DEFAULTS)
        merged = dict(DEFAULTS)
    else:
        merged = dict(DEFAULTS)
        merged.update(_read_json(CONFIG_PATH))
    local = _read_json(LOCAL_CONFIG_PATH)
    for field in _LOCAL_OVERRIDES:
        if local.get(field):
            merged[field] = local[field]
    return _clamp(merged)


def save_config(data: dict[str, Any]) -> None:
    """Записать настройки в `config.json`.

    Ключ в общий файл не пишется: он лежит в репозитории, а репозиторий
    публичный. Если ключ пришёл в `data`, он уходит в `config.local.json`,
    который в git не попадает.
    """
    merged = _clamp(dict(DEFAULTS) | dict(data))
    api_key = str(merged.pop("ai_api_key", "") or "").strip()
    CONFIG_PATH.write_text(
        json.dumps(merged, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    if api_key:
        local = _read_json(LOCAL_CONFIG_PATH)
        local["ai_api_key"] = api_key
        LOCAL_CONFIG_PATH.write_text(
            json.dumps(local, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
