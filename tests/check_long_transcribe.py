"""Сквозная проверка распознавания длинной записи настоящим кодом приложения.

Запуск:
  .venv\\Scripts\\python.exe tests\\check_long_transcribe.py            — самая свежая
  .venv\\Scripts\\python.exe tests\\check_long_transcribe.py <файл.wav>

Проверяется то, на чём пропала длинная диктовка: `Transcriber.transcribe`
получает несколько минут звука и обязан вернуть текст, а не зависнуть.

Отдельно проверяется нарезка: куски не длиннее предела, короткая запись идёт
целиком, склейка текста не оставляет двойных пробелов.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dictation import chunking  # noqa: E402
from dictation.recorder import SAMPLE_RATE  # noqa: E402
from dictation.recording_store import load  # noqa: E402
from dictation.transcriber import Transcriber  # noqa: E402

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(("  ок   " if condition else "  ПЛОХО ") + name + ("" if condition else f" {detail}"))
    if not condition:
        FAILURES.append(name)


def test_split_rules() -> None:
    print("правила нарезки:")
    silence = np.zeros(int(40 * SAMPLE_RATE), dtype=np.float32)
    pieces = chunking.split(silence, SAMPLE_RATE)
    check("длинная запись режется", len(pieces) > 1, str(len(pieces)))
    longest = max(len(p) for p in pieces)
    check(
        "кусок не длиннее предела",
        longest <= int(chunking.MAX_CHUNK_S * SAMPLE_RATE) + 1,
        f"{longest / SAMPLE_RATE:.1f} с",
    )
    covered = sum(len(p) for p in pieces)
    check("ничего не потеряно при нарезке", covered == len(silence), f"{covered} != {len(silence)}")

    short = np.zeros(int(5 * SAMPLE_RATE), dtype=np.float32)
    check("короткая идёт одним куском", len(chunking.split(short, SAMPLE_RATE)) == 1)

    empty = np.zeros(0, dtype=np.float32)
    check("пустая не даёт кусков", chunking.split(empty, SAMPLE_RATE) == [])

    check("склейка убирает двойные пробелы", chunking.join_texts(["а  б", " в "]) == "а б в")
    check("склейка пропускает пустые", chunking.join_texts(["а", "", "  ", "б"]) == "а б")


def test_split_on_pause() -> None:
    print("рез по паузам:")
    # Громкий звук с широкой тишиной посередине: разрез должен попасть в неё.
    loud = (np.sin(np.linspace(0, 300, int(20 * SAMPLE_RATE))) * 0.4).astype(np.float32)
    pause = np.zeros(int(6 * SAMPLE_RATE), dtype=np.float32)
    audio = np.concatenate([loud, pause, loud])

    cuts = chunking.find_cuts(audio, SAMPLE_RATE)
    check("разрез найден", len(cuts) >= 1, str(len(cuts)))
    quiet_zone = range(int(20 * SAMPLE_RATE), int(26 * SAMPLE_RATE))
    check(
        "разрез попал в тишину, а не в речь",
        any(cut in quiet_zone for cut in cuts),
        f"разрезы: {[round(c / SAMPLE_RATE, 1) for c in cuts]}",
    )


def main() -> int:
    test_split_rules()
    test_split_on_pause()

    root = Path(__file__).resolve().parent.parent
    recordings = root / "записи"

    if len(sys.argv) > 1:
        target = Path(sys.argv[1])
        if not target.is_file():
            target = recordings / sys.argv[1]
    else:
        files = sorted(
            (p for p in recordings.glob("*.wav") if p.is_file()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not files:
            print()
            print("В папке «записи» нет файлов — проверьте нарезку и завершайте.")
            return 1 if FAILURES else 0
        target = files[0]

    print()
    print(f"живое распознавание: {target.name}")
    audio = load(target)
    if audio is None:
        check("файл читается", False, str(target))
        return 1

    duration = len(audio) / SAMPLE_RATE
    print(f"       длительность {duration:.1f} с")
    pieces = chunking.split(audio, SAMPLE_RATE)
    print(f"       кусков {len(pieces)}")

    transcriber = Transcriber(model="v3_e2e_rnnt", cpu_threads=6, remove_fillers_enabled=True)
    seen: list[tuple[int, int]] = []
    started = time.time()
    text = transcriber.transcribe(audio, on_progress=lambda d, t: seen.append((d, t)))
    elapsed = time.time() - started

    print(f"       распознано за {elapsed:.1f} с")
    check("текст получен", bool(text.strip()), "пусто")
    check("текст длинный", len(text) > 300, f"{len(text)} знаков")
    check("прогресс по кускам сообщался", len(seen) == len(pieces), f"{len(seen)} из {len(pieces)}")
    check("прогресс доходит до конца", seen and seen[-1] == (len(pieces), len(pieces)), str(seen[-1:]))
    check("быстрее реального времени", elapsed < duration, f"{elapsed:.1f} против {duration:.1f}")
    check("двойных пробелов нет", "  " not in text)

    print()
    print("первые 300 знаков:")
    print(text[:300])

    print()
    if FAILURES:
        print(f"ПРОВАЛЕНО: {len(FAILURES)}")
        for name in FAILURES:
            print(f"  - {name}")
        return 1
    print("Всё прошло: длинная запись распознаётся.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
