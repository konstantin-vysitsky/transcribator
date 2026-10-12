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
# Сколько записей держим, если распознавание не удалось. Больше — росла бы
# папка; меньше — можно потерять то, что ещё не разобрали руками.
KEEP_LIMIT = 20


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

    def discard(self, path: Path | None) -> None:
        """Удалить запись: распознавание прошло, файл больше не нужен."""
        if path is None:
            return
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    def pending(self) -> list[Path]:
        """Записи, оставшиеся от неудачных попыток. Новые — первыми."""
        if not self._dir.is_dir():
            return []
        try:
            files = [p for p in self._dir.glob("*.wav") if p.is_file()]
        except OSError:
            return []
        return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)

    def trim(self, limit: int = KEEP_LIMIT) -> None:
        """Не дать папке расти без предела.

        Удаляются самые старые записи. Это осознанный компромисс: держать всё
        нельзя, а место на диске кончится молча и в самый неподходящий момент.
        """
        files = self.pending()
        for old in files[limit:]:
            try:
                old.unlink(missing_ok=True)
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
