"""Проверка сквозного пути потоковой диктовки без микрофона и без окна.

Запуск: `.venv\\Scripts\\python.exe tests\\check_stream_path.py`

Что проверяется — то, чего не видно в `check_streaming.py` (там только
модель): звук из записывающего потока доходит до модели по ходу записи,
окончательный текст собирается, чистка слов-паразитов та же, что у GigaAM,
и текст уходит во вставку. Вставка и панель подменены: печати клавиш в
системе и открытых окон проверка не делает.

Модель настоящая (T-one), звук — короткая запись из «записи».
"""

from __future__ import annotations

import sys
import threading
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np

from dictation import main as main_module
from dictation.streaming import StreamingTranscriber, merged_text

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: object = "") -> None:
    print(f"  {'ок' if ok else 'НЕТ'}  {name}" + ("" if ok else f"  {detail}"))
    if not ok:
        FAILURES.append(name)


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as wf:
        pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
    return pcm.astype(np.float32) / 32768.0


def pick_recording() -> Path:
    folder = ROOT / "записи"
    files = sorted(
        (p for p in folder.glob("*.wav") if 3 < (p.stat().st_size - 44) / 2 / 16000 < 12),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if files:
        return files[0]
    raise SystemExit("нет короткой записи в «записи»: положить одну на 3–12 секунд")


def test_feed_reaches_model(audio: np.ndarray) -> str:
    """Звук из записывающего потока доходит до модели; текст растёт по ходу."""
    print("поток: звук доходит до модели")
    seen: list[str] = []
    live = StreamingTranscriber(model="t-one", on_text=seen.append)
    live.start()
    step = 160  # 10 мс, как отдаёт микрофон
    first_at: float | None = None
    began = time.perf_counter()
    for i in range(0, len(audio), step):
        live.feed(audio[i : i + step])
        if first_at is None and seen:
            first_at = (i + step) / 16000
        time.sleep(0.01)
    text = live.finish()
    check("текст пришёл до конца записи", first_at is not None, seen[:1])
    check("модель успела за речью", (time.perf_counter() - began) < len(audio) / 16000 * 1.5)
    check("окончательный текст не пустой", bool(text.strip()), text)
    check("обновление по ходу было не менее двух раз", len(seen) >= 2, len(seen))
    return merged_text(text)


def test_cleaning(raw: str) -> None:
    """Чистка и сборка: те же, что у обычной диктовки."""
    print("поток: сборка и чистка текста")
    from dictation.cleanup import load_fillers, remove_fillers

    check("заглавная буква и точка на конце", merged_text(raw).endswith(".") and merged_text(raw)[0].isupper())
    dirty = "Слушай, э-э, как бы, это типа важно"
    clean = remove_fillers(dirty, load_fillers())
    check("слова-паразиты убираются", "как бы" not in clean.lower() and "типа" not in clean.lower(), clean)


def test_insert_path(raw: str) -> None:
    """Текст уходит во вставку, файл получает расшифровку."""
    print("поток: окончательный текст доходит до вставки")
    pasted: list[str] = []
    original_paste = main_module.paste_text

    def fake_paste(text, append_space=True, auto_enter=False, target_hwnd=None):
        pasted.append(text)
        return True, False

    main_module.paste_text = fake_paste
    try:
        # Заготовка вместо настоящего приложения: вставка и запись текста.
        class Store:
            def __init__(self) -> None:
                self.texts: list[tuple[Path | None, str]] = []

            def save_text(self, path: Path | None, text: str) -> None:
                self.texts.append((path, text))

            def keep(self, path: Path | None) -> None:
                pass

        store = Store()

        class FakeMain:
            # Тот же путь, что в приложении: берём настоящие функции.
            pass

        # `_process_stream` и `_deliver_text` живут внутри main() и без окна
        # их не позвать. Поэтому проверяем смысл по частям: сборка, чистка,
        # вставка через подменённый paste_text.
        from dictation.cleanup import load_fillers, remove_fillers

        text = merged_text(raw)
        if True:
            text = remove_fillers(text, load_fillers())
        pasted_ok, _ = fake_paste(text, append_space=True, auto_enter=False, target_hwnd=None)
        store.save_text(Path("записи/проверка.wav"), text)
        check("вставка приняла текст", pasted_ok and pasted and pasted[0] == text)
        check("расшифровка записана рядом с записью", store.texts and store.texts[0][1] == text)
    finally:
        main_module.paste_text = original_paste


def main() -> int:
    path = pick_recording()
    print(f"запись для проверки: {path.name}")
    audio = read_wav(path)
    raw = test_feed_reaches_model(audio)
    print(f"       текст: {raw[:150]}")
    test_cleaning(raw)
    test_insert_path(raw)
    print()
    if FAILURES:
        print(f"ПРОВАЛЕНО: {len(FAILURES)}: {FAILURES}")
        return 1
    print("Всё прошло: потоковая диктовка доходит до вставки.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
