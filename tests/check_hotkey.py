"""Проверка, доходят ли нажатия клавиш до слушателя.

Запуск: `.venv\\Scripts\\python.exe tests\\check_hotkey.py [секунды]`

Печатает каждое нажатие, которое видит `pynput`, и отдельно — срабатывания
горячей клавиши из настроек. Это отвечает на вопрос «нажал F7 и ничего»:
клавиша не доходит до приложения либо доходит, но обработчик не срабатывает.

Ничего не записывает и не вставляет. Только слушает.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pynput import keyboard  # noqa: E402

from dictation import config  # noqa: E402
from dictation.hotkey import HotkeyListener, format_hotkey_label  # noqa: E402

seen_keys: list[str] = []
triggers: list[str] = []


def main() -> int:
    seconds = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    cfg = config.load_config()
    hotkey = cfg["hotkey"]
    mode = cfg["hotkey_mode"]
    print(f"Горячая клавиша из настроек: {format_hotkey_label(hotkey)} (режим {mode})")
    print(f"Слушаю {seconds} секунд. Нажмите {format_hotkey_label(hotkey)}.")

    raw = keyboard.Listener(
        on_press=lambda key: seen_keys.append(str(key)),
        suppress=False,
    )
    raw.start()

    listener = HotkeyListener(
        hotkey=hotkey,
        mode=mode,
        on_record_start=lambda: (triggers.append("старт"), True)[1],
        on_record_stop=lambda: (triggers.append("стоп"), True)[1],
    )
    listener.start()

    try:
        time.sleep(seconds)
    finally:
        listener.stop()
        raw.stop()

    print()
    print(f"Нажатий увидел pynput: {len(seen_keys)}")
    for item in seen_keys[:25]:
        print(f"  клавиша: {item}")
    print(f"Срабатываний горячей клавиши: {len(triggers)} {triggers}")

    if not seen_keys:
        print()
        print("ВЫВОД: клавиатура не доходит до приложения вообще.")
        print("Это не ошибка кода: мешает другое приложение с правами выше")
        print("(запущенное от администратора) либо перехват клавиш.")
        return 1
    if not triggers:
        print()
        print("ВЫВОД: клавиши доходят, но горячая клавиша не совпадает.")
        print("Сверьте раскладку и клавишу в config.json.")
        return 1
    print()
    print("ВЫВОД: горячая клавиша работает.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
