from __future__ import annotations

import threading
from collections import deque

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000
CHANNELS = 1
DTYPE = "float32"
PREROLL_MS = 200
PREROLL_SAMPLES = int(SAMPLE_RATE * PREROLL_MS / 1000)


class Recorder:
    def __init__(self, min_duration_s: float = 0.3, preroll: bool = True) -> None:
        self._min_duration_s = min_duration_s
        self._preroll = preroll
        self._lock = threading.Lock()
        self._chunks: list[np.ndarray] = []
        self._ring: deque[np.ndarray] = deque(maxlen=max(1, PREROLL_SAMPLES // 1024 + 1))
        self._stream: sd.InputStream | None = None
        self._recording = False

    def _callback(self, indata: np.ndarray, _frames: int, _time, status) -> None:
        if status:
            pass
        chunk = indata[:, 0].copy()
        with self._lock:
            if self._recording:
                self._chunks.append(chunk)
            elif self._preroll:
                self._ring.append(chunk)

    def start(self) -> bool:
        """Начать запись. False — запись уже идёт, начинать заново нельзя.

        Повторный вызов раньше молча обнулял буфер (`self._chunks = []`) и
        стирал уже записанное. Именно так пропала длинная диктовка: второе
        нажатие горячей клавиши вместо остановки начало запись заново и
        уничтожило аудио, которое ещё не успели распознать.

        Возврат False вместо исключения: вызывающий код решает, что делать, а
        падать на повторном нажатии приложение не должно.
        """
        with self._lock:
            if self._recording:
                return False
            self._chunks = []
            if self._preroll and self._ring:
                preroll = np.concatenate(list(self._ring))
                if len(preroll) > PREROLL_SAMPLES:
                    preroll = preroll[-PREROLL_SAMPLES:]
                self._chunks.append(preroll)
            self._recording = True
            if self._stream is None:
                self._stream = sd.InputStream(
                    samplerate=SAMPLE_RATE,
                    channels=CHANNELS,
                    dtype=DTYPE,
                    callback=self._callback,
                )
                self._stream.start()
            elif not self._stream.active:
                self._stream.start()
        return True

    def is_recording(self) -> bool:
        with self._lock:
            return self._recording

    def stop(self) -> np.ndarray | None:
        with self._lock:
            self._recording = False
            if not self._chunks:
                return None
            audio = np.concatenate(self._chunks)
            self._chunks = []
        if len(audio) < int(self._min_duration_s * SAMPLE_RATE):
            return None
        return audio

    def close(self) -> None:
        with self._lock:
            self._recording = False
            if self._stream is not None:
                self._stream.stop()
                self._stream.close()
                self._stream = None
            self._chunks = []
            self._ring.clear()
