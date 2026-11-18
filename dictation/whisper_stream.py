"""Потоковый Whisper: слова в поле по ходу речи, со знаками препинания.

Whisper не умеет считать поток: он всегда смотрит на кусок звука целиком.
Поэтому кусок нарезается сам — по паузам в речи. Пока человек говорит,
накопленная фраза пересчитывается каждые `_CHUNK_SECONDS`, и в поле уходит
только то, что совпало в двух чтениях подряд (правило локального согласия
из whisper_streaming, лицензия MIT). На паузе фраза досчитывается один раз
целиком — это и есть готовый конец фразы.

Знаки препинания, большие буквы и точки на конце Whisper ставит сам,
поэтому здесь они не придумываются правилами.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

import numpy as np

from dictation.live_field import FieldTyping, clip as _clip
from dictation.recorder import SAMPLE_RATE

# Тишина, после которой фраза считается сказанной. По самому звуку, а не
# по часам: задержка счёта — не пауза.
_QUIET_LEVEL = 0.006
_PAUSE_SECONDS = 0.5

# Как часто пересчитывать накопленную фразу. Реже — меньше нагрузка и
# больше задержка; чаще — наоборот.
_CHUNK_SECONDS = 1.0

# Сколько знаков прошлого текста отдавать модели как подсказку. Подсказка
# держит знаки и стиль, но длинный хвост замедляет счёт.
_PROMPT_CHARS = 200

# Дольше этого фразу не копим: пересчёт длинного куска не успевает за
# речью, и диктовка начинает отставать.
_MAX_UTTERANCE_SECONDS = 60.0

KNOWN_MODELS = ("whisper-small", "whisper-medium", "whisper-large-v3-turbo")

_MODEL_NAMES = {
    "whisper-small": "small",
    "whisper-medium": "medium",
    "whisper-large-v3-turbo": "large-v3-turbo",
}


def load_model(recognition: str, cpu_threads: int = 8):
    """Загрузить модель Whisper один раз на всё приложение."""
    from faster_whisper import WhisperModel

    name = _MODEL_NAMES.get(recognition)
    if name is None:
        raise ValueError(f"неизвестная модель Whisper: {recognition}")
    return WhisperModel(
        name,
        device="cpu",
        compute_type="int8",
        cpu_threads=max(1, cpu_threads),
    )


class WhisperStreaming(FieldTyping):
    """Потоковая диктовка на Whisper для одной диктовки.

    Публичная часть та же, что у `StreamingTranscriber`: `feed`, `finish`,
    `cancel`, `set_typing`, `final_append`, `runtime_label`. Поэтому
    приложение не знает, какая модель под ним работает.
    """

    def __init__(
        self,
        model: str = "whisper-small",
        cpu_threads: int = 8,
        on_text: Callable[[str], None] | None = None,
        model_ref: object | None = None,
        log: Callable[[str], None] | None = None,
        language: str = "ru",
    ) -> None:
        self.model_name = model
        self._language = language
        self._log = log
        self.started_at = time.monotonic()
        self._model = model_ref if model_ref is not None else load_model(model, cpu_threads)
        self._lock = threading.Lock()
        self._stop_worker = threading.Event()
        self._fresh = threading.Event()
        self._worker = threading.Thread(
            target=self._work, daemon=True, name="whisper-stream"
        )
        self._on_text = on_text
        # Текст текущей фразы и всего, что уже сказано до неё.
        self._partial = ""
        self._settled_text = ""
        self._done = False
        # Накопленная фраза: от паузы до паузы.
        self._utterance = np.zeros(0, dtype=np.float32)
        self._paused = False
        # Прошлое чтение той же фразы: по нему видно, что подтвердилось.
        self._prev_words: list[str] = []
        self._prev_norms: list[str] = []
        self._committed = 0
        # Сколько слов уже отдано в поле за всю диктовку.
        self._written = 0
        self._last_decode = 0.0
        self._quiet_seconds = 0.0
        self._audio_seconds = 0.0
        self._audio_logged = False
        self._decode_seconds = 0.0
        self._decodes = 0
        self._init_field_typing()

    def start(self) -> None:
        self._worker.start()

    def runtime_label(self) -> str:
        return f"stream/{self.model_name}, cpu"

    def feed(self, chunk: np.ndarray) -> None:
        """Принять прозвучавший кусок. Считает другой поток."""
        if not len(chunk) or self._done:
            return
        chunk = chunk.astype(np.float32)
        self._audio_seconds += len(chunk) / SAMPLE_RATE
        if not self._audio_logged:
            self._audio_logged = True
            self._log_safe(
                f"поток: первый звук в модели, {len(chunk) / SAMPLE_RATE:.2f} с кусок"
            )
        level = float(np.sqrt(np.mean(np.square(chunk, dtype=np.float64))))
        with self._lock:
            self._utterance = np.concatenate([self._utterance, chunk])
            if level < _QUIET_LEVEL:
                self._quiet_seconds += len(chunk) / SAMPLE_RATE
            else:
                self._quiet_seconds = 0.0
            self._paused = self._quiet_seconds >= _PAUSE_SECONDS
        self._fresh.set()

    def _decode(self, audio: np.ndarray) -> str:
        """Прочитать кусок и вернуть текст со знаками."""
        if not len(audio):
            return ""
        started = time.monotonic()
        prompt = self._settled_text[-_PROMPT_CHARS:] or None
        segments, _info = self._model.transcribe(
            audio,
            language=self._language,
            beam_size=1,
            condition_on_previous_text=False,
            vad_filter=False,
            without_timestamps=True,
            initial_prompt=prompt,
        )
        text = " ".join(segment.text.strip() for segment in segments).strip()
        self._decode_seconds += time.monotonic() - started
        self._decodes += 1
        return text

    def _work(self) -> None:
        """Считать фразу и дописывать в поле то, что уже подтвердилось."""
        while not self._stop_worker.is_set():
            if not self._fresh.wait(timeout=0.02):
                continue
            self._fresh.clear()
            with self._lock:
                paused = self._paused
                audio = self._utterance.copy()
            now = time.monotonic()
            if paused:
                self._close_utterance(audio)
                continue
            if now - self._last_decode < _CHUNK_SECONDS:
                continue
            if len(audio) < SAMPLE_RATE // 2:
                # Меньше полусекунды: читать нечего, модель придумает слово.
                continue
            if len(audio) > _MAX_UTTERANCE_SECONDS * SAMPLE_RATE:
                # Долгая речь без пауз: закрываем фразу, иначе пересчёт
                # длинного куска не успеет за человеком.
                self._close_utterance(audio)
                continue
            self._last_decode = now
            text = self._decode(audio)
            if not text:
                continue
            self._partial = text
            self._log_safe(
                f"текст модели: {now - self.started_at:.1f} с от F7, "
                f"звука {self._audio_seconds:.1f} с, «{_clip(text)}»"
            )
            if self._on_text is not None:
                try:
                    self._on_text(text)
                except Exception:  # noqa: BLE001 — приписка важнее отчёта
                    self._on_text = None
            words = text.split()
            # Сверка без знаков и регистра: между чтениями Whisper меняет
            # запятые и большую букву, и точное сравнение не двигало
            # подтверждение, пока фраза не кончится целиком.
            norms = self._normal_words(text)
            common = 0
            for a, b in zip(self._prev_norms, norms):
                if a != b:
                    break
                common += 1
            self._prev_words = words
            self._prev_norms = norms
            # Последнее слово ещё может поменяться — его придерживаем.
            self._emit(words, max(0, min(common, len(words) - 1)))

    def _emit(self, words: list[str], upto: int) -> None:
        """Отдать в поле подтверждённое начало фразы."""
        if upto <= self._committed:
            return
        chunk = words[self._committed : upto]
        self._append_words(chunk)
        self._committed = upto
        self._written += len(chunk)

    def _close_utterance(self, audio: np.ndarray) -> None:
        """Пауза: досчитать фразу целиком и отдать её хвост."""
        if len(audio) < SAMPLE_RATE // 10:
            self._reset_utterance()
            return
        if float(np.abs(audio).max()) < _QUIET_LEVEL:
            # В куске одна тишина: модель на ней придумывает слова.
            self._reset_utterance()
            return
        text = self._decode(audio)
        if text:
            self._partial = text
            self._emit(text.split(), len(text.split()))
            self._settled_text = f"{self._settled_text} {text}".strip()
        self._reset_utterance()

    def _reset_utterance(self) -> None:
        with self._lock:
            self._utterance = np.zeros(0, dtype=np.float32)
            self._quiet_seconds = 0.0
            self._paused = False
        self._partial = ""
        self._prev_words = []
        self._committed = 0
        self._last_decode = time.monotonic()

    def finish(self) -> str:
        """Остановить поток и отдать окончательный текст всей диктовки."""
        if self._done:
            return self.full_text()
        self._done = True
        self._stop_worker.set()
        self._worker.join(timeout=3.0)
        with self._lock:
            audio = self._utterance.copy()
        if len(audio) >= SAMPLE_RATE // 10 and float(np.abs(audio).max()) >= _QUIET_LEVEL:
            text = self._decode(audio)
            if text:
                self._partial = text
                self._emit(text.split(), len(text.split()))
                self._settled_text = f"{self._settled_text} {text}".strip()
        self._log_safe(
            f"поток: запись закрыта, в модели {self._audio_seconds:.1f} с звука, "
            f"счёт {self._decode_seconds:.1f} с за {self._decodes} чтений"
        )
        return self.full_text()

    def full_text(self) -> str:
        """Всё сказанное за диктовку одной строкой."""
        return " ".join(f"{self._settled_text} {self._partial}".split())

    def final_append(self) -> str | None:
        """Остаток диктовки в поле: слова, которых там ещё нет.

        Считается по всему сказанному за диктовку, а не по последней фразе:
        фраз было несколько, и каждая дописывалась в поле по ходу.
        """
        if not self._typing:
            return None
        words = self.full_text().split()
        new_words = words[self._written :]
        if not new_words:
            return None
        self._append_words(new_words)
        self._written = len(words)
        return " ".join(new_words)

    def cancel(self) -> None:
        self._done = True
        self._stop_worker.set()
        self._worker.join(timeout=1.0)
