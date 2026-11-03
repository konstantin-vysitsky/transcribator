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

# Пороги громкости в процентах от полной шкалы — так же, как показывает
# уровень звукозапись Windows. У веб-камеры C922 шум в тихой комнате идёт
# около четверти шкалы, а сказанное слово поднимает пик над ним заметно:
# вчерашняя проверка живой записи дала 47 % против шума в 4 %. Ниже
# COLD_PERCENT запись считается тишиной, выше — где-то был голос.
COLD_PERCENT = 25.0
SPEECH_PERCENT = 50.0

# Окно пробы «слышно ли хоть что-нибудь». Короткое намеренно: проба идёт
# внутри обработки диктовки, и человек ждёт её ответа.
PROBE_WINDOW_S = 0.05

# Сколько тишины оставить до и после голоса, когда обрезаются края записи.
MARGIN_S = 0.5

# Предел, ниже которого в окне только фон. Всё, что выше его и резко
# выделяется из шума комнаты, уже может быть тихим голосом.
MIN_SPEECH_PERCENT = 0.3

# Устройства, на которых поток уже не открылся. Windows помнит вынутый
# микрофон и показывает его в списке, но запись с него невозможна, и без
# этой памяти каждый старт записи заново тратил бы время на отказ.
BROKEN_DEVICES: set[str] = set()


def _device_key(index: int | None) -> str:
    """Имя устройства для памяти об отказах.

    Имя, а не номер: номера выдаются заново после каждого переподключения,
    и по ним запомнился бы не тот микрофон.
    """
    if index is None:
        return ""
    try:
        return str(sd.query_devices(index)["name"])
    except (sd.PortAudioError, ValueError, TypeError):
        return ""


# Вкусы звука Windows по убыванию предпочтения: WASAPI — новый и быстрый,
# за ним MME (его так же держит и сама Windows). Без DirectSound: он один
# прошёл проверку на запись и стоит последним на случай поломки остальных.
_HOSTAPI_RANK = {"wasapi": 0, "mme": 1, "wdm-ks": 2, "directsound": 3}


def _hostapi_rank(index: int) -> int:
    try:
        name = str(sd.query_hostapis(sd.query_devices(index)["hostapi"])["name"]).lower()
    except (sd.PortAudioError, ValueError, TypeError, KeyError):
        return 9
    for prefix, rank in _HOSTAPI_RANK.items():
        if prefix in name:
            return rank
    return 9


def _is_default_input(index: int | None) -> bool:
    try:
        return int(sd.default.device[0]) == index
    except (TypeError, ValueError, IndexError):
        return False


def device_variants(choice: object = "") -> list[int]:
    """Все способы открыть выбранный микрофон, лучший первым.

    Один и тот же микрофон Windows показывает столько раз, сколько в нём
    способов записи, и по-разному. Самая частая жалоба на «не слышно» —
    микрофон занят: его уже держит другое приложение, и один из способов
    при этом отвечает отказом, хотя остальные работают. Поэтому смена
    идёт по всем способам, а не по одному.

    Выбранное устройство по имени первым; за ним те же имена во всех
    вкусах, чтобы выбирать было из чего.
    """
    try:
        devices = list(sd.query_devices())
    except (sd.PortAudioError, ValueError, TypeError):
        return []
    inputs = []
    for position, device in enumerate(devices):
        try:
            if int(device.get("max_input_channels", 0)) > 0:
                inputs.append((position, str(device.get("name", ""))))
        except (KeyError, TypeError, ValueError):
            continue
    wanted = str(choice if choice is not None else "").strip()
    preferred: str | None = None
    if wanted:
        try:
            preferred = str(devices[int(wanted)]["name"])
        except (ValueError, IndexError, KeyError, TypeError):
            preferred = wanted
    chosen: list[int] = []
    if preferred is not None:
        chosen = [index for index, name in inputs if name == preferred]
        if not chosen:
            return []
        rest = [(index, name) for index, name in inputs if name != preferred]
    else:
        system = [index for index, name in inputs if _is_default_input(index)]
        chosen = system or [index for index, _name in inputs]
        # Системный вход тоже пробуется во всех вкусах: отказ одного не
        # должен значить, что выбранный в Windows микрофон не слышен.
        preferred = ""
        rest = [(index, name) for index, name in inputs if index not in chosen]
    chosen.sort(key=lambda index: _hostapi_rank(index))
    grouped: dict[str, list[int]] = {}
    for index, name in rest:
        grouped.setdefault(name, []).append(index)
    tail = [index for name in grouped for index in sorted(grouped[name], key=_hostapi_rank)]
    return chosen + tail


def _default_input_name() -> str:
    """Имя входа, выбранного в Windows."""
    try:
        return str(sd.query_devices(kind="input")["name"])
    except (sd.PortAudioError, ValueError, TypeError):
        return ""


def input_device_name(choice: object = "") -> str:
    """Имя микрофона, который сейчас записывает диктовку.

    Пустой выбор — микрофон Windows. Выбранный по имени — он же, даже если
    запись пошла через другой вкус того же устройства.
    """
    return str(choice if choice is not None else "").strip() or _default_input_name()


def input_devices() -> list[dict[str, Any]]:
    """Микрофоны, которые сейчас видит система.

    У одной звуковой карты бывает несколько входов (у сенсора — «микрофон»,
    «линейный вход», «микшер»), и номер у каждого свой. Поэтому в список
    идут все входы, а не только «тот, что по умолчанию»: выбрать нужно
    именно тот, в который говорят.
    """
    try:
        devices = sd.query_devices()
    except (sd.PortAudioError, ValueError, TypeError):
        return []
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for device in devices:
        try:
            if int(device.get("max_input_channels", 0)) <= 0:
                continue
            index = int(device["index"])
            name = str(device.get("name", ""))
        except (KeyError, TypeError, ValueError):
            continue
        # Один микрофон Windows отдаёт в списке несколько раз — разными
        # звуковыми подсистемами. Человеку нужен один пункт на микрофон.
        if name in seen:
            continue
        seen.add(name)
        label = f"{name} (#{index})"
        # Вход, который Windows помнит, но которого нет (вынут из USB),
        # открыть нельзя. Сразу говорим об этом в меню, а не после того,
        # как человек выбрал его и получил пустую запись.
        if name in BROKEN_DEVICES:
            label = f"{name} (не работает)"
        result.append({"index": index, "name": name, "label": label})
    return result


def resolve_device(choice: object) -> int | None:
    """Какому устройству отдать запись.

    Пусто — тот микрофон, что выбран в Windows. Имя — устройство с таким
    именем, если оно есть, иначе снова выбор Windows: микрофон могли
    вынуть, и молчать об этом хуже, чем взять другой. Номер — прямое
    указание на устройство.
    """
    wanted = str(choice if choice is not None else "").strip()
    if not wanted:
        return None
    try:
        return int(wanted)
    except ValueError:
        pass
    try:
        devices = sd.query_devices()
    except (sd.PortAudioError, ValueError, TypeError):
        return None
    for device in devices:
        if str(device.get("name", "")) == wanted and int(device.get("max_input_channels", 0)) > 0:
            return int(device["index"])
    return None


def _pick_stream_kwargs(index: int | None) -> dict[str, Any]:
    """Способ открыть устройство, которое умеет отдать 16000.

    Частота у разных входов разная, и устройство, не умеющее 16000, на
    прямой просьбе молчит. Перебор идёт короткой пробой: открыть и сразу
    закрыть, ничего не записывая.
    """
    candidates: list[int] = [SAMPLE_RATE]
    try:
        own = int(float(sd.query_devices(index)["default_samplerate"]))
    except (sd.PortAudioError, ValueError, TypeError, KeyError):
        own = 0
    if own and own != SAMPLE_RATE:
        candidates.append(own)
    for samplerate in candidates:
        try:
            stream = sd.InputStream(
                samplerate=samplerate,
                channels=CHANNELS,
                dtype=DTYPE,
                device=index,
            )
        except sd.PortAudioError:
            continue
        stream.close()
        kwargs: dict[str, Any] = {"device": index}
        if samplerate != SAMPLE_RATE:
            kwargs["samplerate"] = samplerate
        return kwargs
    return {"device": index}


def _pick_stream(
    index: int | None,
    callback: Callable[..., None] | None = None,
) -> sd.InputStream:
    """Открыть поток на микрофоне.

    У сенсора вход умеет и 16000, у веб-камеры — только свою частоту, и
    молчаливая «тишина» бывает именно оттого, что устройство не отдало
    нужную. Поэтому сначала пробуем 16000, а если не вышло — берём частоту
    самого устройства и просим пересчёт к 16000.

    Обработчик звука обязателен: без него поток открывается, но звук
    копит сам в себе и никуда не отдаёт — запись выходит пустой.
    """
    kwargs = _pick_stream_kwargs(index)
    return sd.InputStream(
        samplerate=kwargs.get("samplerate", SAMPLE_RATE),
        channels=CHANNELS,
        dtype=DTYPE,
        device=kwargs["device"],
        callback=callback,
    )


def _pick_rec_kwargs(index: int | None, probe: bool = True) -> dict[str, Any]:
    """То же для `sd.rec`: там пересчёт частоты умеет только он.

    `probe=False` — для записи, а не для пробы: устройство проверяется
    одно, и перебор способов открыть его остаётся за `_pick_stream`.
    """
    kwargs = _pick_stream_kwargs(index) if probe else {"device": index}
    result: dict[str, Any] = {"device": index}
    if kwargs.get("samplerate", SAMPLE_RATE) != SAMPLE_RATE:
        result["samplerate"] = kwargs["samplerate"]
        result["resample"] = True
    return result


def _loud(samples: np.ndarray, noise: np.ndarray | None = None) -> bool:
    """Есть ли в куске звук громче шума.

    «Громче нуля» не годится, а шум почти у всех микрофонов заметный: у
    веб-камеры C922 он колеблется около четверти шкалы, и тогда звучащим
    оказалось бы каждое окно. Ниже шума проверять нечего, поэтому порог
    берётся по шуму самого устройства, но не ниже общего порога тишины.
    """
    peak = float(np.max(np.abs(samples))) * 100.0
    floor = COLD_PERCENT
    if noise is not None and len(noise):
        quiet = float(np.percentile(np.abs(noise), 90)) * 100.0
        floor = max(floor, quiet * 1.3)
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


def peak_percent(audio: np.ndarray) -> float:
    """Самая громкая точка записи в процентах от полной шкалы."""
    if len(audio) == 0:
        return 0.0
    return round(float(np.max(np.abs(audio))) * 100.0, 2)


def whisper_rooms(audio: np.ndarray, noise: np.ndarray | None = None) -> int:
    """Сколько окон звучат так тихо, что обычный порог их не заметил.

    Порог тишины высокий нарочно: шум вентилятора и веб-камеры иначе
    звучал бы как речь. Но тихий микрофон — гарнитура, микрофон веб-камеры,
    гарнитура на минимальном усилении — говорит тише этого порога, и запись
    целиком называлась тишиной, хотя в ней был голос: «раз, два, три»
    набирало 1.2 % шкалы, а порог стоял на 25 %.

    Здесь решает шум комнаты. Человек, который что-то сказал, в любом
    месте записи поднимается над самым громким местом пустой комнаты:
    тише не бывает — иначе его бы никто не услышал. Шум комнаты такой
    высоты сам не бывает, он стоит ниже и ровно.
    """
    if len(audio) == 0:
        return 0
    windows = _window_levels(audio)
    if not len(windows):
        return 0
    if _click_only(audio):
        # Щелчок от включения передачи: он громкий и короткий, но низкие
        # частоты в нём не звучат — человек так не говорит.
        return 0
    level = _floor_percent(noise)
    if level is None:
        level = _floor_percent(audio)
    # Выше шума в два с половиной раза, но не ниже общего предела: в
    # цифровом нуле на фоне нечего, и планка на нуле пропустила бы щелчок.
    rise = max(MIN_SPEECH_PERCENT, (level or 0.0) * 2.5)
    return int(np.count_nonzero(windows > rise))


def _window_levels(audio: np.ndarray) -> np.ndarray:
    """Действующая громкость каждого окна пробы, в процентах шкалы."""
    window = max(4, int(SAMPLE_RATE * PROBE_WINDOW_S))
    parts = [audio[start:start + window] for start in range(0, len(audio) - window + 1, window)]
    if not parts:
        return np.zeros(0, dtype=np.float32)
    levels = [float(np.percentile(np.abs(part), 75)) for part in parts]
    return np.asarray(levels, dtype=np.float32) * 100.0


def _click_only(audio: np.ndarray) -> bool:
    """Громкое в записи — это щелчок, а не голос.

    У голоса есть основной тон: даже «с», «ш» и щелчки языка стоят на
    низах и держат их. У щелчка от включения передачи энергия сдвинута
    вверх, и ниже 250 герц почти пусто. Проверяется только самая громкая
    часть записи: в редких выбросах голоса его и не должно быть.
    """
    if len(audio) == 0:
        return True
    peak = float(np.max(np.abs(audio)))
    if peak <= 0:
        return True
    loud = np.nonzero(np.abs(audio) > peak * 0.5)[0]
    if not len(loud):
        return True
    begin = max(0, int(loud[0]) - 100)
    piece = audio[begin:begin + 512]
    if len(piece) < 64:
        return False
    spectrum = np.abs(np.fft.rfft(piece * np.hanning(len(piece))))
    freqs = np.fft.rfftfreq(len(piece), 1.0 / SAMPLE_RATE)
    total = float(np.sum(spectrum))
    if total <= 0:
        return True
    low = float(np.sum(spectrum[freqs < 250]))
    return low / total < 0.05


def _floor_percent(audio: np.ndarray | None) -> float | None:
    """Самый громкий настоящий звук тишины, в процентах шкалы.

    Порог — не весь кусок тишины: в него попадают щелчки от нажатия
    клавиши и от включения передачи, и по всему куску планка встала бы
    выше, чем нужно. Берётся уровень, который держится чаще остального.
    """
    if audio is None or not len(audio):
        return None
    levels = _window_levels(audio)
    if not len(levels):
        return None
    # Средний, а не верхний уровень: щелчки от нажатия клавиши и от
    # включения передачи занимают пару окон из сотни, и медиана их не видит.
    return float(np.median(levels))


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
        try:
            data = sd.rec(
                int(window_s * SAMPLE_RATE),
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype="int16",
                blocking=True,
                **_pick_rec_kwargs(device),
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

    Режутся только края, и только если они тихие. Запись без единого
    громкого места возвращается целой: тишина этой функцией не лечится,
    а отнять у человека сказанное хуже, чем оставить всё как есть.
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
    def __init__(
        self,
        min_duration_s: float = 0.3,
        preroll: bool = True,
        device: object = "",
    ) -> None:
        self._min_duration_s = min_duration_s
        self._preroll = preroll
        # Выбранный человеком микрофон: пусто — тот, что в Windows.
        self._device_choice = device
        self._lock = threading.Lock()
        # Замок для звука из обработчика потока. Отдельный нарочно:
        # обработчик вызывается из звуковой нити, и если он ждёт замок,
        # который держит тот, кто открывает или закрывает поток, они
        # ждут друг друга навсегда — приложение встаёт намертво.
        self._data_lock = threading.Lock()
        self._chunks: list[np.ndarray] = []
        self._ring: deque[np.ndarray] = deque(maxlen=max(1, PREROLL_SAMPLES // 1024 + 1))
        self._stream: sd.InputStream | None = None
        # Способы держать открытыми выбранный микрофон на будущее. Поток
        # открывается в момент нажатия, и открытие — то самое время, когда
        # первые слова теряются, а иногда и весь вход не отвечает. Здесь
        # способы, которые уже проверены и слышат: их мы держим открытыми
        # заранее, и каждое нажатие лишь дописывает звук.
        self._warm: list[tuple[int, sd.InputStream]] = []
        # Микрофон, выбранный при прошлой записи (уже подставленный).
        self._prepared_choice: object = device
        # Номер устройства, с которого пошла прошлая запись; None — ещё не
        # было записи.
        self._opened_index: int | None = None
        self._recording = False
        # Куда сообщать громкость: панель показывает, что её слышно. Звать
        # необязательно, а без него запись работает как прежде.
        self.on_level: Callable[[float], None] | None = None

    def _callback(self, indata: np.ndarray, _frames: int, _time, status) -> None:
        if status:
            pass
        chunk = indata[:, 0].copy()
        with self._data_lock:
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

    def _warm_up(self) -> None:
        """Открыть входы заранее, чтобы нажатие не тратило время на открытие.

        Нажатие — единственное время, которое не отменить. Если способ
        открытия на этом компьютере не работал ни разу, одна его попытка
        при записи не тратит ничего: она делается здесь, заранее.
        """
        wanted = self._device_choice
        if wanted == self._prepared_choice and self._warm:
            return
        self._close_warm()
        self._prepared_choice = wanted
        for index in device_variants(wanted)[:4]:
            try:
                stream = _pick_stream(index, self._callback)
                stream.start()
            except Exception:  # noqa: BLE001 — нерабочий способ просто пропускаем
                BROKEN_DEVICES.add(_device_key(index))
                continue
            BROKEN_DEVICES.discard(_device_key(index))
            self._warm.append((index, stream))

    def _close_warm(self) -> None:
        warm, self._warm = self._warm, []
        for _index, stream in warm:
            try:
                stream.close()
            except Exception:
                pass

    def _start_recording_lock(self) -> None:
        """Включить запись. Вызывается уже под замком."""
        self._chunks = []
        if self._preroll and self._ring:
            preroll = np.concatenate(list(self._ring))
            if len(preroll) > PREROLL_SAMPLES:
                preroll = preroll[-PREROLL_SAMPLES:]
            self._chunks.append(preroll)
        self._recording = True
        self._warm_up()
        # Готовые входы — первыми. Микрофон могли выдернуть из USB или
        # переключить устройство по умолчанию: тогда из тех же способов
        # хватает живого, а если не хватает — закрываем и открываем заново.
        for index, stream in list(self._warm):
            if stream.active or self._reopen(stream):
                self._opened_index = index
                return
            BROKEN_DEVICES.add(_device_key(index))
            self._warm.remove((index, stream))
            try:
                stream.close()
            except Exception:
                pass
        # Ни один из заранее открытых способов не слышит: пробуем всё
        # остальное, от лучшего к худшему.
        for index in device_variants(self._device_choice):
            try:
                stream = _pick_stream(index, self._callback)
                stream.start()
            except Exception:  # noqa: BLE001 — пробуем следующий
                BROKEN_DEVICES.add(_device_key(index))
                continue
            BROKEN_DEVICES.discard(_device_key(index))
            self._warm.append((index, stream))
            self._opened_index = index
            return
        raise sd.PortAudioError("микрофон не отвечает: ни один вход не открылся")

    @staticmethod
    def _reopen(stream: sd.InputStream) -> bool:
        """Оживить уже открытый вход: Windows могла его приостановить."""
        try:
            if not stream.active:
                stream.start()
            return bool(stream.active)
        except Exception:  # noqa: BLE001
            return False

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
            try:
                self._start_recording_lock()
            except Exception:
                # Поток не пошёл — микрофон пропал, устройство занято, звук
                # сменился. Флаг записи снимаем и готовые входы выбрасываем:
                # вход, который не запустился, уже не годится, и держать его
                # значит обречь на ту же ошибку каждое следующее нажатие.
                # Без этого `is_recording()` навсегда отдаёт True, приложение
                # считает, что уже пишет, и молча ничего не делает — это и был
                # отказ «включается, слушает, а потом перестаёт работать».
                self._recording = False
                self._chunks = []
                self._ring.clear()
                self._close_warm()
                raise
        return True

    def _discard_stream(self) -> None:
        """Закрыть входы целиком: они больше не годятся.

        Вход, привязанный к пропавшему устройству, не оживает сам и держит
        микрофон занятым. Если оставить его, каждое следующее нажатие
        упирается в ту же ошибку, и запись перестаёт начинаться насовсем.
        """
        self._close_warm()
        self._opened_index = None

    def is_recording(self) -> bool:
        with self._lock:
            return self._recording

    def set_device(self, device: object) -> None:
        """Сменить микрофон без перезапуска приложения.

        Готовые входы привязаны к старому устройству, поэтому они
        закрываются, а выбранное прозвучит в микшере Windows при следующем
        нажатии: коротко, пока её никто не записывает. Запись, если она
        идёт, этим не трогается — человек услышит, что микрофон теперь
        другой, со следующей.
        """
        with self._lock:
            self._device_choice = device
            self._discard_stream()

    def input_device_name(self) -> str:
        """Имя микрофона, с которого идёт запись.

        Нужно ради журнала: когда микрофон отключают от USB, Windows молча
        берёт другое устройство по умолчанию — обычно веб-камеру, которая
        пишет тишину. Без имени устройства такая подмена не видна, и поломка
        выглядит как «диктовка перестала понимать». Спрашивается выбор, а не
        номер потока: имя остаётся тем же, через какой вкус Windows ни шла
        бы запись.
        """
        if self._opened_index is None and not self._warm:
            return ""
        return input_device_name(self._device_choice)

    def stop(self) -> np.ndarray | None:
        """Остановить запись и отдать звук. None — запись короче порога."""
        result = self.stop_with_noise()
        return None if result is None else result[0]

    def stop_with_noise(self) -> tuple[np.ndarray, np.ndarray | None] | None:
        """Остановить запись: звук и шум комнаты с того же микрофона.

        Шум нужен не только обрезке краёв. По нему видно, говорит ли
        человек тихо: голос поднимается над фоном резко, шум — нет. В самой
        записи голос поднимает фон и планку вместе с собой, поэтому фон
        берётся из тишины, записанной до микрофона.
        """
        with self._data_lock:
            self._recording = False
            # Готовые входы продолжают тихо слушать в ожидании следующего
            # нажатия: их начало уже на месте, и открывать вход заново под
            # нажатием не придётся.
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
        return trim_silence(recorded, noise), noise

    def close(self) -> None:
        with self._data_lock:
            self._recording = False
            self._chunks = []
            self._ring.clear()
        with self._lock:
            self._discard_stream()
