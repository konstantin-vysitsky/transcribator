"""Проверка: микрофон открыт заранее и запись стартует без задержки.

Запись раньше открывала вход только в момент нажатия. Пока Windows
открывала вход — а иногда и не открывала вовсе — терялись первые слова.
Теперь входы держатся открытыми, и нажатие только отделяет начало.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from dictation.recorder import (  # noqa: E402
    Recorder,
    device_variants,
    input_device_name,
    input_devices,
    peak_percent,
)


def check(name: str, condition: bool, detail: str = "") -> None:
    print(("ОК      " if condition else "ОШИБКА  ") + name + (f" — {detail}" if detail else ""))
    if not condition:
        raise SystemExit(1)


def main() -> int:
    devices = input_devices()
    check("входы находятся", bool(devices), f"{len(devices)}")

    variants = device_variants("")
    check("способы открытия найдены", bool(variants), str(variants))

    recorder = Recorder(min_duration_s=0.2)
    # Первая запись подготавливает входы.
    check("запись началась", recorder.start())
    time.sleep(0.4)
    first = recorder.stop()
    check("звук вернулся", first is not None and len(first) > 0, f"{0 if first is None else len(first)} сэмплов")

    # Вторая запись — по уже готовому входу: время до первого звука мало.
    started = time.monotonic()
    check("запись началась", recorder.start())
    launch = time.monotonic() - started
    time.sleep(0.5)
    second = recorder.stop()
    check("звук вернулся", second is not None and len(second) > 0)
    check("нажатие не ждёт открытия", launch < 0.15, f"{launch * 1000:.0f} мс")

    peak = peak_percent(np.concatenate([first, second]))
    print(f"громкость записи: {peak}% шкалы")
    print(f"микрофон в журнале: {recorder.input_device_name()}")

    recorder.close()

    # Выбор устройства применился без перезапуска.
    before = input_device_name()
    target = next((d for d in devices if d["name"] != before), None)
    if target is not None:
        recorder = Recorder(min_duration_s=0.2)
        recorder.start()
        recorder.stop()
        check("до выбора запись идёт с системного", recorder.input_device_name() == before, before)
        recorder.set_device(target["name"])
        check("выбор микрофона принят", input_device_name(target["name"]) == target["name"], target["name"])
        check("запись началась на выбранном", recorder.start())
        time.sleep(0.3)
        recorder.stop()
        check("журнал называет выбранный", recorder.input_device_name() == target["name"])
        recorder.close()

    print("ОК: вход открыт заранее, нажатие стартует сразу, выбор микрофона работает")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
