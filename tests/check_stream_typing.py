"""Проверка: в поле только дописывается, ничего не стирается.

Запуск: `.venv\\Scripts\\python.exe tests\\check_stream_typing.py`

Модель T-one на настоящей записи, а поле подменено: считается, что в нём
оказалось после дописывания. Нужны две вещи — что поле только растёт и
что в конце диктовки дописывается последнее слово и точка. Стирание и
переписывание человек видел как «всё стирает и пишет заново», и это
мешало больше, чем редкая опечатка.
"""

from __future__ import annotations

import sys
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np

from dictation.streaming import StreamingTranscriber

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: object = "") -> None:
    print(f"  {'ок' if ok else 'НЕТ'}  {name}" + ("" if ok else f"  {detail}"))
    if not ok:
        FAILURES.append(name)


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as wf:
        pcm = wf.readframes(wf.getnframes())
    return np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0


def has_speech(path: Path) -> bool:
    """Есть ли в записи звук: пустая запись проверять нечего.

    Сквозной прогон вставляет запись в отдельное окно, и в папке остаются
    пустые файлы. Брать самую большую запись подряд поэтому нельзя.
    """
    data = read_wav(path)
    return float(np.abs(data).max()) > 0.005


def pick_recording() -> Path:
    files = sorted((ROOT / "записи").glob("*.wav"), key=lambda p: p.stat().st_size, reverse=True)
    for path in files:
        if 6 < (path.stat().st_size - 44) / 2 / 16000 < 20 and has_speech(path):
            return path
    raise SystemExit("нет короткой записи со звуком в «записи»")


def main() -> int:
    path = pick_recording()
    audio = read_wav(path)
    print(f"запись: {path.name}, {len(audio)/16000:.1f} с")

    parts: list[str] = []
    stamps: list[float] = []
    began = time.monotonic()

    def on_append(piece: str) -> None:
        parts.append(piece)
        stamps.append(time.monotonic() - began)

    live = StreamingTranscriber(model="t-one", on_text=lambda _t: None)
    live.set_typing(True, on_append)
    live.start()
    for i in range(0, len(audio), 160):
        live.feed(audio[i : i + 160])
        time.sleep(0.01)
    fed_for = time.monotonic() - began
    final = live.finish()
    tail = live.final_append()
    field = "".join(parts) + (tail or "")

    check("в поле дописывалось по частям", len(parts) >= 3, parts[:4])
    # Главное: слова должны появляться, ПОКА человек говорит. Если первая
    # приписка приходит только к концу записи, потоковой диктовки нет.
    check(
        "первое слово появилось задолго до конца речи",
        bool(stamps) and stamps[0] < fed_for * 0.6,
        (round(stamps[0], 1) if stamps else None, round(fed_for, 1)),
    )
    check(
        "приписки идут по ходу, а не одной пачкой в конце",
        len({round(s, 1) for s in stamps}) >= 4,
        [round(s, 1) for s in stamps[:8]],
    )
    if stamps:
        print(f"       первая приписка: {stamps[0]:.1f} с из {fed_for:.1f} с речи")
    check("ни одна приписка не пустая", all(part.strip() for part in parts), parts[:4])
    check("нет двойных пробелов", "  " not in field.strip(), field[:80])
    check("на конец дописано последнее слово и точка", bool(tail) and field.rstrip().endswith("."), tail)
    # «вот» в диктовке повторяется по-настоящему, и слова «и», «там», «то»
    # соседствуют законно. Повтором считается только слово из трёх букв и
    # больше, поставленное два раза подряд.
    words = field.split()
    repeats = [a for a, b in zip(words, words[1:]) if a == b and len(a.strip(".,")) > 2]
    check("в поле нет повторов подряд", not repeats, repeats[:6])
    model_words = set(final.split())
    stray = [w for w in field.split() if w.strip(".,") not in model_words]
    check("в поле нет слов, которых перепутала модель", not stray, stray[:6])
    print(f"       приписок: {len(parts)}")
    print(f"       поле: {field[:180]}")
    print(f"       итог модели: {' '.join(final.split())[:180]}")

    if FAILURES:
        print(f"\nПРОВАЛЕНО: {len(FAILURES)}: {FAILURES}")
        return 1
    print("\nВсё прошло: поле только дописывается.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
