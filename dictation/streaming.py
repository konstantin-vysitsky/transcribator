"""Потоковое распознавание: текст по ходу речи, а не после остановки.

Обычная диктовка пишет весь звук в память и распознаёт его, когда отпущена
клавиша. Здесь звук идёт в модель кусками, едва прозвучав: то, что человек
уже сказал, попадает в окно почти сразу. Куски отдаёт тот же микрофонный
поток, что и обычную запись (короткозвучащая функция `Recorder.add_sink`),
поэтому вход, частота и провалы в ноль уже разобраны записывающей частью.

Модели — две потоковые для русского, обе из sherpa-onnx:
 - t-one: русская потоковая модель T-Bank, около 0,12 с счёта на секунду
   речи на одном ядре-ядре с запасом;
 - zipformer: маленькая на данных Vosk, около 0,05 с на секунду, но слабее.
Ни одна из них не понимает знаков и заглавных букв — их ставит `format_text`,
как и для GigaAM.

Вставать в начале и в середине: модель может поменять слово на ходу, поэтому
в окне есть отдельный блок, который заменяется целиком.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path

import numpy as np

from dictation.chunking import format_text
from dictation.live_field import FieldTyping, clip as _clip
from dictation.cleanup import load_fillers, remove_fillers
from dictation.recorder import SAMPLE_RATE

# Сколько звука примерно приходится на одно слово. Нужно нарезке по
# паузам: по нему считается, какой хвост модель ещё может переписать.
_SECONDS_PER_WORD = 0.4

# Тишина, после которой последнее слово отдаётся в поле. Меньше — слово
# уходило бы недописанным, больше — конец фразы висел бы заметно долго.
# Уровень считается по самому звуку: 0.006 — заметно тише обычной речи.
_QUIET_LEVEL = 0.006
_PAUSE_SECONDS = 0.5

# Знаки, которые модель приставляет к слову. При сверке снимаются: иначе
# запятая посреди диктовки выглядела как другое слово и поле замирало.
_EDGE_PUNCT = ".,!?;:—–-«»\"'()…"

# Модели на диске, рядом с проектом. Ставятся из готовых сборок sherpa-onnx:
# https://github.com/k2-fsa/sherpa-onnx/releases/tag/asr-models
MODELS_DIR = Path(__file__).resolve().parent.parent / "models"

KNOWN_MODELS = ("t-one", "zipformer-small", "zipformer-small-int8")


def _clip(text: str, limit: int = 120) -> str:
    """Текст для журнала: одна строка и не длиннее `limit` знаков."""
    collapsed = " ".join(str(text).split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1] + "…"


def model_available(name: str) -> bool:
    """Стоит ли модель на диске. Нет — подсказываем, где её взять."""
    return (MODELS_DIR / name).is_dir()


def _recognizer(name: str, threads: int):
    import sherpa_onnx
    from sherpa_onnx.lib._sherpa_onnx import (
        OnlineModelConfig,
        OnlineRecognizerConfig,
        OnlineTransducerModelConfig,
    )
    from sherpa_onnx.lib._sherpa_onnx import OnlineRecognizer as _Native

    folder = MODELS_DIR / name
    if not folder.is_dir():
        raise RuntimeError(
            f"нет модели потокового распознавания «{name}» в {MODELS_DIR}"
        )
    if name == "t-one":
        # Первый аргумент — не частота звука микрофона, а частота мел-
        # признаков, с которой обучена модель (16 кГц, 80 признаков).
        return sherpa_onnx.OnlineRecognizer.from_t_one_ctc(
            tokens=str(folder / "tokens.txt"),
            model=str(folder / "model.onnx"),
            num_threads=threads,
            sample_rate=16000,
            feature_dim=80,
            decoding_method="greedy_search",
            provider="cpu",
        )

    def pick(stem: str) -> Path:
        # Облегчённая int8-сборка лежит рядом обычной: какая есть, ту и берём.
        for candidate in (folder / f"{stem}.int8.onnx", folder / f"{stem}.onnx"):
            if candidate.exists():
                return candidate
        raise RuntimeError(f"в модели «{name}» нет файла {stem}.onnx")

    config = OnlineRecognizerConfig(
        feat_config=sherpa_onnx.FeatureExtractorConfig(
            sampling_rate=16000, feature_dim=80
        ),
        model_config=OnlineModelConfig(
            transducer=OnlineTransducerModelConfig(
                encoder=str(pick("encoder")),
                decoder=str(pick("decoder")),
                joiner=str(pick("joiner")),
            ),
            tokens=str(folder / "tokens.txt"),
            num_threads=threads,
            model_type="zipformer2",
        ),
        enable_endpoint=False,
        decoding_method="greedy_search",
    )
    recognizer = sherpa_onnx.OnlineRecognizer()
    recognizer.recognizer = _Native(config)
    recognizer.config = config
    return recognizer


class StreamingTranscriber(FieldTyping):
    """Потоковое распознавание одной диктовки.

    Работает так: `feed` кладёт прозвучавший кусок, `finish` забирает
    последний текст. Куски принимает звуковой поток, а считает модель
    отдельный поток — иначе звук ждал бы модель.

    Модель загружается при запуске приложения и живёт дальше: на каждую
    диктовку заводится свой счёт, поэтому в оценку времени входит только
    сама речь. Готовую модель передаём сюда как `recognizer`.
    """

    def __init__(
        self,
        model: str = "t-one",
        cpu_threads: int = 6,
        on_text: Callable[[str], None] | None = None,
        recognizer: object | None = None,
        model_ref: object | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.model_name = model
        self._lock = threading.Lock()
        # Куда писать строки измерений: приложение передаёт свой журнал.
        # Без него (проверки, пробы) строки просто не пишутся.
        self._log = log
        # Отсчёт «секунд от начала записи»: от создания счёта, то есть от
        # нажатия F7. По часам, а не по длине звука: так видно отставание.
        self.started_at = time.monotonic()
        # Сколько звука уже отдано в модель. В журнале сравнивается с
        # часами: по этой паре видно, успевает ли модель за речью.
        self._audio_seconds = 0.0
        self._audio_logged = False
        # Модель, загруженная один раз на всё приложение (`model_ref`), или
        # своя, если передали готовый `recognizer`. Грузить её заново на
        # каждой диктовке незачем: загрузка дороже самой речи.
        self._model = (
            model_ref
            if model_ref is not None
            else (recognizer if recognizer is not None else _recognizer(model, max(1, cpu_threads)))
        )
        self._stream = self._model.create_stream()
        self._stop_worker = threading.Event()
        self._worker = threading.Thread(
            target=self._work, daemon=True, name="stream-decode"
        )
        self._on_text = on_text
        self._partial = ""
        self._done = False
        # Есть ли новый звук, который модель ещё не считала.
        self._fresh = threading.Event()
        # Дописывание в поле: общая часть с Whisper и GigaAM.
        self._init_field_typing()
        # Когда модель в последний раз сказала что-то новое. По этому
        # времени видна пауза: на паузе последнее слово пора отдавать в
        # поле, иначе оно висит до следующей фразы.
        self._last_text_at = 0.0
        # Сколько подряд тихого звука пришло и была ли уже пауза, которую
        # пора отдать в поле.
        self._quiet_seconds = 0.0
        self._pause_ready = threading.Event()
        self._remove_fillers = True
        try:
            self._fillers = load_fillers()
        except Exception:  # noqa: BLE001 — без чистки поле всё равно нужно
            self._fillers = []

    def start(self) -> None:
        self._worker.start()

    def runtime_label(self) -> str:
        return f"stream/{self.model_name}, cpu"

    def feed(self, chunk: np.ndarray) -> None:
        """Принять прозвучавший кусок (16 кГц, дробные значения).

        Зовётся из звукового потока: только положить звук и сказать, что
        есть работа. Считает модель другой поток.
        """
        if not len(chunk) or self._done:
            return
        self._audio_seconds += len(chunk) / SAMPLE_RATE
        # Пауза в речи определяется по самому звуку, а не по часам: счёт
        # может отставать, и тогда «тишиной» выглядела бы его задержка.
        # На такой мнимой паузе в поле уходило недописанное «др» вместо
        # «другом» — и оставалось там навсегда, потому что не стирается.
        level = float(np.sqrt(np.mean(np.square(chunk, dtype=np.float64))))
        if level < _QUIET_LEVEL:
            self._quiet_seconds += len(chunk) / SAMPLE_RATE
            if self._quiet_seconds >= _PAUSE_SECONDS:
                self._pause_ready.set()
        else:
            self._quiet_seconds = 0.0
        if not self._audio_logged:
            # Первый звук: по этой строке видно, что микрофон действительно
            # доносит куски до модели, а не обрывается раньше.
            self._audio_logged = True
            self._log_safe(
                f"поток: первый звук в модели, {len(chunk) / SAMPLE_RATE:.2f} с кусок"
            )
        with self._lock:
            self._stream.accept_waveform(SAMPLE_RATE, chunk.astype(np.float32))
        self._fresh.set()

    def _final_piece(self, text: str) -> str:
        """Конец диктовки: знаки и большая буква, как у обычной диктовки."""
        return format_text(text)

    def _typing_piece(self, text: str) -> str:
        """Что уже готово в поле: собранный текст без точки на конце."""
        if self._remove_fillers:
            text = remove_fillers(text, self._fillers)
        return format_text(text, final=False)

    def _work(self) -> None:
        """Считать пришедший звук и дописывать в поле готовые слова.

        Поле не перерисовывается: в нём только дописывается то, что модель
        уже закончила. Иначе на каждую букву приходилась бы вставка с
        затиранием — это и выглядело как «всё стирает и пишет заново».
        """
        while not self._stop_worker.is_set():
            # Ждём нового звука. Таймаут 20 мс: за это время с микрофона
            # приходит два куска, и модель не дёргается на каждый.
            if not self._fresh.wait(timeout=0.02):
                # Звука нет — значит пауза. На паузе последнее слово пора
                # отдавать: иначе оно висело в модели до следующей фразы,
                # и конец сказанного выглядел обрубленным.
                self._flush_idle()
                continue
            self._fresh.clear()
            with self._lock:
                stream = self._stream
                while self._model.is_ready(stream):
                    self._model.decode_stream(stream)
                text = self._model.get_result(stream).strip()
            if text and text != self._partial:
                self._partial = text
                self._last_text_at = time.monotonic()
                # Каждое новое чтение модели: секунды от нажатия F7, сколько
                # звука уже отдано и сам текст. Коротко, первые 120 знаков.
                self._log_safe(
                    (
                        f"текст модели: {time.monotonic() - self.started_at:.1f} с "
                        f"от F7, звука {self._audio_seconds:.1f} с, "
                        f"«{_clip(text)}»"
                    )
                )
                if self._on_text is not None:
                    try:
                        self._on_text(text)
                    except Exception as exc:  # noqa: BLE001 — модель важнее приписки
                        # Раньше сбой здесь молча выключал приписку, и в
                        # журнале не оставалось ни строки о причине.
                        self._log_safe(
                            f"сбой обработчика текста модели: {type(exc).__name__}: {exc}"
                        )
                        self._on_text = None
                if self._typing and self._on_append is not None:
                    self._print_ready(text)

    def _flush_idle(self) -> None:
        """Пауза в речи: отдать последнее слово, которое модель придерживала.

        Слово держится, пока модель не начала следующее: новое слово может
        поменяться. Но если человек замолчал, ждать нечего — иначе конец
        фразы появлялся только со следующей фразой.
        """
        if not self._typing or self._on_append is None or not self._partial:
            return
        # Отдаём слово только тогда, когда микрофон сам услышал тишину:
        # заминка счёта за паузу не считается.
        if not self._pause_ready.is_set():
            return
        # Перед отдачей модель должна дочитать всё, что уже прозвучало.
        # Иначе отдавался огрызок: на паузе модель ещё отставала, и в поле
        # уходило «др» вместо «другом» — навсегда, потому что написанное
        # не стирается.
        with self._lock:
            stream = self._stream
            while self._model.is_ready(stream):
                self._model.decode_stream(stream)
            text = self._model.get_result(stream).strip()
        if text and text != self._partial:
            self._partial = text
            self._last_text_at = time.monotonic()
            self._print_ready(text)
            return
        self._pause_ready.clear()
        self._print_ready(self._partial, release_last=True)

    def finish(self) -> str:
        """Остановить поток и отдать окончательный текст.

        Тишина в конце досчитывается: последние слова ещё не успели пройти
        модель, когда человек отпустил клавишу.
        """
        if self._done:
            return self._partial
        self._done = True
        self._stop_worker.set()
        self._worker.join(timeout=2.0)
        # Конец счёта: сколько звука модель прожевала за диктовку. Пара к
        # «запись сохранена (N с)» показывает, не потерялся ли хвост.
        self._log_safe(
            f"поток: запись закрыта, в модели {self._audio_seconds:.1f} с звука"
        )
        with self._lock:
            stream = self._stream
            stream.accept_waveform(
                SAMPLE_RATE, np.zeros(int(0.4 * SAMPLE_RATE), np.float32)
            )
            # Пока модель готова считать — считаем; она сама знает, когда
            # хвоста хватит (is_ready отсекает недобранные куски).
            while self._model.is_ready(stream):
                self._model.decode_stream(stream)
            self._partial = self._model.get_result(stream).strip()
            return self._partial

    def cancel(self) -> None:
        self._done = True
        self._stop_worker.set()
        self._worker.join(timeout=1.0)


def merged_text(raw: str) -> str:
    """Собрать сказанное в нормальный текст: знаки, заглавные.

    Здесь только сборка строки. Лишнее («э», «как бы») убирает обычная
    чистка диктовки в `main`, одна и та же для обеих моделей.
    """
    if not raw.strip():
        return ""
    return format_text(raw)
