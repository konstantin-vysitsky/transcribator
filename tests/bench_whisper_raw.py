"""Сколько времени Whisper тратит на запись: один проход, без потоковой нарезки.

Запуск: `.venv\\Scripts\\python.exe tests\\bench_whisper_raw.py small medium turbo`
"""

from __future__ import annotations

import sys
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np
from faster_whisper import WhisperModel


def pick_recording() -> Path:
    files = sorted((ROOT / "записи").glob("*.wav"), key=lambda p: p.stat().st_size, reverse=True)
    for path in files:
        seconds = (path.stat().st_size - 44) / 2 / 16000
        if 15 < seconds < 30:
            with wave.open(str(path), "rb") as wf:
                data = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
            if float(np.abs(data).max()) > 200:
                return path
    raise SystemExit("нет записи 15–30 секунд")


def main() -> int:
    names = sys.argv[1:] or ["small", "medium"]
    path = pick_recording()
    with wave.open(str(path), "rb") as wf:
        audio = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    duration = len(audio) / 16000
    print(f"запись: {path.name}, {duration:.1f} с")

    for name in names:
        started = time.monotonic()
        try:
            model = WhisperModel(name, device="cpu", compute_type="int8", cpu_threads=8)
        except Exception as exc:  # noqa: BLE001 — модель может не скачаться
            print(f"{name}: не загрузилась — {type(exc).__name__}: {exc}")
            continue
        load = time.monotonic() - started
        started = time.monotonic()
        segments, _ = model.transcribe(
            audio, language="ru", beam_size=1, without_timestamps=True, vad_filter=False
        )
        text = " ".join(segment.text.strip() for segment in segments).strip()
        spent = time.monotonic() - started
        print(f"\n{name}: загрузка {load:.1f} с, счёт {spent:.1f} с, доля от речи {spent / duration:.2f}")
        print(" ", text[:300])
        del model
    return 0


if __name__ == "__main__":
    sys.exit(main())
