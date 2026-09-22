from __future__ import annotations

import json
import time
from pathlib import Path

_STATS_PATH = Path.home() / ".dictation_speed.json"
_EMA_ALPHA = 0.35

# Секунд обработки на 1 секунду аудио. ~0.4 ≈ в 2.5 раза быстрее длины речи.
_DEFAULT_SPEED = {
    "cuda": 0.1,
    "cpu": 0.4,
}


def resolve_cpu_threads(cpu_threads: int) -> int:
    if cpu_threads > 0:
        return max(1, min(10, cpu_threads))
    import os

    return min(10, os.cpu_count() or 4)


def _load_stats() -> dict[str, float]:
    if not _STATS_PATH.exists():
        return {}
    try:
        data = json.loads(_STATS_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {k: float(v) for k, v in data.items() if isinstance(v, (int, float))}
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        pass
    return {}


def _save_stats(stats: dict[str, float]) -> None:
    try:
        _STATS_PATH.write_text(
            json.dumps(stats, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass


def get_speed_factor(device: str) -> float:
    stats = _load_stats()
    if device in stats:
        return stats[device]
    return _DEFAULT_SPEED.get(device, 1.0)


def record_speed_sample(device: str, audio_duration: float, elapsed: float) -> None:
    if audio_duration <= 0 or elapsed <= 0:
        return
    sample = elapsed / audio_duration
    stats = _load_stats()
    prev = stats.get(device)
    if prev is None:
        stats[device] = sample
    else:
        stats[device] = prev * (1 - _EMA_ALPHA) + sample * _EMA_ALPHA
    _save_stats(stats)


def estimate_transcribe_seconds(
    audio_duration: float,
    device: str,
    *,
    vad_filter: bool = True,
    speed_factor: float | None = None,
) -> float:
    factor = speed_factor if speed_factor is not None else get_speed_factor(device)
    effective = _effective_audio_duration(audio_duration, vad_filter)
    return max(1.0, effective * factor + 0.4)


def _effective_audio_duration(audio_duration: float, vad_filter: bool) -> float:
    if not vad_filter or audio_duration <= 12.0:
        return audio_duration
    # Длинная запись в toggle-режиме часто содержит много тишины.
    return 4.0 + (audio_duration - 4.0) * 0.2


def format_transcribe_detail(elapsed: float, estimate: float) -> str:
    elapsed_i = max(0, int(elapsed + 0.5))
    remaining = max(0.0, estimate - elapsed)
    remaining_i = max(0, int(remaining + 0.5))
    if remaining_i > 0:
        return f"{elapsed_i} сек · ~{remaining_i} сек осталось"
    return f"{elapsed_i} сек"


def transcribe_progress(elapsed: float, estimate: float) -> float:
    total = max(estimate, elapsed * 1.15, 1.0)
    return min(0.95, elapsed / total)


class TranscribeTimer:
    def __init__(self, estimate: float) -> None:
        self.estimate = estimate
        self._start = time.monotonic()

    def elapsed(self) -> float:
        return time.monotonic() - self._start

    def detail(self) -> str:
        return format_transcribe_detail(self.elapsed(), self.estimate)

    def progress(self) -> float:
        return transcribe_progress(self.elapsed(), self.estimate)
