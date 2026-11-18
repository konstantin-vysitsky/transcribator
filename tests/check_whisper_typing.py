"""Проверка потокового Whisper: слова и знаки по ходу речи.

Запуск: `.venv\\Scripts\\python.exe tests\\check_whisper_typing.py [модель]`

Модель читает настоящую запись в настоящем темпе речи, поле подменено:
собирается то, что в него попало. Смотрится три вещи — слова идут по
ходу, знаки препинания есть, и конец фразы дописывается.
"""

from __future__ import annotations

import sys
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np

from dictation.whisper_stream import WhisperStreaming

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: object = "") -> None:
    print(f"  {'ок' if ok else 'НЕТ'}  {name}" + ("" if ok else f"  {detail}"))
    if not ok:
        FAILURES.append(name)


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as wf:
        pcm = wf.readframes(wf.getnframes())
    return np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0


def pick_recording() -> Path:
    files = sorted((ROOT / "записи").glob("*.wav"), key=lambda p: p.stat().st_size, reverse=True)
    for path in files:
        seconds = (path.stat().st_size - 44) / 2 / 16000
        if 8 < seconds < 20 and float(np.abs(read_wav(path)).max()) > 0.005:
            return path
    raise SystemExit("нет записи со звуком 8–20 секунд")


def main() -> int:
    model = sys.argv[1] if len(sys.argv) > 1 else "whisper-small"
    path = pick_recording()
    audio = read_wav(path)
    duration = len(audio) / 16000
    print(f"запись: {path.name}, {duration:.1f} с; модель: {model}")

    parts: list[str] = []
    stamps: list[float] = []
    began = time.monotonic()

    def on_append(piece: str) -> None:
        parts.append(piece)
        stamps.append(time.monotonic() - began)

    live = WhisperStreaming(model=model, log=lambda m: None)
    live.set_typing(True, on_append)
    live.start()
    for i in range(0, len(audio), 320):
        live.feed(audio[i : i + 320])
        time.sleep(0.02)
    fed_for = time.monotonic() - began
    time.sleep(1.5)
    field_at_pause = "".join(parts)
    full = live.finish()
    tail = live.final_append()
    field = "".join(parts) + (tail or "")

    print(f"  приписок: {len(parts)}")
    print(f"  поле: {field.strip()[:200]}")
    print(f"  весь текст: {full[:200]}")

    check("по ходу речи были приписки", len(parts) >= 3, len(parts))
    check(
        "первая приписка задолго до конца речи",
        bool(stamps) and stamps[0] < fed_for,
        (round(stamps[0], 1) if stamps else None, round(fed_for, 1)),
    )
    check("в поле есть знаки препинания", any(ch in field for ch in ".,!?"), field[:80])
    check("в поле есть большая буква", any(w[:1].isupper() for w in field.split()), field[:80])
    check("на паузе текст тоже дописывался", bool(field_at_pause.strip()), field_at_pause[:80])
    check("текст не обрублен: последнее слово на месте", len(field.split()) >= 8, field[:80])

    if FAILURES:
        print(f"\nПРОВАЛЕНО: {len(FAILURES)}: {FAILURES}")
        return 1
    print("\nВсё прошло: Whisper печатает по ходу, со знаками.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
