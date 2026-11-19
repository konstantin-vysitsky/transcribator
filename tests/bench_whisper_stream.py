"""Проба: потоковый Whisper на настоящей записи, по-настоящему в реальном времени.

Запуск: `.venv\\Scripts\\python.exe tests\\bench_whisper_stream.py [модель]`

Что делает: берёт запись, кормит её модели кусками с той же скоростью, с
какой человек говорит, и по правилу «локального согласия» отдаёт только
то, что подтвердилось в двух чтениях подряд. Считает, сколько времени
занимает счёт и с какой задержкой появляются слова.

Печатает: подтверждённый текст по ходу (со знаками препинания), задержку
и загрузку.
"""

from __future__ import annotations

import sys
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np

# Сколько звука набирать перед каждым чтением модели. Меньше — меньше
# задержка, но больше счёта; больше — наоборот.
MIN_CHUNK = 0.8
SAMPLE_RATE = 16000


def pick_recording() -> Path:
    files = sorted((ROOT / "записи").glob("*.wav"), key=lambda p: p.stat().st_size, reverse=True)
    for path in files:
        seconds = (path.stat().st_size - 44) / 2 / 16000
        if 8 < seconds < 25:
            with wave.open(str(path), "rb") as wf:
                data = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
            if float(np.abs(data).max()) > 200:
                return path
    raise SystemExit("нет записи со звуком 8–25 секунд")


def words_of(text: str) -> list[str]:
    return text.split()


def main() -> int:
    model_name = sys.argv[1] if len(sys.argv) > 1 else "medium"
    path = pick_recording()
    with wave.open(str(path), "rb") as wf:
        audio = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    duration = len(audio) / SAMPLE_RATE
    print(f"запись: {path.name}, {duration:.1f} с")
    print(f"модель: {model_name}, int8, cpu, язык ru")

    from faster_whisper import WhisperModel

    load_started = time.monotonic()
    model = WhisperModel(model_name, device="cpu", compute_type="int8", cpu_threads=8)
    print(f"модель загружена за {time.monotonic() - load_started:.1f} с")

    buffer = np.zeros(0, dtype=np.float32)
    committed_words = 0
    previous: list[str] = []
    decode_seconds = 0.0
    commits: list[tuple[float, str]] = []
    began = time.monotonic()
    fed = 0
    step = int(MIN_CHUNK * SAMPLE_RATE)

    while fed < len(audio):
        piece = audio[fed : fed + step]
        fed += len(piece)
        buffer = np.concatenate([buffer, piece])
        # Ждём, пока этот кусок «прозвучит»: так проверка честная.
        wait = began + fed / SAMPLE_RATE - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        decode_started = time.monotonic()
        segments, _info = model.transcribe(
            buffer,
            language="ru",
            beam_size=1,
            condition_on_previous_text=False,
            vad_filter=False,
            without_timestamps=True,
        )
        text = " ".join(segment.text.strip() for segment in segments).strip()
        decode_seconds += time.monotonic() - decode_started
        words = words_of(text)
        # Локальное согласие: подтверждено то, что совпало с прошлым чтением.
        common = 0
        for a, b in zip(previous, words):
            if a != b:
                break
            common += 1
        if common > committed_words:
            new = words[committed_words:common]
            if new:
                commits.append((time.monotonic() - began, " ".join(new)))
                print(
                    f"  {commits[-1][0]:5.1f} с | {time.monotonic() - began - fed / SAMPLE_RATE:+.2f} с "
                    f"задержки | {' '.join(new)}"
                )
            committed_words = common
        previous = words

    total = time.monotonic() - began
    print()
    print("подтверждённый текст:")
    print(" ".join(piece for _t, piece in commits))
    print()
    print(f"звука: {duration:.1f} с, прошло: {total:.1f} с")
    print(f"счёт занял: {decode_seconds:.1f} с (доля от речи {decode_seconds / duration:.2f})")
    if commits:
        print(f"первая приписка: {commits[0][0]:.1f} с, всего приписок: {len(commits)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
