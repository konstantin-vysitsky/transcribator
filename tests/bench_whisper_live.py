"""Потоковый Whisper: живая проверка на записи, как будто человек говорит.

Запуск: `.venv\\Scripts\\python.exe tests\\bench_whisper_live.py medium 1.0`

Устройство: пока человек говорит, накопленная фраза (от паузы до паузы)
пересчитывается каждые N секунд. В поле уходит только то, что совпало в
двух чтениях подряд — правило локального согласия. На паузе фраза
досчитывается один раз целиком, и её хвост тоже уходит в поле.

Печатает: что и когда появилось, задержку и сколько времени занял счёт.
"""

from __future__ import annotations

import sys
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np

SAMPLE_RATE = 16000
STEP = SAMPLE_RATE // 10  # 0,1 с звука за шаг
QUIET_LEVEL = 0.006
PAUSE_SECONDS = 0.5


def pick_recording() -> Path:
    files = sorted((ROOT / "записи").glob("*.wav"), key=lambda p: p.stat().st_size, reverse=True)
    for path in files:
        seconds = (path.stat().st_size - 44) / 2 / 16000
        if 8 < seconds < 25:
            with wave.open(str(path), "rb") as wf:
                data = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
            if float(np.abs(data).max()) > 200:
                return path
    raise SystemExit("нет записи со звуком 8–25 секунд")


class LiveWhisper:
    def __init__(self, model, chunk: float = 1.0) -> None:
        self.model = model
        self.chunk = chunk
        self.utt = np.zeros(0, dtype=np.float32)
        self.silence = 0.0
        self.last_decode = 0.0
        self.prev_words: list[str] = []
        self.committed = 0
        self.decode_seconds = 0.0
        self.decodes = 0
        self.log: list[tuple[float, str, float]] = []
        self.began = time.monotonic()

    def _decode(self, audio: np.ndarray) -> str:
        started = time.monotonic()
        segments, _info = self.model.transcribe(
            audio,
            language="ru",
            beam_size=1,
            condition_on_previous_text=False,
            vad_filter=False,
            without_timestamps=True,
        )
        text = " ".join(segment.text.strip() for segment in segments).strip()
        self.decode_seconds += time.monotonic() - started
        self.decodes += 1
        return text

    def _emit(self, words: list[str], up_to: int, now: float) -> None:
        if up_to <= self.committed:
            return
        piece = " ".join(words[self.committed : up_to])
        lag = now - len(self.utt) / SAMPLE_RATE
        self.log.append((now, piece, lag))
        print(f"  {now:5.1f} с | {piece}")
        self.committed = up_to

    def push(self, chunk: np.ndarray, now: float) -> None:
        level = float(np.sqrt(np.mean(np.square(chunk, dtype=np.float64))))
        if level < QUIET_LEVEL:
            self.silence += len(chunk) / SAMPLE_RATE
            if self.silence >= PAUSE_SECONDS:
                self.finish_utterance(now)
                return
        else:
            self.silence = 0.0
        self.utt = np.concatenate([self.utt, chunk])
        if now - self.last_decode >= self.chunk and len(self.utt) > SAMPLE_RATE // 2:
            self.last_decode = now
            words = self._decode(self.utt).split()
            common = 0
            for a, b in zip(self.prev_words, words):
                if a != b:
                    break
                common += 1
            self._emit(words, common, now)
            self.prev_words = words

    def finish_utterance(self, now: float) -> None:
        if not len(self.utt):
            return
        words = self._decode(self.utt).split()
        self._emit(words, len(words), now)
        self.utt = np.zeros(0, dtype=np.float32)
        self.prev_words = []
        self.committed = 0


def main() -> int:
    model_name = sys.argv[1] if len(sys.argv) > 1 else "medium"
    chunk = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0
    path = pick_recording()
    with wave.open(str(path), "rb") as wf:
        audio = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    duration = len(audio) / SAMPLE_RATE
    print(f"запись: {path.name}, {duration:.1f} с; модель {model_name}, int8, cpu; шаг счёта {chunk} с")

    from faster_whisper import WhisperModel

    started = time.monotonic()
    model = WhisperModel(model_name, device="cpu", compute_type="int8", cpu_threads=8)
    print(f"загрузка модели: {time.monotonic() - started:.1f} с")

    live = LiveWhisper(model, chunk=chunk)
    began = time.monotonic()
    fed = 0
    while fed < len(audio):
        piece = audio[fed : fed + STEP]
        fed += len(piece)
        wait = began + fed / SAMPLE_RATE - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        live.push(piece, time.monotonic() - began)
    live.finish_utterance(time.monotonic() - began)

    total = time.monotonic() - began
    print()
    print("текст в поле:")
    print(" ".join(part for _t, part, _lag in live.log))
    print()
    print(
        f"звука {duration:.1f} с, прошло {total:.1f} с, счёт {live.decode_seconds:.1f} с "
        f"(доля от речи {live.decode_seconds / duration:.2f}), чтений {live.decodes}"
    )
    if live.log:
        print(f"первая приписка: {live.log[0][0]:.1f} с, приписок {len(live.log)}")
        worst = max(lag for _t, _part, lag in live.log)
        print(f"худшее отставание приписки от прозвучавшего: {worst:.1f} с")
    return 0


if __name__ == "__main__":
    sys.exit(main())
