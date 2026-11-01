"""Проверка: молчащий микрофон называется молчащим, а не «ничего не вставлено».

Отказ, с которого это началось: человек нажимает F7, говорит, а текста нет.
В журнале оставались «запись начата» и «запись пуста» — а причина (микрофон
отдаёт тишину) не была видна ниоткуда. Заодно проверяется, что нормальная
запись не попадает под это правило: в ней звук есть, и он должен пройти.

Запуск: .venv\\Scripts\\python.exe tests\\check_silent_mic.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dictation import recorder as rec_mod  # noqa: E402
from dictation.recorder import speech_rooms  # noqa: E402

# Порог из main.py: сколько кусков по 50 мс должны быть громче шума.
MIN_SPEECH_ROOMS = 4
SAMPLE_RATE = 16000


def _file_sound() -> np.ndarray | None:
    """Любая сохранённая запись с речью — образец «микрофон работает»."""
    folder = ROOT / "записи"
    if not folder.is_dir():
        return None
    for path in sorted(folder.glob("*.wav"), reverse=True):
        data = _read_wav(path)
        if data is not None and speech_rooms(data) >= MIN_SPEECH_ROOMS:
            return data
    return None


def _read_wav(path: Path) -> np.ndarray | None:
    import wave

    try:
        with wave.open(str(path), "rb") as handle:
            frames = handle.readframes(handle.getnframes())
    except (OSError, wave.Error):
        return None
    if not frames:
        return None
    return np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32767.0


def main() -> int:
    failures: list[str] = []

    # 1. Тишина — распознавать нечего, и это должно быть видно по счёту.
    silence = np.zeros(SAMPLE_RATE * 5, dtype=np.float32)
    if speech_rooms(silence) >= MIN_SPEECH_ROOMS:
        failures.append("тишина посчитана речью")

    # 2. Тихий шум комнаты — тоже не речь.
    rng = np.random.default_rng(1)
    hiss = (rng.standard_normal(SAMPLE_RATE * 5) * 0.0005).astype(np.float32)
    if speech_rooms(hiss) >= MIN_SPEECH_ROOMS:
        failures.append("тихий шум посчитан речью")

    # 3. Запись с речью проходит: иначе правило съело бы нормальную диктовку.
    sound = _file_sound()
    if sound is None:
        # Образца нет — проверяем на громком звуке нужной формы.
        sound = (np.sin(np.arange(SAMPLE_RATE, dtype=np.float32) * 0.1) * 0.2).repeat(5)
    if speech_rooms(sound) < MIN_SPEECH_ROOMS:
        failures.append("запись с речью принята за тишину")

    # 4. Проба микрофона отвечает и говорит, с какого устройства слушала.
    probe = rec_mod.ping_sources(window_s=0.1)
    if "имя" not in probe:
        failures.append(f"проба не назвала устройство: {probe}")

    print("тишина:", speech_rooms(silence), "шум:", speech_rooms(hiss), "речь:", speech_rooms(sound))
    print("проба:", probe)
    if failures:
        for failure in failures:
            print("ПРОВАЛ:", failure)
        return 1
    print("ОК: молчащий микрофон отличается от говорящего")
    return 0


if __name__ == "__main__":
    sys.exit(main())
