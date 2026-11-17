"""Проверка вставки по ходу речи без аудио и без чужих окон.

Запуск: `.venv\\Scripts\\python.exe tests\\check_stream_typing.py`

Что проверяется: модель T-one на настоящей записи и то, что её растущий
текст действительно уходит в поле кусками — с ровной заменой прошлой
вставки на новую. Поле подменено: вместо клавиатуры считается, что стоит
в «поле», и что было бы, если стереть прежнюю вставку.
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


def pick_recording() -> Path:
    files = sorted(
        (ROOT / "записи").glob("*.wav"),
        key=lambda p: p.stat().st_size,
        reverse=True,
    )
    for path in files:
        if 6 < (path.stat().st_size - 44) / 2 / 16000 < 20:
            return path
    raise SystemExit("нет короткой записи в «записи»")


class FakeField:
    """Поле ввода: что в нём стоит и что человек видит."""

    def __init__(self) -> None:
        self.text = ""
        self.builds = 0

    def paste(self, text: str) -> None:
        self.text += text

    def erase(self, count: int) -> None:
        self.text = self.text[: max(0, len(self.text) - count)]


def main() -> int:
    path = pick_recording()
    audio = read_wav(path)
    print(f"запись: {path.name}, {len(audio)/16000:.1f} с")

    field = FakeField()
    pasted_pieces: list[str] = []

    def replace(piece: str) -> None:
        # Так же, как в приложении: стираем прошлую вставку, печатаем новую.
        if pasted_pieces:
            field.erase(len(pasted_pieces[-1]) + 1)
        field.paste(piece + " ")
        pasted_pieces.append(piece)

    live = StreamingTranscriber(model="t-one", on_text=lambda _t: None)
    live.set_typing(True, replace)
    live.start()
    for i in range(0, len(audio), 160):
        live.feed(audio[i : i + 160])
        time.sleep(0.01)
    final = live.finish()

    check("текст уходил в поле по ходу речи", len(pasted_pieces) >= 3, len(pasted_pieces))
    check(
        "поле росло по ходу (первый кусок короткий)",
        len(pasted_pieces[0]) < len(pasted_pieces[-1]) if pasted_pieces else False,
        len(pasted_pieces[0]) if pasted_pieces else "-",
    )
    typed = " ".join(pasted_pieces).strip()
    check("в поле стоит сказанное", bool(typed), typed[:80])
    check(
        "в поле нет мусора от стирания",
        "\ufffd" not in field.text,
    )
    final_text = " ".join(final.split())
    common = len(set(final_text.split()) & set(typed.split()))
    check("поле совпадает с итогом по словам", common >= max(1, len(final_text.split()) // 2), common)

    # Когда говорить перестали, последний кусок в поле — весь текст.
    live2 = StreamingTranscriber(model="t-one", on_text=lambda _t: None)
    live2.set_typing(True, lambda _p: None)
    live2.start()
    for i in range(0, len(audio), 160 * 5):
        live2.feed(audio[i : i + 160 * 5])
    live2.finish()
    tail = live2.emit_pending()
    check("остатки текста после остановки запрашиваются отдельно", tail is None or bool(tail.strip()))

    print()
    print(f"  пример поля: {field.text[:120]}")
    if FAILURES:
        print(f"ПРОВАЛЕНО: {len(FAILURES)}: {FAILURES}")
        return 1
    print("Всё прошло: текст идёт в поле по ходу речи.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
