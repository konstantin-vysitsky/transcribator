"""Приложение с микрофоном из файла — для проверки tests/check_app_live.py.

Запускается отдельным процессом: настоящий `dictation.main`, только
вместо звуковой карты отдаётся wav. Так проверяется весь путь целиком,
включая то, что приложение дописывает слова, пока человек говорит.
"""

from __future__ import annotations

import sys
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

wav_path = Path(sys.argv[1])
with wave.open(str(wav_path), "rb") as wf:
    audio = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).astype(
        np.float32
    ) / 32768.0

sys.argv = ["dictation"]
import dictation.main as app  # noqa: E402

from check_app_live import FakeRecorder  # noqa: E402

app.Recorder = lambda *_args, **_kwargs: FakeRecorder(audio)
app.main()
