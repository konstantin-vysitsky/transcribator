"""Проверка: запись идёт с выбранного микрофона и без проверки на тишину.

Отказ, с которого это началось: человек нажимает F7, говорит, а текста нет.
Проверка на тишину решала за него, есть ли в записи речь, и ошибалась:
тихую диктовку объявляла тишиной. Проверку убрали, и вот что должно
остаться вместо неё:

- каждая запись уходит в распознавание, громкая и тихая;
- пустой ответ модели понятен в журнале: громкость и микрофон;
- меню трея даёт выбрать микрофон, и выбор действует без перезапуска.

Запуск: .venv\\Scripts\\python.exe tests\\check_silent_mic.py
"""

from __future__ import annotations

import inspect
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dictation import config, recorder as rec_mod  # noqa: E402
from dictation.recorder import Recorder  # noqa: E402

SAMPLE_RATE = 16000
FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(("  ок   " if condition else "  ПЛОХО ") + name + ("" if condition else f" {detail}"))
    if not condition:
        FAILURES.append(name)


def test_no_silence_gate() -> None:
    print("проверки на тишину нет:")
    import dictation.main as main_module

    source = inspect.getsource(main_module)
    for word in ("speech_rooms", "whisper_rooms", "MIN_SPEECH_ROOMS", "тишина"):
        check(f"в главном коде нет «{word}»", word not in source)


def test_device_choice() -> None:
    print("выбор микрофона:")
    recorder = Recorder(min_duration_s=0.0, preroll=True)
    check("по умолчанию отдан Windows", recorder._device_choice == "" or recorder._device_choice is None)
    devices = rec_mod.input_devices()
    if not devices:
        check("система показывает микрофоны", False)
        return
    chosen = devices[0]["name"]
    recorder.set_device(chosen)
    check("запись узнала о смене", recorder._device_choice == chosen)
    check(
        "устройство найдено по имени",
        rec_mod.resolve_device(chosen) == devices[0]["index"],
    )

    # Настоящая запись на выбранном микрофоне открывается и закрывается.
    recorder.start()
    recording = recorder.is_recording()
    recorder.close()
    check("запись открывается на выбранном микрофоне", recording is True)


def test_menu_microphone() -> None:
    print("меню трея:")
    import pystray

    from dictation.tray import TrayApp

    with tempfile.TemporaryDirectory() as tmp:
        original_path = config.CONFIG_PATH
        original_local = config.LOCAL_CONFIG_PATH
        try:
            config.CONFIG_PATH = Path(tmp) / "config.json"
            config.LOCAL_CONFIG_PATH = Path(tmp) / "config.local.json"
            changed: list[str] = []
            app = TrayApp(
                project_root=Path(tmp),
                status_text="x",
                on_exit=lambda: None,
                on_microphone_change=changed.append,
            )
            menu = app._build_menu()
            texts = [str(item.text) for item in menu.items]
            check("пункт микрофона есть", "Микрофон" in texts, texts)
            mic_item = next(item for item in menu.items if str(item.text) == "Микрофон")
            submenu = (mic_item.submenu or mic_item._action)(None, mic_item)
            labels = [str(i.text) for i in submenu.items if i.__name__ != "SEPARATOR"]
            print(f"       микрофоны: {labels}")
            check("есть выбор «как в Windows»", labels and labels[0].startswith("Как в Windows"))
            check("список микрофонов не пуст", len(labels) > 1, labels)
            if len(submenu.items) > 2:
                target = submenu.items[2]
                target._action(None, target)
                saved = config.load_config()["microphone"]
                check("выбор записан в настройки", bool(saved), saved)
                check("запись узнала о смене", changed == [saved], changed)
        finally:
            config.CONFIG_PATH = original_path
            config.LOCAL_CONFIG_PATH = original_local


def main() -> int:
    test_no_silence_gate()
    test_device_choice()
    test_menu_microphone()
    print()
    if FAILURES:
        print(f"ПРОВАЛЕНО: {len(FAILURES)}")
        for name in FAILURES:
            print(f"  - {name}")
        return 1
    print("ОК: запись идёт с выбранного микрофона, тишину не проверяем")
    return 0


if __name__ == "__main__":
    sys.exit(main())
