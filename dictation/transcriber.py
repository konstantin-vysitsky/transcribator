from __future__ import annotations

import threading

import numpy as np

from dictation.cleanup import load_fillers, remove_fillers
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

    def transcribe(self, audio: np.ndarray) -> str:
        with self._lock:
            text = transcribe_gigaam(self._model, audio)
            return self._postprocess(text)
