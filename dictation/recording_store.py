"""Сохранение записанного аудио на диск.

Аудио живёт только в оперативной памяти, пока идёт распознавание. Любой сбой на
этом пути — падение, повторное нажатие горячей клавиши, закрытие приложения —
уничтожал запись без следа. Восстановить её было неоткуда.

Здесь запись кладётся в файл **сразу после остановки и до распознавания**.
Файл переживает падение, перезапуск и что угодно ещё. После успешного
распознавания он удаляется, чтобы папка не росла.

Формат — WAV (16 бит, тот же поток, что идёт в модель). Формат выбран ради
того, чтобы запись можно было открыть любым проигрывателем без конвертации:
достать важную диктовку руками — половина смысла этой затеи.
"""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000
# Сколько записей держим. Запас намеренно большой: записи нужны не только как
# страховка от сбоя, но и как примеры своей речи, которые достают руками.
# Вытесняются по одной, самые старые — папка не пустеет скачком.
KEEP_LIMIT = 40


class RecordingStore:
    """Папка с записями и их учёт."""

    def __init__(self, project_root: Path) -> None:
        self._dir = project_root / "записи"

    @property
    def directory(self) -> Path:
        return self._dir

    def _ensure(self) -> None:
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    def save(self, audio: np.ndarray, stamp: str) -> Path | None:
        """Записать аудио в файл. Возвращает путь либо None, если не вышло.

        Ошибка записи не должна мешать диктовке: не сохранилось — работаем
        дальше как раньше, в памяти.
        """
        self._ensure()
        path = self._dir / f"{stamp}.wav"
        try:
            clipped = np.clip(audio, -1.0, 1.0)
            pcm = (clipped * 32767.0).astype("<i2")
            with wave.open(str(path), "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(SAMPLE_RATE)
                handle.writeframes(pcm.tobytes())
        except (OSError, ValueError):
            return None
        return path

    def save_text(self, path: Path | None, text: str) -> None:
        """Положить расшифровку рядом с записью.

        Запись остаётся и после удачной расшифровки, поэтому текст нужен в
        папке: иначе непонятно, что в ней за звук, и достать пример речи
        становится тяжело.
        """
        if path is None or not text.strip():
            return
        try:
            path.with_suffix(".txt").write_text(text.rstrip() + "\n", encoding="utf-8")
        except OSError:
            pass

    def keep(self, path: Path | None) -> None:
        """Оставить запись на диске и подрезать папку до предела.

        Записи не удаляются после распознавания: папка «записи» нужна ещё и как
        собрание своей речи — примеров, надиктовок, удачных формулировок. Место
        на диске ограничено, поэтому лишнее вытесняется по одной, самые старые.
        """
        if path is None:
            return
        self.trim()

    def pending(self) -> list[Path]:
        """Все сохранённые записи. Новые — первыми."""
        if not self._dir.is_dir():
            return []
        try:
            files = [p for p in self._dir.glob("*.wav") if p.is_file()]
        except OSError:
            return []
        return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)

    def trim(self, limit: int = KEEP_LIMIT) -> None:
        """Не дать папке расти без предела.

        Удаляются самые старые записи — по одной за вызов, а не партией.
        Скачком папка не пустеет, и уже ненужное уходит постепенно: если
        человек что-то искал, у него есть время заметить потерю.

        Вместе с записью убирается и её расшифровка: файл текста без звука
        только сбивает с толку.
        """
        files = self.pending()
        for old in files[limit:]:
            for target in (old, old.with_suffix(".txt")):
                try:
                    target.unlink(missing_ok=True)
                except OSError:
                    pass


def load(path: Path) -> np.ndarray | None:
    """Прочитать запись обратно в том же виде, в каком её ждёт модель."""
    try:
        with wave.open(str(path), "rb") as handle:
            frames = handle.readframes(handle.getnframes())
    except (OSError, wave.Error):
        return None
    if not frames:
        return None
    pcm = np.frombuffer(frames, dtype="<i2")
    return (pcm.astype(np.float32) / 32767.0)
