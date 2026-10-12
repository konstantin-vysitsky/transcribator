"""Проверка, что запись не теряется.

Запуск: `.venv\\Scripts\\python.exe tests\\check_recording.py`

Разбирается случай, из-за которого пропала длинная диктовка: второе нажатие
горячей клавиши вместо остановки начало запись заново и стёрло буфер, который
ещё не успели распознать. Здесь проверяется, что такого больше не происходит,
и что аудио переживает падение процесса.

Микрофон не нужен: буфер наполняется напрямую. Окна не открываются.
"""
from __future__ import annotations

import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dictation.hotkey import HotkeyListener  # noqa: E402
from dictation.recorder import SAMPLE_RATE, Recorder  # noqa: E402
from dictation.recording_store import RecordingStore, load  # noqa: E402

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(("  ок   " if condition else "  ПЛОХО ") + name + ("" if condition else f" {detail}"))
    if not condition:
        FAILURES.append(name)


def test_repeated_start_keeps_audio() -> None:
    print("повторный запуск записи:")
    recorder = Recorder(min_duration_s=0.0, preroll=False)
    # Буфер наполняем как это делает звуковой поток.
    recorder._chunks = [np.zeros(SAMPLE_RATE, dtype=np.float32)]  # 1 секунда
    recorder._recording = True

    started = recorder.start()
    check("повторный запуск отклонён", started is False, repr(started))
    check("буфер не обнулён", len(recorder._chunks) == 1, str(len(recorder._chunks)))

    audio = recorder.stop()
    check("аудио сохранилось целиком", audio is not None and len(audio) == SAMPLE_RATE,
          str(None if audio is None else len(audio)))

    check("после остановки запуск снова возможен", recorder.start() is True)
    recorder._recording = False


def test_audio_survives_crash() -> None:
    print("аудио переживает падение:")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        store = RecordingStore(root)
        audio = (np.sin(np.linspace(0, 40, SAMPLE_RATE)).astype(np.float32) * 0.5)

        path = store.save(audio, "2026-10-12_03-31-47")
        check("файл создан", path is not None and path.is_file(), str(path))
        check("папка записей создана", store.directory.is_dir())

        if path is not None:
            # Читаем так, как будто процесс упал и его перезапустили заново.
            back = load(path)
            check("файл читается обратно", back is not None)
            if back is not None:
                check("длина совпадает", len(back) == len(audio), f"{len(back)} != {len(audio)}")
                # 16 бит даёт погрешность квантования около 3e-5.
                diff = float(np.max(np.abs(back - audio)))
                check("звук не искажён", diff < 1e-3, f"расхождение {diff}")

            with wave.open(str(path), "rb") as handle:
                check("частота 16000", handle.getframerate() == SAMPLE_RATE)
                check("моно", handle.getnchannels() == 1)
                check("16 бит", handle.getsampwidth() == 2)

        check("запись видна в списке", len(store.pending()) == 1)

        # Запись остаётся и после расшифровки: папка «записи» служит ещё и
        # собранием своей речи, откуда достают примеры. Рядом кладётся текст,
        # иначе непонятно, что за звук лежит в файле.
        store.save_text(path, "расшифровка для проверки")
        check("расшифровка легла рядом", path is not None and path.with_suffix(".txt").is_file())
        store.keep(path)
        check("после распознавания запись осталась", path is not None and path.exists())
        check("список не опустел", len(store.pending()) == 1)


def test_pending_and_trim() -> None:
    print("учёт записей:")
    with tempfile.TemporaryDirectory() as tmp:
        store = RecordingStore(Path(tmp))
        audio = np.zeros(SAMPLE_RATE, dtype=np.float32)
        paths = [store.save(audio, f"2026-10-12_03-00-{n:02d}") for n in range(5)]
        check("сохранено пять записей", all(p is not None for p in paths))
        check("список видит все", len(store.pending()) == 5, str(len(store.pending())))

        store.trim(limit=3)
        check("лишние удалены", len(store.pending()) == 3, str(len(store.pending())))

        # Вместе с записью уходит и её расшифровка: текст без звука сбивает.
        kept = store.pending()[0]
        assert kept is not None
        store.save_text(kept, "текст")
        store.trim(limit=1)
        remaining = store.pending()
        check("осталась одна запись", len(remaining) == 1, str(len(remaining)))
        check(
            "расшифровка удалённых тоже убрана",
            all(not p.with_suffix(".txt").exists() for p in remaining if p != kept),
        )

        check("пустая папка не роняет список", RecordingStore(Path(tmp) / "нет").pending() == [])


def test_hotkey_toggle_does_not_stick() -> None:
    print("горячая клавиша не залипает:")
    events: list[str] = []
    stop_allowed = {"value": False}

    def start() -> bool:
        events.append("старт")
        return True

    def stop() -> bool:
        events.append("стоп")
        return stop_allowed["value"]

    listener = HotkeyListener(hotkey="f7", mode="toggle",
                              on_record_start=start, on_record_stop=stop)
    # Нажимаем трижды: старт, стоп (отказ), старт.
    for _ in range(3):
        listener._combo_triggered = False
        listener._keys_down.clear()
        listener._on_press(None)
    # Первое нажатие с key=None отбрасывается: нужна настоящая клавиша.
    check("нулевая клавиша игнорируется", events == [], repr(events))

    from pynput.keyboard import Key

    for _ in range(3):
        listener._combo_triggered = False
        listener._keys_down.clear()
        listener._on_press(Key.f7)

    check(
        "после отказа остановки следующее нажатие снова начинает",
        events == ["старт", "стоп", "старт"],
        repr(events),
    )
    # Запись сейчас действительно идёт: «старт» прошёл, а «стоп» отказал.
    # Значит, следующее нажатие обязано остановить, а не начать заново.
    check("слушатель знает, что запись идёт", listener._recording is True)

    events.clear()
    stop_allowed["value"] = True
    listener._combo_triggered = False
    listener._keys_down.clear()
    listener._on_press(Key.f7)
    check("следующее нажатие останавливает", events == ["стоп"], repr(events))
    check("слушатель понимает, что запись кончилась", listener._recording is False)

    # И дальше чередование обычное.
    events.clear()
    for _ in range(4):
        listener._combo_triggered = False
        listener._keys_down.clear()
        listener._on_press(Key.f7)
    check(
        "чередование старт/стоп устойчиво",
        events == ["старт", "стоп", "старт", "стоп"],
        repr(events),
    )


def main() -> int:
    test_repeated_start_keeps_audio()
    test_audio_survives_crash()
    test_pending_and_trim()
    test_hotkey_toggle_does_not_stick()
    print()
    if FAILURES:
        print(f"ПРОВАЛЕНО: {len(FAILURES)}")
        for name in FAILURES:
            print(f"  - {name}")
        return 1
    print("Всё прошло.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
