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
        # Дописывание в поле по ходу речи: только в то окно, где стоял
        # курсор. На панели управления текст не показывается.
        self._typing = False
        self._on_append: Callable[[str], None] | None = None
        # Что уже отдано в поле: слова в нижнем виде и без знаков — так
        # сверка не зависит от запятых и заглавных, которые модель ставит
        # `_field_line` — ровно то, что в поле написано.
        self._printed_normal = ""
        self._printed_count = 0
        self._field_line = ""
        # Сколько раз модель поменяла уже записанное: по этому числу в
        # журнале видно, много ли правок и не пора ли их убирать.
        self.revisions = 0
        self._remove_fillers = True
        try:
            self._fillers = load_fillers()
        except Exception:  # noqa: BLE001 — без чистки поле всё равно нужно
            self._fillers = []

    def start(self) -> None:
        self._worker.start()

    def _log_safe(self, message: str) -> None:
        """Записать строку измерений, не давая журналу уронить диктовку."""
        if self._log is None:
            return
        try:
            self._log(message)
        except Exception:  # noqa: BLE001 — журнал не важнее звука
            pass

    def runtime_label(self) -> str:
        return f"stream/{self.model_name}, cpu"

    def set_typing(self, enabled: bool, on_append: Callable[[str], None] | None = None) -> None:
        """Дописывать в поле по ходу речи. `on_append` получает только новое.

        В поле ничего не стирается и не пишется заново: каждое слово, которое
        модель закончила, дописывается один раз. Незаконченное слово
        придерживается — оно ещё может поменяться.
        """
        self._typing = enabled
        self._on_append = on_append

    def feed(self, chunk: np.ndarray) -> None:
        """Принять прозвучавший кусок (16 кГц, дробные значения).

        Зовётся из звукового потока: только положить звук и сказать, что
        есть работа. Считает модель другой поток.
        """
        if not len(chunk) or self._done:
            return
        self._audio_seconds += len(chunk) / SAMPLE_RATE
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

    def _typing_piece(self, text: str) -> str:
        """Что уже готово в поле: собранный текст без точки на конце."""
        if self._remove_fillers:
            text = remove_fillers(text, self._fillers)
        return format_text(text, final=False)

    def _work(self) -> None:
        """Считать пришедший звук и дописывать в поле законченные слова.

        Поле не перерисовывается: в нём только дописывается то, что модель
        уже закончила. Иначе на каждую букву приходилась бы вставка с
        затиранием — это и выглядело как «всё стирает и пишет заново».
        """
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
                previous = self._partial
                self._partial = text
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
                    self._print_ready(text, previous)

    @staticmethod
    def _normal_words(text: str) -> list[str]:
        """Слова для сверки: нижним регистром, без знаков вокруг слова.

        Знаки модель ставит и снимает по ходу («а потом запятая»), и сверка
        по тексту с ними останавливала поле. Буква «ё» считается за «е»:
        модель пишет то так, то так.
        """
        words = []
        for word in text.split():
            bare = word.strip(_EDGE_PUNCT).lower().replace("ё", "е")
            if bare:
                words.append(bare)
        return words

    @staticmethod
    def _place(line: list[str], field: list[str]) -> int | None:
        """Где в чтении начинается то, чего в поле нет.

        Сверяется хвост того, что стоит в поле: сдвиг в пару слов туда-сюда
        допускается. None — сверить не удалось, ждём следующего чтения.
        Врать наугад нельзя: ошибка здесь приписывает в поле кусок заново.
        """
        if not field:
            return 0
        tail = min(3, len(field))
        for shift in (0, -1, 1, -2, 2):
            spot = len(field) + shift
            if 0 <= spot <= len(line) and line[spot - tail:spot] == field[-tail:]:
                return spot
        return None

    def _print_ready(self, text: str, previous: str = "") -> None:
        """Дописать в поле слова, которых в нём ещё нет.

        В поле только дописывается: стирание и повтор съедали всю скорость.
        Готовым считается слово, после которого модель уже начала следующее.
        `_field_line` — строка ровно того, что стоит в поле: по ней видно,
        где дописывать, даже если модель по ходу поправила прошлое.
        """
        piece = self._typing_piece(text)
        model_words = self._normal_words(piece)
        complete = len(model_words)
        if piece and not piece.endswith(" "):
            complete -= 1
        line = model_words[:complete]
        field = self._field_line.split()
        start = self._place(line, field)
        if start is None:
            return
        if line[:start] != field[:start]:
            self.revisions += 1
        chunk = " ".join(line[start:])
        if not chunk:
            return
        try:
            self._on_append(chunk + " ")
        except Exception as exc:  # noqa: BLE001 — поле важнее, но причину в журнал
            self._log_safe(f"сбой дописывания в поле: {type(exc).__name__}: {exc}")
            self._on_append = None
            return
        self._field_line = " ".join(line[:start] + chunk.split())

    def final_append(self) -> str | None:
        """Приписка в конец диктовки: остаток текста.

        Вызывается после остановки, когда модель дочитала хвост: последнее
        слово и точка. Дописывается только то, чего в поле ещё нет —
        старое не стирается и не повторяется.
        """
        if not self._typing or not self._partial.strip():
            return None
        piece = format_text(self._partial)
        words = piece.split()
        model_words = self._normal_words(piece)
        field_words = self._field_line.split()
        start = self._place(model_words, field_words)
        if start is None:
            start = len(field_words)
        new_words = words[min(start, len(words)):]
        self._printed_normal = " ".join(model_words)
        self._printed_count = len(model_words)
        self._field_line = " ".join(model_words)
        if not new_words:
            return None
        return " ".join(new_words)

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
