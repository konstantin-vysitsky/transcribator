"""Сквозная проверка: запись доходит до файла и переживает перезапуск.

Запуск: `.venv\\Scripts\\python.exe tests\\check_recording_live.py [секунды]`

Пишет с настоящего микрофона, останавливает, сохраняет файл — и читает его
обратно так, как это сделало бы новое приложение после падения. Это тот путь,
на котором пропала длинная диктовка, поэтому проверяется он целиком, а не по
частям.

Распознавание не запускается: проверяется сохранность звука, а не модель.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dictation.recorder import SAMPLE_RATE, Recorder  # noqa: E402
from dictation.recording_store import RecordingStore, load  # noqa: E402

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(("  ок   " if condition else "  ПЛОХО ") + name + ("" if condition else f" {detail}"))
    if not condition:
        FAILURES.append(name)


def main() -> int:
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 5.0
    root = Path(__file__).resolve().parent.parent
    store = RecordingStore(root)

    recorder = Recorder(min_duration_s=0.3, preroll=False)
    print(f"Пишу {seconds:g} секунд с микрофона — говорите что-нибудь.")
    if not recorder.start():
        print("Не удалось начать запись: микрофон занят или недоступен.")
        return 1
    time.sleep(seconds)

    # Второе нажатие во время записи не должно ничего стирать.
    second = recorder.start()
    check("повторный запуск во время записи отклонён", second is False, repr(second))

    audio = recorder.stop()
    recorder.close()

    check("звук записан", audio is not None and len(audio) > 0)
    if audio is None:
        print("Записи нет — проверить микрофон.")
        return 1

    duration = len(audio) / SAMPLE_RATE
    print(f"       записано {duration:.1f} с, {len(audio)} отсчётов")
    check("длительность близка к заданной", abs(duration - seconds) < 1.0, f"{duration:.1f}")

    peak = float(np.max(np.abs(audio)))
    print(f"       самая громкая точка: {peak:.4f}")
    check("звук не тишина", peak > 0.001, f"пик {peak}")

    stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
    path = store.save(audio, stamp)
    check("файл создан", path is not None and path.is_file(), str(path))
    if path is None:
        return 1

    # Читаем так, как это сделает приложение после перезапуска.
    back = load(path)
    check("файл читается обратно", back is not None)
    if back is not None:
        check("длина совпадает", len(back) == len(audio), f"{len(back)} != {len(audio)}")
        diff = float(np.max(np.abs(back - audio)))
        print(f"       расхождение после сохранения: {diff:.6f}")
        check("звук не искажён", diff < 1e-3, f"{diff}")

    print(f"       файл: {path}")
    store.discard(path)
    check("после распознавания файл удалён", not path.exists())

    print()
    if FAILURES:
        print(f"ПРОВАЛЕНО: {len(FAILURES)}")
        for name in FAILURES:
            print(f"  - {name}")
        return 1
    print("Всё прошло: запись доходит до файла и читается обратно.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
