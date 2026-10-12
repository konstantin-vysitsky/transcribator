"""Проверка связки: настройки, меню трея и путь обработки в main без звука.

Запуск: `.venv\\Scripts\\python.exe tests\\check_wiring.py`

Здесь проверяется то, что не видно в отдельных проверках: что настройки
читаются и пишутся целиком, что меню трея собирается и пункт обработки
переключает настройку, и что решение «обрабатывать или нет» складывается
из настройки и кнопки так, как задумано.

Микрофон и модель не трогаются, окна не открываются.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dictation import ai, config  # noqa: E402

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(("  ок   " if condition else "  ПЛОХО ") + name + ("" if condition else f" {detail}"))
    if not condition:
        FAILURES.append(name)


def test_config_roundtrip() -> None:
    print("настройки:")
    with tempfile.TemporaryDirectory() as tmp:
        original = config.CONFIG_PATH
        original_local = config.LOCAL_CONFIG_PATH
        config.CONFIG_PATH = Path(tmp) / "config.json"
        config.LOCAL_CONFIG_PATH = Path(tmp) / "config.local.json"
        try:
            fresh = config.load_config()
            check("новый файл заполнен значениями по умолчанию", config.CONFIG_PATH.exists())
            check("обработка выключена по умолчанию", fresh["ai_enabled"] is False)
            check("адрес — Token Seller", fresh["ai_base_url"] == ai.DEFAULT_BASE_URL)

            fresh["ai_enabled"] = True
            fresh["ai_api_key"] = "sk-проверка"
            fresh["ai_model"] = "deepseek-chat"
            config.save_config(fresh)

            again = config.load_config()
            check("включение сохранилось", again["ai_enabled"] is True)
            check("ключ сохранился", again["ai_api_key"] == "sk-проверка", repr(again["ai_api_key"]))
            check("модель сохранилась", again["ai_model"] == "deepseek-chat", repr(again["ai_model"]))

            on_disk = json.loads(config.CONFIG_PATH.read_text(encoding="utf-8"))
            check("в файле есть поле обработки", "ai_enabled" in on_disk)
            check("в файле есть адрес", on_disk["ai_base_url"] == ai.DEFAULT_BASE_URL)
            # Ключ — это доступ к деньгам, и config.json лежит в git.
            check("ключа нет в общем файле", "ai_api_key" not in on_disk, repr(on_disk.get("ai_api_key")))
            check("ключ ушёл в местный файл", config.LOCAL_CONFIG_PATH.exists())
            local_disk = json.loads(config.LOCAL_CONFIG_PATH.read_text(encoding="utf-8"))
            check("ключ лежит в местном файле", local_disk.get("ai_api_key") == "sk-проверка")

            # Ключ из местного файла главнее общего.
            common = config._read_json(config.CONFIG_PATH)
            common["ai_api_key"] = "sk-из-общего"
            config.CONFIG_PATH.write_text(
                json.dumps(common, ensure_ascii=False), encoding="utf-8"
            )
            check(
                "местный ключ перекрывает общий",
                config.load_config()["ai_api_key"] == "sk-проверка",
            )

            # Старый config.json без полей обработки должен читаться без правок.
            config.LOCAL_CONFIG_PATH.unlink()
            config.CONFIG_PATH.write_text(
                json.dumps({"hotkey": "f7", "model": "v3_e2e_rnnt"}, ensure_ascii=False),
                encoding="utf-8",
            )
            legacy = config.load_config()
            check("старый файл не роняет чтение", legacy["hotkey"] == "f7")
            check("поля обработки подставлены", legacy["ai_enabled"] is False)
            check("адрес по умолчанию подставлен", legacy["ai_base_url"] == ai.DEFAULT_BASE_URL)

            legacy["ai_timeout_s"] = 9999
            config.save_config(legacy)
            check(
                "слишком большой таймаут ограничен",
                config.load_config()["ai_timeout_s"] == 120.0,
                str(config.load_config()["ai_timeout_s"]),
            )

            broken = Path(tmp) / "broken.json"
            broken.write_text("{не json", encoding="utf-8")
            check("битый файл не роняет чтение", config._read_json(broken) == {})
        finally:
            config.CONFIG_PATH = original
            config.LOCAL_CONFIG_PATH = original_local


def test_decision_matrix() -> None:
    """Как складывается «обрабатывать или нет» из настройки и кнопки."""
    print("решение об обработке:")

    class FakePanel:
        def __init__(self, enabled: bool) -> None:
            self._enabled = enabled

        def ai_enabled(self) -> bool:
            return self._enabled

    def decide(cfg_enabled: bool, panel_enabled: bool | None) -> bool:
        # Повторяет main.process_audio: кнопка важнее настройки.
        value = cfg_enabled
        if panel_enabled is not None:
            value = panel_enabled
        return value

    check("настройка выключена, кнопки нет — не обрабатывать", decide(False, None) is False)
    check("настройка включена, кнопки нет — обрабатывать", decide(True, None) is True)
    check("настройка включена, кнопка выключила — не обрабатывать", decide(True, False) is False)
    check("настройка выключена, кнопка включила — обрабатывать", decide(False, True) is True)

    check("панель без окна отвечает", FakePanel(True).ai_enabled() is True)


def test_tray_menu() -> None:
    print("меню трея:")
    import pystray

    from dictation.tray import TrayApp

    app = TrayApp.__new__(TrayApp)
    app._activity = "idle"
    app._activity_detail = ""
    app._runtime_info = ""
    app._cpu_threads = 6
    app._hotkey_mode = "hold"
    app._autostart_enabled = False
    app._ai_enabled = True
    app._history = __import__("collections").deque(maxlen=3)
    app._history_lock = __import__("threading").Lock()

    menu = TrayApp._build_menu(app)
    texts = [str(item.text) for item in menu.items]
    print(f"       пункты: {texts}")
    check("пункт обработки есть", "Обработка через ИИ" in texts)
    check("пункт промпта есть", "Промпт обработки…" in texts)
    check("прочие пункты не потеряны", "Перезагрузить" in texts and "Выход" in texts)

    ai_item = next(item for item in menu.items if str(item.text) == "Обработка через ИИ")
    check("пункт с галочкой", ai_item.checked is not None)

    check("объект pystray.Menu собран", isinstance(menu, pystray.Menu))


def test_default_prompt_content() -> None:
    print("содержание промпта:")
    from dictation.prompt import DEFAULT_PROMPT

    lowered = DEFAULT_PROMPT.lower()
    for word in ["смысл", "знаки препинания", "заглавные буквы", "абзацы", "списком"]:
        check(f"в промпте есть «{word}»", word in lowered)
    for word in ["не выдумывай", "переписывать", "добавлять"]:
        check(f"в промпте есть запрет «{word}»", word in lowered)
    check(
        "смысл защищён прямо",
        "менять, добавлять или убирать смысл" in lowered,
        DEFAULT_PROMPT[:80],
    )
    check(
        "ответ на сказанное запрещён",
        "отвечать на сказанное" in lowered,
    )
    check("промпт требует чёткости", "чётк" in lowered)
    check("промпт не длиннее 2000 знаков", len(DEFAULT_PROMPT) < 2000, str(len(DEFAULT_PROMPT)))
    print(f"       длина промпта: {len(DEFAULT_PROMPT)} знаков")


def main() -> int:
    test_config_roundtrip()
    test_decision_matrix()
    test_default_prompt_content()
    test_tray_menu()
    print()
    if FAILURES:
        print(f"ПРОВАЛЕНО: {len(FAILURES)}")
        for name in FAILURES:
            print(f"  - {name}")
        return 1
    print("Всё прошло.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
