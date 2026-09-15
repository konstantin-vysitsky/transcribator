from __future__ import annotations

import threading

import numpy as np
import sounddevice as sd

# ~3× тише системного Beep, еле слышно
BEEP_VOLUME = 0.07
BEEP_MS = 45
SAMPLE_RATE = 44100


def _play_tone(freq: float, volume: float = BEEP_VOLUME, duration_ms: int = BEEP_MS) -> None:
    duration = duration_ms / 1000.0
    samples = int(SAMPLE_RATE * duration)
    if samples < 2:
        return
    t = np.linspace(0, duration, samples, endpoint=False)
    wave = np.sin(2 * np.pi * freq * t, dtype=np.float64) * volume
    fade = min(int(SAMPLE_RATE * 0.008), samples // 4)
    if fade > 1:
        wave[:fade] *= np.linspace(0, 1, fade)
        wave[-fade:] *= np.linspace(1, 0, fade)
    sd.play(wave.astype(np.float32), SAMPLE_RATE, blocking=False)


def quiet_beep_start() -> None:
    threading.Thread(target=_play_tone, args=(880,), daemon=True).start()


def quiet_beep_stop() -> None:
    threading.Thread(target=_play_tone, args=(660,), daemon=True).start()
