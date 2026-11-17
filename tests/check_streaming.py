"""Проверка потокового движка: кормим запись кусками, смотрим текст и задержку.

Запуск: `.venv\\Scripts\\python.exe tests\\check_streaming.py [файл.wav]`

Запись подаётся так, как её отдаёт микрофон: кусками по 10 мс в темпе
настоящей речи. Проверяется то, ради чего всё делалось: что текст идёт по
ходу, а не в конце, что он не теряется и что модель успевает за речью.
"""

from __future__ import annotations

import sys
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np

from dictation.streaming import StreamingTranscriber, merged_text

STEP = 160  # 10 мс при 16 кГц — так отдаёт микрофон


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as wf:
        pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
    return pcm.astype(np.float32) / 32768.0


def check(ok: bool, message: str) -> None:
    print(f"  {'ок' if ok else 'НЕТ'}  {message}")
    if not ok:
        globals()["FAILED"] = True


FAILED = False


def run(model: str, audio: np.ndarray) -> dict:
    updates: list[tuple[float, str]] = []
    began = time.perf_counter()
    fed = 0
    streamer = StreamingTranscriber(model=model, cpu_threads=6)
    streamer.start()
    streamer._on_text = lambda text: updates.append((fed / 16000, text))
    for i in range(0, len(audio), STEP):
        streamer.feed(audio[i : i + STEP])
        fed += len(audio[i : i + STEP])
        # Темп настоящей речи: кусок появляется, когда он прозвучал.
        behind = fed / 16000 - (time.perf_counter() - began)
        if behind > 0:
            time.sleep(behind)
    # Клавишу отпустили: забираем окончательный текст.
    final = streamer.finish()
    return {
        "updates": updates,
        "final": final,
        "elapsed": time.perf_counter() - began,
        "audio_s": len(audio) / 16000,
    }


def main() -> None:
    args = [Path(a) for a in sys.argv[1:]]
    if args:
        files = args
    else:
        folder = ROOT / "записи"
        files = sorted(folder.glob("*.wav"), key=lambda p: p.stat().st_size, reverse=True)[:2]
    for path in files:
        audio = read_wav(path)
        print(f"\n=== {path.name}, {len(audio)/16000:.1f} с")
        for model in ("t-one", "zipformer-small"):
            result = run(model, audio)
            first = result["updates"][0] if result["updates"] else None
            finished = result["final"]
            check(
                first is not None and first[0] < result["audio_s"] * 0.6,
                f"{model}: текст пошёл по ходу (первое слово на {first[0]:.1f} с)"
                if first
                else f"{model}: текст не пошёл по ходу",
            )
            check(bool(finished.strip()), f"{model}: есть окончательный текст")
            check(
                len(finished.split()) > len(first[1].split()) if first else False,
                f"{model}: текст вырос ({len(first[1].split()) if first else 0} → {len(finished.split())} слов)",
            )
            check(
                result["elapsed"] <= result["audio_s"] * 1.15,
                f"{model}: успевает за речью (звук {result['audio_s']:.1f} с, прошло {result['elapsed']:.1f} с)",
            )
            print(f"  окончательно: {merged_text(finished)[:200]}")
    if FAILED:
        print("\nЕсть расхождения.")
        sys.exit(1)
    print("\nВсё прошло: потоковая диктовка идёт по ходу речи.")


if __name__ == "__main__":
    main()
