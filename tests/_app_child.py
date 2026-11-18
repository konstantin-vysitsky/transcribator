"""Приложение с микрофоном из файла — для проверки tests/check_app_live.py.

Запускается отдельным процессом: настоящий `dictation.main`, только
вместо звуковой карты отдаётся wav. Так проверяется весь путь целиком,
включая то, что приложение дописывает слова, пока человек говорит.

С `--no-paste` вставка подменяется: проверяется, что она вызывается, но
в настоящие окна ничего не печатается. Так проверку можно гонять, пока
человек работает за компьютером.
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
no_paste = "--no-paste" in sys.argv
with wave.open(str(wav_path), "rb") as wf:
    audio = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).astype(
        np.float32
    ) / 32768.0

if no_paste:
    import dictation.paster as paster

    def fake_paste(text, **_kwargs):
        print(f"вставка (подмена): {text!r}", flush=True)
        return True, False

    paster.paste_text = fake_paste

sys.argv = ["dictation"]
import dictation.main as app  # noqa: E402

from check_app_live import FakeRecorder  # noqa: E402

app.Recorder = lambda *_args, **_kwargs: FakeRecorder(audio)
app.main()
