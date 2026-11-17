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
from dictation.cleanup import load_fillers, remove_fillers
from dictation.recorder import SAMPLE_RATE

# Сколько звука примерно приходится на одно слово. Нужно нарезке по
# паузам: по нему считается, какой хвост модель ещё может переписать.
_SECONDS_PER_WORD = 0.4

# Модели на диске, рядом с проектом. Ставятся из готовых сборок sherpa-onnx:
# https://github.com/k2-fsa/sherpa-onnx/releases/tag/asr-models
MODELS_DIR = Path(__file__).resolve().parent.parent / "models"

KNOWN_MODELS = ("t-one", "zipformer-small", "zipformer-small-int8")


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


class StreamingTranscriber:
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
    ) -> None:
        self.model_name = model
        self._lock = threading.Lock()
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
        # Вставка в поле по ходу речи. `_printed` — то, что уже стоит в
        # поле для текущего куска, `_handover` — сколько знаков закрыто
        # прошлыми кусками.
        self._typing = False
        self._on_replace: Callable[[str], None] | None = None
        self._printed = ""
        self._handover = 0
        self._remove_fillers = True
        try:
            self._fillers = load_fillers()
        except Exception:  # noqa: BLE001 — без чистки поле всё равно нужно
            self._fillers = []

    def start(self) -> None:
        self._worker.start()

    def runtime_label(self) -> str:
        return f"stream/{self.model_name}, cpu"

    def set_typing(self, enabled: bool, on_replace: Callable[[str], None] | None = None) -> None:
        """Вставлять текст в поле по ходу речи.

        Потоковая модель может переписать уже показанное слово, поэтому
        текст в поле не дописывается кусками, а заменяется целиком. Место
        замены знает тот, кто уже вставил прошлый текст: он стирает
        прошлую вставку и печатает новую.

        Новый текст отдаётся, только когда он вырос на `_MIN_GROWTH` знаков
        или закончилась точка. Иначе каждая буква — отдельная вставка, а
        на каждую вставку уходит закладка и отпускание клавиш: поле осталось
        бы без букв.
        """
        self._typing = enabled
        self._on_replace = on_replace

    def feed(self, chunk: np.ndarray) -> None:
        """Принять прозвучавший кусок (16 кГц, дробные значения).

        Зовётся из звукового потока: только положить звук и сказать, что
        есть работа. Считает модель другой поток.
        """
        if not len(chunk) or self._done:
            return
        with self._lock:
            self._stream.accept_waveform(SAMPLE_RATE, chunk.astype(np.float32))
        self._fresh.set()

    def _typing_piece(self, text: str) -> str:
        """Что вставить в поле: собранный текст без точки на конце."""
        if self._remove_fillers:
            text = remove_fillers(text, self._fillers)
        return format_text(text, final=False)

    def _work(self) -> None:
        """Считать то, что пришло, и сообщать текст по мере роста."""
        while not self._stop_worker.is_set():
            # Ждём нового звука. Таймаут 20 мс: за это время с микрофона
            # приходит два куска, и модель не дёргается на каждый.
            if not self._fresh.wait(timeout=0.02):
                continue
            self._fresh.clear()
            with self._lock:
                stream = self._stream
                while self._model.is_ready(stream):
                    self._model.decode_stream(stream)
                text = self._model.get_result(stream).strip()
            if text and text != self._partial:
                self._partial = text
                if self._on_text is not None:
                    try:
                        self._on_text(text)
                    except Exception:
                        self._on_text = None
                if self._typing and self._on_replace is not None:
                    piece = self._typing_piece(text)
                    if self._should_print(piece):
                        try:
                            self._on_replace(piece)
                        except Exception:
                            self._on_replace = None

    def _should_print(self, piece: str) -> bool:
        """Пора ли править поле.

        Здоровая модель только дописывает, и тогда поле правится на каждый
        новый знак — получается печать по буквам. Если модель уже написанное
        исправила, не трогаем поле до конца предложения: иначе каждое
        исправление слова — две вставки на одно слово, и поле мигает.
        """
        if not piece or piece == self._printed:
            return False
        if not piece.startswith(self._printed):
            return piece.endswith((".", "!", "?"))
        return True

    def emit_pending(self) -> str | None:
        """Текст, который модель уже поняла, а в поле ещё не ушёл."""
        if not self._typing:
            return None
        piece = self._typing_piece(self._partial)
        if not piece or piece == self._printed:
            return None
        self._printed = piece
        return piece

    def mark_handed_over(self) -> None:
        """Закрыть кусок: дальше в поле пойдёт только новый текст.

        Кусок закрывается на конце предложения. Раньше закрывать нельзя:
        модель ещё исправит слово внутри, и в поле осталась бы ошибка.
        """
        self._handover = len(self._partial)
        self._printed = ""

    def append_audio(self, audio: np.ndarray) -> None:
        """Досчитать ещё не принятый звук, не останавливая поток.

        Нужно для нарезки по паузам: где резать, ищется по громкости уже
        прозвучавшего, а модель этот звук ещё ждёт. Отдаём только ту часть,
        от которой модель не изменит окончание закрытого куска.
        """
        if self._on_replace is None or not len(audio):
            return
        piece = self._typing_piece(self._partial)
        if not piece:
            return
        words = piece.split()
        fallback = int(0.9 * max(1, len(words)) * _SECONDS_PER_WORD * SAMPLE_RATE)
        # Хвост меньше одного слова модель ещё может исправить: его оставляем
        # следующему куску, остальное уже окончательное.
        cut = len(audio) - fallback
        if cut <= 0:
            return
        try:
            self._on_replace(piece)
        except Exception:
            self._on_replace = None
            return
        with self._lock:
            self._stream = self._model.create_stream()
        self._handover = 0
        self._printed = ""
        self._partial = ""
        self.feed(audio[cut:])

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
