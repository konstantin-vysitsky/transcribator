from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable

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
        # Куда сообщать громкость: панель показывает, что её слышно. Звать
        # необязательно, а без него запись работает как прежде.
        self.on_level: Callable[[float], None] | None = None

    def _callback(self, indata: np.ndarray, _frames: int, _time, status) -> None:
        if status:
            pass
        chunk = indata[:, 0].copy()
        with self._lock:
            recording = self._recording
            if recording:
                self._chunks.append(chunk)
            elif self._preroll:
                self._ring.append(chunk)
        if recording and self.on_level is not None and len(chunk):
            # 300 — тишина, 9000 — громкая речь: этого хватает для столбика
            # на панели. Считаем здесь же, чтобы не заводить второй поток.
            level = float(np.sqrt(np.mean(np.square(chunk))))
            try:
                self.on_level(max(0.0, min(1.0, (level - 300.0) / 8700.0)))
            except Exception:
                self.on_level = None

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
            try:
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
            except Exception:
                # Поток не пошёл — микрофон пропал, устройство занято, звук
                # сменился. Флаг записи снимаем и поток выбрасываем целиком:
                # поток, который не запустился, уже не годится, и держать его
                # значит обречь на ту же ошибку каждое следующее нажатие.
                # Без этого `is_recording()` навсегда отдаёт True, приложение
                # считает, что уже пишет, и молча ничего не делает — это и был
                # отказ «включается, слушает, а потом перестаёт работать».
                self._recording = False
                self._chunks = []
                self._ring.clear()
                stream = self._stream
                self._stream = None
                if stream is not None:
                    try:
                        stream.close()
                    except Exception:
                        pass
                raise
        return True

    def is_recording(self) -> bool:
        with self._lock:
            return self._recording

    def input_device_name(self) -> str:
        """Имя устройства, с которого идёт запись.

        Нужно ради журнала: когда микрофон отключают от USB, Windows молча
        берёт другое устройство по умолчанию — обычно веб-камеру, которая
        пишет тишину. Без имени устройства такая подмена не видна, и поломка
        выглядит как «диктовка перестала понимать».
        """
        stream = self._stream
        if stream is None:
            return ""
        try:
            index = stream.device
            return str(sd.query_devices(index)["name"])
        except (sd.PortAudioError, ValueError, TypeError):
            return ""

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
