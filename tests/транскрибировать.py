"""Расшифровать запись из папки «записи» вручную.

Запуск:
  .venv\\Scripts\\python.exe tests\\транскрибировать.py              — самую свежую
  .venv\\Scripts\\python.exe tests\\транскрибировать.py <файл.wav>   — конкретную

Текст кладётся рядом с записью, в файл с тем же именем и расширением `.txt`.
Сам WAV не трогается: папка «записи» хранит ещё и примеры своей речи.

Приложение расшифровывает записи само, поэтому этот скрипт нужен для двух
случаев: разобрать то, что не расшифровалось из-за сбоя, и перечитать старую
запись, не включая микрофон.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dictation.recorder import SAMPLE_RATE  # noqa: E402
from dictation.recording_store import load  # noqa: E402
from dictation.transcriber import Transcriber  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Расшифровать сохранённую запись")
    parser.add_argument("path", nargs="?", default=None, help="Файл .wav из папки «записи»")
    parser.add_argument("--model", default="v3_e2e_rnnt")
    parser.add_argument("--threads", type=int, default=6)
    parser.add_argument(
        "--no-fillers",
        action="store_true",
        help="Не убирать слова-паразиты",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
    recordings = root / "записи"

    if args.path:
        target = Path(args.path)
        if not target.is_file():
            target = recordings / args.path
    else:
        files = sorted(
            (p for p in recordings.glob("*.wav") if p.is_file()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not files:
            print(f"В папке {recordings} нет записей.")
            return 1
        target = files[0]
        if len(files) > 1:
            print(f"Записей в папке: {len(files)}. Беру самую свежую.")

    if not target.is_file():
        print(f"Файл не найден: {target}")
        return 1

    print(f"Запись: {target.name}")
    audio = load(target)
    if audio is None:
        print("Не удалось прочитать файл.")
        return 1

    duration = len(audio) / SAMPLE_RATE
    print(f"Длительность: {duration:.1f} с")
    print(f"Загружаю модель ({args.model})…")

    transcriber = Transcriber(
        model=args.model,
        cpu_threads=args.threads,
        remove_fillers_enabled=not args.no_fillers,
    )

    def on_chunk(done: int, total: int) -> None:
        print(f"  часть {done} из {total}")

    started = time.time()
    text = transcriber.transcribe(audio, on_progress=on_chunk)
    elapsed = time.time() - started

    out_path = target.with_suffix(".txt")
    out_path.write_text(text.rstrip() + "\n", encoding="utf-8")

    print()
    print(f"Готово за {elapsed:.1f} с")
    print(f"Текст: {out_path}")
    print()
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
