"""Проверка: молчащий микрофон называется молчащим, а тихую речь не съедает.

Отказ, с которого это началось: человек нажимает F7, говорит, а текста нет.
В журнале оставались «запись начата» и «запись пуста» — а причина (микрофон
отдаёт тишину) не была видна ниоткуда.

Второй отказ, 3 ноября: два «раз, два, три» легли в папку записей с
пометкой «тишина». Порог тишины стоял на 25 % шкалы, а тихий микрофон
набрал 7.9 % — модель на этом же файле текст узнала. Теперь решает шум
комнаты, записанный до микрофона.

Запуск: .venv\\Scripts\\python.exe tests\\check_silent_mic.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dictation import recorder as rec_mod  # noqa: E402
from dictation.recorder import _click_only, peak_percent, speech_rooms, whisper_rooms  # noqa: E402

# Порог из main.py: сколько кусков по 50 мс должны быть громче шума.
MIN_SPEECH_ROOMS = 4
SAMPLE_RATE = 16000
FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(("  ок   " if condition else "  ПЛОХО ") + name + ("" if condition else f" {detail}"))
    if not condition:
        FAILURES.append(name)


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


def _room_noise(seconds: float = 0.2) -> np.ndarray:
    """Настоящий шум комнаты с микрофона; без микрофона — цифровая тишь."""
    try:
        import sounddevice as sd

        return sd.rec(
            int(seconds * SAMPLE_RATE),
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            device=0,
            blocking=True,
        )[:, 0]
    except Exception:  # noqa: BLE001 — проверка не должна зависеть от железа
        return np.zeros(int(seconds * SAMPLE_RATE), dtype=np.float32)


def _quiet_speech(peak: float = 0.012) -> np.ndarray:
    """Тихая речь: слоги по 250 мс над слабым шумом, пик задаётся сам."""
    rng = np.random.default_rng(3)
    length = int(7 * SAMPLE_RATE)
    audio = (rng.standard_normal(length) * 0.0003).astype(np.float32)
    moment = np.arange(int(0.25 * SAMPLE_RATE)) / SAMPLE_RATE
    envelope = np.minimum(1, np.minimum(moment / 0.03, (0.25 - moment) / 0.03))
    word = (np.sin(2 * np.pi * 180 * moment) * envelope * peak).astype(np.float32)
    position = int(0.6 * SAMPLE_RATE)
    for _ in range(3):
        for _syllable in range(4):
            if position + len(word) > length:
                break
            audio[position:position + len(word)] += word
            position += len(word) + int(0.1 * SAMPLE_RATE)
        position += int(0.4 * SAMPLE_RATE)
    return audio


def main() -> int:
    noise = _room_noise()

    # 1. Тишина — распознавать нечего, и это должно быть видно по счёту.
    silence = np.zeros(SAMPLE_RATE * 5, dtype=np.float32)
    check("цифровая тишь не речь", whisper_rooms(silence, silence[:1000]) < MIN_SPEECH_ROOMS)

    # 2. Ровный шум комнаты — тоже не речь, даже громкий.
    rng = np.random.default_rng(1)
    for level in (0.0005, 0.002, 0.004):
        hiss = (rng.standard_normal(SAMPLE_RATE * 4) * level).astype(np.float32)
        rooms = whisper_rooms(hiss, hiss[:SAMPLE_RATE // 5])
        check(f"шум {level * 100:.2f}% не речь", rooms < MIN_SPEECH_ROOMS, str(rooms))

    # 3. Громкая запись проходит: иначе правило съело бы нормальную диктовку.
    sound = _file_sound()
    if sound is None:
        sound = (np.sin(np.arange(SAMPLE_RATE, dtype=np.float32) * 0.1) * 0.2).repeat(5)
    check("громкая речь принята", speech_rooms(sound) >= MIN_SPEECH_ROOMS)

    # 4. Тихая речь проходит: ради этого всё и делалось.
    #    Шум комнаты здесь настоящий — так, как он приходит с микрофона.
    for peak in (0.006, 0.012, 0.03):
        quiet = _quiet_speech(peak)
        rooms = whisper_rooms(quiet, noise if len(noise) else quiet[:1000])
        check(
            f"тихая речь с пиком {peak * 100:.1f}% принята",
            rooms >= MIN_SPEECH_ROOMS,
            f"окон {rooms}, пик {peak_percent(quiet)}%",
        )

    # 5. Щелчок от включения передачи — не речь, хотя и громкий.
    click = np.zeros(SAMPLE_RATE * 2, dtype=np.float32)
    moment = np.arange(600) / SAMPLE_RATE
    click[3000:3600] = (np.sin(2 * np.pi * 3000 * moment) * 0.3).astype(np.float32)
    check("щелчок распознан как щелчок", _click_only(click))
    check("щелчок не речь", whisper_rooms(click, click[:1000]) < MIN_SPEECH_ROOMS)
    check("речь не путается со щелчком", not _click_only(_quiet_speech(0.03)))

    # 6. Запись отдаёт звук и шум вместе: по шуму решается, тихо ли говорят.
    recorder = rec_mod.Recorder(min_duration_s=0.0, preroll=True)
    recorder._chunks = [np.zeros(SAMPLE_RATE, dtype=np.float32)]
    recorder._ring.append(np.zeros(1024, dtype=np.float32))
    recorder._recording = True
    result = recorder.stop_with_noise()
    check(
        "остановка отдаёт звук и фон",
        result is not None and result[0] is not None and result[1] is not None,
        str(None if result is None else [type(part) for part in result]),
    )

    # 7. Проба микрофона отвечает и говорит, с какого устройства слушала.
    probe = rec_mod.ping_sources(window_s=0.1)
    if "имя" not in probe:
        check("проба назвала устройство", False, str(probe))
    else:
        print(f"       проба: {probe}")

    print()
    if FAILURES:
        print(f"ПРОВАЛЕНО: {len(FAILURES)}")
        for name in FAILURES:
            print(f"  - {name}")
        return 1
    print("ОК: молчащий микрофон отличается от тихой речи")
    return 0


if __name__ == "__main__":
    sys.exit(main())
