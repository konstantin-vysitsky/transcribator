from __future__ import annotations

import threading
from collections.abc import Callable

import numpy as np

from dictation.chunking import join_texts, split
from dictation.cleanup import load_fillers, remove_fillers
from dictation.recorder import SAMPLE_RATE
from transcribe import load_model, transcribe_gigaam


class Transcriber:
    def __init__(
        self,
        model: str = "v3_e2e_rnnt",
        cpu_threads: int = 0,
        remove_fillers_enabled: bool = True,
    ) -> None:
        self._model_name = model
        self._remove_fillers = remove_fillers_enabled
        self._fillers = load_fillers() if remove_fillers_enabled else []
        self._lock = threading.Lock()
        self._model, self._device, self._compute_type = load_model(model, cpu_threads)

    @property
    def device(self) -> str:
        return self._device

    def runtime_label(self) -> str:
        return f"gigaam/{self._model_name}, {self._device}"

    def _postprocess(self, text: str) -> str:
        text = text.strip()
        if self._remove_fillers and text:
            text = remove_fillers(text, self._fillers)
        return text

    def transcribe(
        self,
        audio: np.ndarray,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> str:
        """Распознать запись.

        Длинная запись режется на куски: поданная целиком, она не даёт текста
        вовсе — модель обучена на коротких фразах, и обработка на этом встаёт.
        `on_progress` получает (готово кусков, всего кусков): на длинной
        диктовке это единственный признак, что работа идёт, а не зависла.
        """
        with self._lock:
            pieces = split(audio, SAMPLE_RATE)
            if not pieces:
                return ""
            if len(pieces) == 1:
                return self._postprocess(transcribe_gigaam(self._model, pieces[0]))

            parts: list[str] = []
            total = len(pieces)
            for index, piece in enumerate(pieces, start=1):
                parts.append(transcribe_gigaam(self._model, piece))
                if on_progress is not None:
                    try:
                        on_progress(index, total)
                    except Exception:
                        pass
            return self._postprocess(join_texts(parts))
