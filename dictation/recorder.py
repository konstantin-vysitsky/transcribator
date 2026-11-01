from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable
from typing import Any

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000
CHANNELS = 1
DTYPE = "float32"
PREROLL_MS = 200
PREROLL_SAMPLES = int(SAMPLE_RATE * PREROLL_MS / 1000)

# Пороги громкости в процентах от полной шкалы: ниже — тишина, выше — звук.
# В тех же процентах показывает уровень звукозапись Windows. 20 % — шум
# тихой комнаты у веб-камеры C922, 40 % уверенно слышная речь.
SILENCE_PERCENT = 20.0
SPEECH_PERCENT = 40.0

# Окно пробы «слышно ли хоть что-нибудь». Короткое намеренно: проба идёт
# внутри обработки диктовки, и человек ждёт её ответа.
PROBE_WINDOW_S = 0.05

# Сколько тишины оставить до и после голоса, когда обрезаются края записи.
MARGIN_S = 0.5


def _default_input_signature() -> str:
    """Опознать микрофон, который сейчас выбран в системе.

    Сравнивается имя, а не номер устройства: номера присваиваются заново,
    когда микрофон втыкают или вынимают, и один и тот же номер после
    переподключения может указывать на другое железо. Имя остаётся именем.
    """
    try:
        return str(sd.query_devices(kind="input")["name"])
    except (sd.PortAudioError, ValueError, TypeError):
        return ""


def _loud(samples: np.ndarray, noise: np.ndarray | None = None) -> bool:
    """Есть ли в куске звук громче шума.

    Просто «громче порога тишины» не годится: у веб-камеры C922 шум громче
    этого порога, и тогда звучащим оказалось бы каждое окно. Порог берётся
    по шуму самого устройства и не опускается ниже порога тишины.
    """
    peak = float(np.max(np.abs(samples))) * 100.0
    floor = SILENCE_PERCENT
    if noise is not None and len(noise):
        quiet = float(np.percentile(np.abs(noise), 10)) * 100.0
        floor = max(floor, quiet * 2.0)
    return peak > floor


def loud_windows(data: np.ndarray, noise: np.ndarray | None = None) -> int:
    """Сколько окон в куске звука было громче шума.

    `data` — звук в тех же единицах, что у модели: от -1 до 1. `noise` —
    кусок тишины с того же устройства, если он есть.
    """
    if len(data) == 0:
        return 0
    window = max(4, int(SAMPLE_RATE * PROBE_WINDOW_S))
    loud = 0
    for start in range(0, len(data) - window + 1, window):
        if _loud(data[start:start + window], noise):
            loud += 1
    return loud


def ping_sources(window_s: float = 0.3) -> dict[str, Any]:
    """Послушать микрофон и сказать, сколько окон было не тишиной.

    Нужно ради записи, которая кончилась пустой. Тогда непонятно, кто виноват:
    микрофон молчит сам (провод, драйвер, другое приложение), или молчал
    человек. Одно звучание сразу отличает одно от другого. Слушаются два
    источника: тот, с которого пишет диктовка, и microsoft sound mapper —
    так в Windows зовётся «системный» вход. Если молчат оба, дело в
    микрофоне, а не в том, как его открыли.
    """
    result: dict[str, Any] = {}
    index: int | None = None
    try:
        found = sd.query_devices(kind="input")
        index = int(found["index"])
        result["имя"] = found["name"]
    except (sd.PortAudioError, ValueError, TypeError, KeyError):
        pass
    for label, device in (
        ("диктовка", index),
        ("sound mapper", None),
    ):
        kwargs: dict[str, object] = {}
        if device is not None:
            kwargs["device"] = device
        try:
            data = sd.rec(
                int(window_s * SAMPLE_RATE),
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype="int16",
                blocking=True,
                **kwargs,
            )
        except Exception:  # noqa: BLE001 — проба не должна ронять запись
            continue
        sound = data[:, 0].astype(np.float32) / 32768.0
        if len(sound):
            # Число строкой: в журнале нужно увидеть, сколько было слышно,
            # а не разбираться, насколько оно велико по сравнению с порогом.
            result[label] = round(float(np.max(np.abs(sound))) * 100.0, 1)
        else:
            result[label] = 0.0
    return result


def speech_rooms(audio: np.ndarray) -> int:
    """Сколько мест в записи были громче шума.

    Слишком короткая или пустая запись отбрасывалась и раньше. Но запись
    нормальной длины, в которой нет ни одного громкого места, чаще всего
    значит, что микрофон молчал: распознавать в ней нечего, и звать модель
    значит спрятать причину. Человек при этом мог просто промолчать — это
    и нельзя отличить, поэтому запись остаётся на диске, а в журнале
    сказано, что в ней тишина.
    """
    if len(audio) == 0:
        return 0
    return loud_windows(np.clip(audio, -1.0, 1.0).astype(np.float32))


def trim_silence(audio: np.ndarray, noise: np.ndarray | None = None) -> np.ndarray:
    """Обрезать тихие края записи, не трогая середину.

    Запись начинается раньше, чем человек заговорил, и кончается позже —
    хвост и начало обычно тишина. Модели они отдаются зря, а на коротком
    «раз два три» тишина вокруг занимает почти всю запись. Внутри записи
    не выбрасывается ничего: пауза между словами — тоже речь.
    """
    if len(audio) == 0:
        return audio
    window = max(4, int(SAMPLE_RATE * PROBE_WINDOW_S))
    loud_starts = [
        start
        for start in range(0, len(audio) - window + 1, window)
        if _loud(audio[start:start + window], noise)
    ]
    if not loud_starts:
        return audio
    margin = int(MARGIN_S * SAMPLE_RATE)
    begin = max(0, loud_starts[0] - margin)
    finish = min(len(audio), loud_starts[-1] + window + margin)
    return audio[begin:finish]


class Recorder:
    def __init__(self, min_duration_s: float = 0.3, preroll: bool = True) -> None:
        self._min_duration_s = min_duration_s
        self._preroll = preroll
        self._lock = threading.Lock()
        self._chunks: list[np.ndarray] = []
        self._ring: deque[np.ndarray] = deque(maxlen=max(1, PREROLL_SAMPLES // 1024 + 1))
        self._stream: sd.InputStream | None = None
        # К какому микрофону привязан открытый поток. По этому признаку
        # замечается смена устройства между записями.
        self._opened_signature: str = ""
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
                # Поток переиспользуется между записями, но привязан он к
                # устройству на момент открытия. Микрофон могут выдернуть из
                # USB, вставить обратно, Windows — переключить устройство по
                # умолчанию. Тогда старый поток пишет тишину либо с чужого
                # железа, и снаружи это «диктовка перестала понимать».
                # Поэтому сравниваем, что выбрано сейчас, и при расхождении
                # открываем поток заново уже на новом устройстве.
                desired = _default_input_signature()
                # Пустой признак означает, что поток открыт не нами (его
                # подставили снаружи) либо признак ещё не снят. Судить о смене
                # устройства по нему нельзя — выбрасывать чужой поток без
                # причины незачем.
                if (
                    self._stream is not None
                    and self._opened_signature
                    and desired
                    and desired != self._opened_signature
                ):
                    self._discard_stream()
                if self._stream is None:
                    self._stream = sd.InputStream(
                        samplerate=SAMPLE_RATE,
                        channels=CHANNELS,
                        dtype=DTYPE,
                        callback=self._callback,
                    )
                    self._stream.start()
                    self._opened_signature = desired
                elif not self._stream.active:
                    self._stream.start()
                    self._opened_signature = desired
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
                self._discard_stream()
                raise
        return True

    def _discard_stream(self) -> None:
        """Закрыть поток целиком: он больше не годится.

        Поток, привязанный к пропавшему устройству, не оживает сам и держит
        микрофон занятым. Если оставить его, каждое следующее нажатие
        упирается в ту же ошибку, и запись перестаёт начинаться насовсем.
        """
        stream = self._stream
        self._stream = None
        self._opened_signature = ""
        if stream is None:
            return
        try:
            stream.close()
        except Exception:
            pass

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
            recorded = np.concatenate(self._chunks)
            self._chunks = []
            # Тишина с прелюдией — шум комнаты того же устройства. По нему
            # видно, где кончается шум и начинается голос: у разных
            # микрофонов этот шум разной громкости.
            noise = np.concatenate(list(self._ring)) if self._ring else None
        if len(recorded) < int(self._min_duration_s * SAMPLE_RATE):
            return None
        return trim_silence(recorded, noise)

    def close(self) -> None:
        with self._lock:
            self._recording = False
            stream = self._stream
            if stream is not None:
                try:
                    stream.stop()
                except Exception:
                    pass
            self._discard_stream()
            self._chunks = []
            self._ring.clear()
