from __future__ import annotations

import threading

import numpy as np

from dictation.cleanup import load_fillers, remove_fillers
from transcribe import is_gigaam_model, load_model, transcribe_gigaam


class Transcriber:
    def __init__(
        self,
        model: str = "v3_e2e_rnnt",
        cpu_threads: int = 0,
        language: str = "ru",
        beam_size: int = 5,
        vad_filter: bool = True,
        remove_fillers_enabled: bool = True,
    ) -> None:
        self._model_name = model
        self._language = language
        self._beam_size = beam_size
        self._vad_filter = vad_filter
        self._remove_fillers = remove_fillers_enabled
        self._fillers = load_fillers() if remove_fillers_enabled else []
        self._lock = threading.Lock()
        self._backend = "gigaam" if is_gigaam_model(model) else "whisper"
        self._model, self._device, self._compute_type = load_model(model, cpu_threads)

    @property
    def device(self) -> str:
        return self._device

    def runtime_label(self) -> str:
        if self._backend == "gigaam":
            return f"gigaam/{self._model_name}, {self._device}"
        return f"{self._device}, {self._compute_type}"

    def _postprocess(self, text: str) -> str:
        text = text.strip()
        if self._remove_fillers and text:
            text = remove_fillers(text, self._fillers)
        return text

    def transcribe(self, audio: np.ndarray) -> str:
        with self._lock:
            if self._backend == "gigaam":
                text = transcribe_gigaam(self._model, audio)
            else:
                segments, _ = self._model.transcribe(
                    audio,
                    language=self._language,
                    beam_size=self._beam_size,
                    vad_filter=self._vad_filter,
                )
                text = "".join(seg.text for seg in segments)
            return self._postprocess(text)
